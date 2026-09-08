import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app.documents as documents_module
from app.db import get_db_pool
from app.documents import ingest_document
from app.main import app

ENDPOINT = "/api/v1/documents"

VALID_FIELDS = {
    "service": "doc-processor",
    "document_name": "Invoice 2024-001",
    "source_system": "power_automate",
    "document_type": "invoice",
    "correlation_id": "corr-12345",
}


def _post(client, fields, filename, content, content_type):
    files = {"file": (filename, content, content_type)}
    return client.post(ENDPOINT, data=dict(fields), files=files)


class _FakeUploadFile:
    def __init__(self, filename, content, content_type):
        self.filename = filename
        self.content_type = content_type
        self._content = content
        self._pos = 0

    async def read(self, size):
        if self._pos >= len(self._content):
            return b""
        chunk = self._content[self._pos : self._pos + size]
        self._pos += len(chunk)
        return chunk

    async def close(self):
        pass


# --- Insert content correctness ---------------------------------------------


def test_successful_upload_inserts_correct_fields(client, storage_root, db_pool):
    resp = _post(client, VALID_FIELDS, "invoice.pdf", b"content bytes", "application/pdf")
    assert resp.status_code == 201
    body = resp.json()

    assert len(db_pool) == 1
    inserted = db_pool[0]
    assert inserted["document_id"] == uuid.UUID(body["document_id"])
    assert inserted["service"] == "doc-processor"
    assert inserted["storage_key"] == f"received/doc-processor/{body['stored_filename']}"
    assert inserted["mime_type"] == "application/pdf"
    assert inserted["sha256"] == body["sha256"]
    assert inserted["size_bytes"] == body["size_bytes"]


def test_storage_key_is_relative_and_matches_stored_file(client, storage_root, db_pool):
    resp = _post(client, VALID_FIELDS, "invoice.pdf", b"content bytes", "application/pdf")
    body = resp.json()
    storage_key = db_pool[0]["storage_key"]

    assert not storage_key.startswith("/")
    assert (storage_root / storage_key).exists()
    assert (storage_root / storage_key).name == body["stored_filename"]


def test_no_absolute_path_is_recorded(client, storage_root, db_pool):
    resp = _post(client, VALID_FIELDS, "invoice.pdf", b"content bytes", "application/pdf")
    assert resp.status_code == 201
    storage_key = db_pool[0]["storage_key"]

    assert not Path(storage_key).is_absolute()
    assert str(storage_root.resolve()) not in storage_key


# --- DB insert failure -> cleanup --------------------------------------------


def test_db_insert_failure_deletes_final_file_and_leaves_no_part_file(client, storage_root, monkeypatch):
    async def failing_insert(pool, **fields):
        raise RuntimeError("simulated database failure")

    monkeypatch.setattr(documents_module, "insert_document", failing_insert)

    resp = _post(client, VALID_FIELDS, "invoice.pdf", b"content bytes", "application/pdf")
    assert resp.status_code == 500

    service_dir = storage_root / "received" / VALID_FIELDS["service"]
    if service_dir.exists():
        assert list(service_dir.iterdir()) == []


# --- request.state.document_id timing ----------------------------------------


@pytest.mark.anyio
async def test_document_id_not_assigned_when_insert_fails(tmp_path, monkeypatch):
    request_state = SimpleNamespace()
    fake_request = SimpleNamespace(state=request_state)

    async def failing_insert(pool, **fields):
        raise RuntimeError("simulated database failure")

    monkeypatch.setattr(documents_module, "insert_document", failing_insert)

    fake_file = _FakeUploadFile("invoice.pdf", b"content", "application/pdf")

    with pytest.raises(Exception):
        await ingest_document(
            request=fake_request,
            service="doc-processor",
            document_name="Invoice",
            source_system="power_automate",
            document_type="invoice",
            correlation_id="corr-1",
            file=fake_file,
            storage_root=tmp_path,
            pool=object(),
        )

    assert not hasattr(request_state, "document_id")


@pytest.mark.anyio
async def test_document_id_assigned_only_after_successful_commit(tmp_path, monkeypatch):
    request_state = SimpleNamespace()
    fake_request = SimpleNamespace(state=request_state)

    async def succeeding_insert(pool, **fields):
        return None

    monkeypatch.setattr(documents_module, "insert_document", succeeding_insert)

    fake_file = _FakeUploadFile("invoice.pdf", b"content", "application/pdf")

    result = await ingest_document(
        request=fake_request,
        service="doc-processor",
        document_name="Invoice",
        source_system="power_automate",
        document_type="invoice",
        correlation_id="corr-1",
        file=fake_file,
        storage_root=tmp_path,
        pool=object(),
    )

    assert hasattr(request_state, "document_id")
    assert request_state.document_id == result.document_id


# --- Missing database configuration -------------------------------------------


def test_missing_db_config_returns_503_and_leaves_no_files(storage_root):
    app.dependency_overrides[get_db_pool] = lambda: None
    try:
        client = TestClient(app)
        resp = _post(client, VALID_FIELDS, "invoice.pdf", b"content", "application/pdf")
        assert resp.status_code == 503
        assert "ARRISE_DB_DSN" not in resp.text
        assert str(storage_root) not in resp.text
        received_dir = storage_root / "received"
        if received_dir.exists():
            assert list(received_dir.rglob("*")) == []
    finally:
        app.dependency_overrides.pop(get_db_pool, None)


def test_health_still_works_without_db_configuration():
    app.dependency_overrides[get_db_pool] = lambda: None
    try:
        client = TestClient(app)
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}
    finally:
        app.dependency_overrides.pop(get_db_pool, None)
