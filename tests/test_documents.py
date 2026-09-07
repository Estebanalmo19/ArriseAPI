import hashlib
import uuid

import pytest
from fastapi import HTTPException

import app.documents as documents_module
from app.documents import CHUNK_SIZE, _stream_to_temp_file, ingest_document

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


# --- Successful uploads ---------------------------------------------------


def test_pdf_upload_succeeds(client, storage_root):
    resp = _post(client, VALID_FIELDS, "invoice.pdf", b"%PDF-1.4 fake pdf content", "application/pdf")
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "RECEIVED"
    assert body["stored_filename"] == f"{body['document_id']}.pdf"
    assert (storage_root / "received" / "doc-processor" / body["stored_filename"]).exists()


def test_docx_upload_succeeds(client, storage_root):
    resp = _post(
        client, VALID_FIELDS, "report.docx", b"fake docx content",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    assert resp.status_code == 201
    assert resp.json()["stored_filename"].endswith(".docx")


def test_xlsx_upload_succeeds(client, storage_root):
    resp = _post(
        client, VALID_FIELDS, "sheet.xlsx", b"fake xlsx content",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    assert resp.status_code == 201
    assert resp.json()["stored_filename"].endswith(".xlsx")


def test_csv_upload_succeeds(client, storage_root):
    resp = _post(client, VALID_FIELDS, "data.csv", b"a,b,c\n1,2,3", "text/csv")
    assert resp.status_code == 201
    assert resp.json()["stored_filename"].endswith(".csv")


def test_octet_stream_accepted_for_approved_extension(client, storage_root):
    resp = _post(client, VALID_FIELDS, "invoice.pdf", b"%PDF-1.4 fake", "application/octet-stream")
    assert resp.status_code == 201


@pytest.mark.parametrize(
    "content_type",
    ["text/csv", "application/csv", "application/vnd.ms-excel", "application/octet-stream"],
)
def test_csv_all_approved_mime_types_accepted(client, storage_root, content_type):
    resp = _post(client, VALID_FIELDS, "data.csv", b"a,b,c\n1,2,3", content_type)
    assert resp.status_code == 201


# --- 422 vs 400 distinction -------------------------------------------------


def test_missing_required_metadata_field_returns_422(client):
    """The 'document_name' form field itself is entirely absent from the request."""
    fields = dict(VALID_FIELDS)
    del fields["document_name"]
    files = {"file": ("invoice.pdf", b"content", "application/pdf")}
    resp = client.post(ENDPOINT, data=fields, files=files)
    assert resp.status_code == 422


def test_missing_file_part_returns_422(client):
    """The 'file' form field itself is entirely absent from the request."""
    resp = client.post(ENDPOINT, data=VALID_FIELDS)
    assert resp.status_code == 422


def test_whitespace_only_metadata_returns_400(client):
    fields = dict(VALID_FIELDS)
    fields["document_name"] = "   "
    resp = _post(client, fields, "invoice.pdf", b"content", "application/pdf")
    assert resp.status_code == 400


def test_empty_string_filename_returns_422(client):
    """An empty-string filename is not treated as a file part by Starlette's multipart
    parser (python-multipart only constructs an UploadFile when filename is truthy) -
    FastAPI rejects the resulting plain-string value against the UploadFile type before
    our code runs, so this is a framework-level structural 422, not our business-rule 400."""
    resp = _post(client, VALID_FIELDS, "", b"content", "application/pdf")
    assert resp.status_code == 422


def test_whitespace_only_filename_on_supplied_file_returns_400(client):
    """A whitespace-only filename is truthy, so Starlette does construct an UploadFile
    and hand it to our code, where the blank-after-strip check returns 400."""
    resp = _post(client, VALID_FIELDS, "   ", b"content", "application/pdf")
    assert resp.status_code == 400


# --- Field-format and length validation ------------------------------------


def test_invalid_service_format_returns_400(client):
    fields = dict(VALID_FIELDS)
    fields["service"] = "Invalid Service!"
    resp = _post(client, fields, "invoice.pdf", b"content", "application/pdf")
    assert resp.status_code == 400


def test_service_too_long_returns_400(client):
    fields = dict(VALID_FIELDS)
    fields["service"] = "a" * 65
    resp = _post(client, fields, "invoice.pdf", b"content", "application/pdf")
    assert resp.status_code == 400


def test_document_name_too_long_returns_400(client):
    fields = dict(VALID_FIELDS)
    fields["document_name"] = "a" * 256
    resp = _post(client, fields, "invoice.pdf", b"content", "application/pdf")
    assert resp.status_code == 400


def test_source_system_too_long_returns_400(client):
    fields = dict(VALID_FIELDS)
    fields["source_system"] = "a" * 65
    resp = _post(client, fields, "invoice.pdf", b"content", "application/pdf")
    assert resp.status_code == 400


def test_document_type_too_long_returns_400(client):
    fields = dict(VALID_FIELDS)
    fields["document_type"] = "a" * 65
    resp = _post(client, fields, "invoice.pdf", b"content", "application/pdf")
    assert resp.status_code == 400


def test_correlation_id_too_long_returns_400(client):
    fields = dict(VALID_FIELDS)
    fields["correlation_id"] = "a" * 129
    resp = _post(client, fields, "invoice.pdf", b"content", "application/pdf")
    assert resp.status_code == 400


def test_unsupported_extension_returns_400(client):
    resp = _post(client, VALID_FIELDS, "malware.exe", b"content", "application/octet-stream")
    assert resp.status_code == 400


def test_mime_mismatch_returns_400(client):
    resp = _post(client, VALID_FIELDS, "invoice.pdf", b"content", "text/plain")
    assert resp.status_code == 400


def test_empty_file_returns_400_and_leaves_no_temp_file(client, storage_root):
    resp = _post(client, VALID_FIELDS, "invoice.pdf", b"", "application/pdf")
    assert resp.status_code == 400
    service_dir = storage_root / "received" / VALID_FIELDS["service"]
    if service_dir.exists():
        assert list(service_dir.glob("*.part")) == []


def test_path_traversal_in_service_rejected(client):
    fields = dict(VALID_FIELDS)
    fields["service"] = "../../etc"
    resp = _post(client, fields, "invoice.pdf", b"content", "application/pdf")
    assert resp.status_code == 400


# --- Storage correctness -----------------------------------------------------


def test_stored_filename_uses_uuid_not_original(client, storage_root):
    resp = _post(client, VALID_FIELDS, "secret-original-name.pdf", b"content", "application/pdf")
    body = resp.json()
    assert body["original_filename"] == "secret-original-name.pdf"
    assert body["stored_filename"] != "secret-original-name.pdf"
    uuid.UUID(body["document_id"])  # raises if not a valid UUID
    assert body["stored_filename"] == f"{body['document_id']}.pdf"


def test_sha256_and_size_are_correct(client, storage_root):
    content = b"a" * 5000
    resp = _post(client, VALID_FIELDS, "invoice.pdf", content, "application/pdf")
    body = resp.json()
    assert body["size_bytes"] == len(content)
    assert body["sha256"] == hashlib.sha256(content).hexdigest()
    assert len(body["sha256"]) == 64


# --- Size limit (413) --------------------------------------------------------


def test_file_exceeding_max_size_returns_413(client, storage_root, monkeypatch):
    """Uses a reduced, monkeypatched limit instead of a real 100 MiB payload."""
    monkeypatch.setattr(documents_module, "MAX_FILE_SIZE_BYTES", 10 * 1024)
    content = b"x" * (11 * 1024)
    resp = _post(client, VALID_FIELDS, "invoice.pdf", content, "application/pdf")
    assert resp.status_code == 413


@pytest.mark.anyio
async def test_streaming_rejects_over_limit_without_full_buffering(tmp_path):
    """Isolated unit test of the streaming limit: bounded memory, no 100 MiB allocation."""

    class FakeUploadFile:
        def __init__(self, total_size, chunk_size):
            self._remaining = total_size
            self._chunk_size = chunk_size

        async def read(self, size):
            if self._remaining <= 0:
                return b""
            n = min(size, self._remaining, self._chunk_size)
            self._remaining -= n
            return b"x" * n

    tmp_file = tmp_path / "big.part"
    fake_upload = FakeUploadFile(total_size=50 * 1024, chunk_size=CHUNK_SIZE)

    with pytest.raises(HTTPException) as exc_info:
        await _stream_to_temp_file(fake_upload, tmp_file, max_size=10 * 1024)

    assert exc_info.value.status_code == 413
    assert not tmp_file.exists()


# --- Simulated filesystem failure (500, no internal detail leaked) ---------


def test_write_failure_returns_500_without_internal_details(client, storage_root, monkeypatch):
    class _FailingWriter:
        def __init__(self, real_file):
            self._real_file = real_file

        def write(self, data):
            raise OSError("simulated disk failure")

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            self._real_file.close()
            return False

    def failing_open(path):
        real_file = open(path, "wb")
        return _FailingWriter(real_file)

    monkeypatch.setattr(documents_module, "_open_for_write", failing_open)

    resp = _post(client, VALID_FIELDS, "invoice.pdf", b"content bytes", "application/pdf")

    assert resp.status_code == 500
    body_text = resp.text
    assert str(storage_root) not in body_text
    assert "OSError" not in body_text
    assert "Traceback" not in body_text
    assert "simulated disk failure" not in body_text

    service_dir = storage_root / "received" / VALID_FIELDS["service"]
    if service_dir.exists():
        assert list(service_dir.glob("*.part")) == []


# --- UploadFile closure, and missing Content-Type, tested at the function level ---
# These call app.documents.ingest_document directly with a minimal fake UploadFile,
# bypassing the ASGI/HTTP layer entirely so the test is not coupled to FastAPI
# internals (Form/File/Depends defaults are simply ignored when arguments are
# supplied explicitly, as any plain Python coroutine call would).


class _FakeUploadFile:
    def __init__(self, filename, content, content_type):
        self.filename = filename
        self.content_type = content_type
        self._content = content
        self._pos = 0
        self.closed = False

    async def read(self, size):
        if self._pos >= len(self._content):
            return b""
        chunk = self._content[self._pos:self._pos + size]
        self._pos += len(chunk)
        return chunk

    async def close(self):
        self.closed = True


@pytest.mark.anyio
async def test_upload_file_closed_on_success(tmp_path):
    fake = _FakeUploadFile("data.csv", b"a,b\n1,2", "text/csv")
    result = await ingest_document(
        service="doc-processor",
        document_name="Invoice",
        source_system="power_automate",
        document_type="invoice",
        correlation_id="corr-1",
        file=fake,
        storage_root=tmp_path,
    )
    assert result.status == "RECEIVED"
    assert fake.closed is True


@pytest.mark.anyio
async def test_upload_file_closed_on_validation_failure(tmp_path):
    fake = _FakeUploadFile("data.csv", b"a,b\n1,2", "text/csv")
    with pytest.raises(HTTPException):
        await ingest_document(
            service="Invalid Service!",
            document_name="Invoice",
            source_system="power_automate",
            document_type="invoice",
            correlation_id="corr-1",
            file=fake,
            storage_root=tmp_path,
        )
    assert fake.closed is True


@pytest.mark.anyio
async def test_missing_content_type_rejected(tmp_path):
    fake = _FakeUploadFile("invoice.pdf", b"content", content_type=None)
    with pytest.raises(HTTPException) as exc_info:
        await ingest_document(
            service="doc-processor",
            document_name="Invoice",
            source_system="power_automate",
            document_type="invoice",
            correlation_id="corr-1",
            file=fake,
            storage_root=tmp_path,
        )
    assert exc_info.value.status_code == 400
    assert fake.closed is True
