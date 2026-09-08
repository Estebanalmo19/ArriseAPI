from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app.logging_middleware as logging_middleware_module
from app.logging_middleware import (
    _map_result,
    _parse_client_ip,
    _parse_content_length,
    request_logging_middleware,
)
from app.main import app


# --- Pure helper functions ---------------------------------------------------


def test_parse_content_length_missing_is_none():
    assert _parse_content_length(None) is None


def test_parse_content_length_invalid_is_none():
    assert _parse_content_length("not-a-number") is None


def test_parse_content_length_negative_is_none():
    assert _parse_content_length("-5") is None


def test_parse_content_length_valid():
    assert _parse_content_length("1234") == 1234


def test_parse_client_ip_valid_ipv4():
    assert _parse_client_ip("127.0.0.1") == "127.0.0.1"


def test_parse_client_ip_valid_ipv6():
    assert _parse_client_ip("::1") == "::1"


def test_parse_client_ip_non_ip_is_none():
    assert _parse_client_ip("testclient") is None


def test_parse_client_ip_none_host_is_none():
    assert _parse_client_ip(None) is None


@pytest.mark.parametrize(
    "status_code,expected",
    [
        (100, "SUCCESS"),
        (200, "SUCCESS"),
        (399, "SUCCESS"),
        (400, "CLIENT_ERROR"),
        (404, "CLIENT_ERROR"),
        (499, "CLIENT_ERROR"),
        (500, "SERVER_ERROR"),
        (599, "SERVER_ERROR"),
    ],
)
def test_map_result(status_code, expected):
    assert _map_result(status_code) == expected


# --- Middleware behavior, tested directly (no ASGI/FastAPI internals) -------


def _make_fake_request(method="GET", path="/health", content_length=None, client_host="127.0.0.1", state=None):
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(db_pool=object())),
        method=method,
        url=SimpleNamespace(path=path),
        headers={"content-length": content_length} if content_length is not None else {},
        client=SimpleNamespace(host=client_host) if client_host is not None else None,
        state=state if state is not None else SimpleNamespace(),
    )


@pytest.mark.anyio
async def test_call_next_exception_logs_server_error_and_reraises(monkeypatch):
    logged = []

    async def fake_insert_request_log(pool, **fields):
        logged.append(fields)

    monkeypatch.setattr(logging_middleware_module, "insert_request_log", fake_insert_request_log)

    fake_request = _make_fake_request(method="POST", path="/api/v1/documents")

    async def failing_call_next(request):
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        await request_logging_middleware(fake_request, failing_call_next)

    assert len(logged) == 1
    assert logged[0]["result"] == "SERVER_ERROR"
    assert logged[0]["http_status"] == 500
    assert logged[0]["method"] == "POST"
    assert logged[0]["endpoint"] == "/api/v1/documents"


@pytest.mark.anyio
async def test_request_log_failure_does_not_alter_successful_response(monkeypatch):
    async def failing_insert_request_log(pool, **fields):
        raise RuntimeError("db down")

    monkeypatch.setattr(logging_middleware_module, "insert_request_log", failing_insert_request_log)

    fake_request = _make_fake_request()
    expected_response = SimpleNamespace(status_code=200)

    async def call_next(request):
        return expected_response

    result = await request_logging_middleware(fake_request, call_next)
    assert result is expected_response


@pytest.mark.anyio
async def test_request_log_failure_does_not_replace_endpoint_exception(monkeypatch):
    async def failing_insert_request_log(pool, **fields):
        raise RuntimeError("db down during exception logging")

    monkeypatch.setattr(logging_middleware_module, "insert_request_log", failing_insert_request_log)

    fake_request = _make_fake_request(method="POST", path="/api/v1/documents")

    async def failing_call_next(request):
        raise ValueError("original endpoint failure")

    with pytest.raises(ValueError, match="original endpoint failure"):
        await request_logging_middleware(fake_request, failing_call_next)


@pytest.mark.anyio
async def test_invalid_content_length_does_not_break_middleware(monkeypatch):
    logged = []

    async def fake_insert_request_log(pool, **fields):
        logged.append(fields)

    monkeypatch.setattr(logging_middleware_module, "insert_request_log", fake_insert_request_log)

    fake_request = _make_fake_request(content_length="not-a-number")
    expected_response = SimpleNamespace(status_code=200)

    async def call_next(request):
        return expected_response

    result = await request_logging_middleware(fake_request, call_next)
    assert result is expected_response
    assert logged[0]["bytes_received"] is None


@pytest.mark.anyio
async def test_negative_content_length_becomes_null(monkeypatch):
    logged = []

    async def fake_insert_request_log(pool, **fields):
        logged.append(fields)

    monkeypatch.setattr(logging_middleware_module, "insert_request_log", fake_insert_request_log)

    fake_request = _make_fake_request(content_length="-100")
    expected_response = SimpleNamespace(status_code=201)

    async def call_next(request):
        return expected_response

    await request_logging_middleware(fake_request, call_next)
    assert logged[0]["bytes_received"] is None


@pytest.mark.anyio
async def test_non_ip_client_host_becomes_null(monkeypatch):
    logged = []

    async def fake_insert_request_log(pool, **fields):
        logged.append(fields)

    monkeypatch.setattr(logging_middleware_module, "insert_request_log", fake_insert_request_log)

    fake_request = _make_fake_request(client_host="testclient")
    expected_response = SimpleNamespace(status_code=200)

    async def call_next(request):
        return expected_response

    await request_logging_middleware(fake_request, call_next)
    assert logged[0]["client_ip"] is None


# --- End-to-end through the real app (middleware only monkeypatched) -------


def test_structural_422_can_be_logged_with_null_fields(monkeypatch):
    """A request that never reaches the endpoint body (a required multipart
    field is entirely absent) must still be logged, with correlation_id,
    source_system and document_id all NULL - the middleware never attempts
    to recover them by reading the multipart body itself."""
    logged = []

    async def fake_insert_request_log(pool, **fields):
        logged.append(fields)

    monkeypatch.setattr(logging_middleware_module, "insert_request_log", fake_insert_request_log)

    client = TestClient(app)
    fields = {
        "service": "doc-processor",
        "source_system": "power_automate",
        "document_type": "invoice",
        "correlation_id": "corr-1",
        # document_name deliberately omitted -> structural 422
    }
    files = {"file": ("invoice.pdf", b"content", "application/pdf")}
    resp = client.post("/api/v1/documents", data=fields, files=files)

    assert resp.status_code == 422
    assert len(logged) == 1
    assert logged[0]["http_status"] == 422
    assert logged[0]["result"] == "CLIENT_ERROR"
    assert logged[0]["correlation_id"] is None
    assert logged[0]["source_system"] is None
    assert logged[0]["document_id"] is None


# --- correlation_id/source_system preserved despite other invalid fields ---


def _post_with_invalid_field(client, field_name, invalid_value):
    fields = {
        "service": "doc-processor",
        "document_name": "Invoice 2024-001",
        "source_system": "power_automate",
        "document_type": "invoice",
        "correlation_id": "corr-valid-1",
    }
    fields[field_name] = invalid_value
    files = {"file": ("invoice.pdf", b"content", "application/pdf")}
    return client.post("/api/v1/documents", data=fields, files=files)


def test_correlation_and_source_logged_when_service_invalid(monkeypatch):
    logged = []

    async def fake_insert_request_log(pool, **fields):
        logged.append(fields)

    monkeypatch.setattr(logging_middleware_module, "insert_request_log", fake_insert_request_log)

    client = TestClient(app)
    resp = _post_with_invalid_field(client, "service", "Invalid Service!")

    assert resp.status_code == 400
    assert len(logged) == 1
    assert logged[0]["correlation_id"] == "corr-valid-1"
    assert logged[0]["source_system"] == "power_automate"


def test_correlation_and_source_logged_when_document_name_invalid(monkeypatch):
    logged = []

    async def fake_insert_request_log(pool, **fields):
        logged.append(fields)

    monkeypatch.setattr(logging_middleware_module, "insert_request_log", fake_insert_request_log)

    client = TestClient(app)
    resp = _post_with_invalid_field(client, "document_name", "   ")

    assert resp.status_code == 400
    assert len(logged) == 1
    assert logged[0]["correlation_id"] == "corr-valid-1"
    assert logged[0]["source_system"] == "power_automate"


def test_correlation_and_source_logged_when_document_type_invalid(monkeypatch):
    logged = []

    async def fake_insert_request_log(pool, **fields):
        logged.append(fields)

    monkeypatch.setattr(logging_middleware_module, "insert_request_log", fake_insert_request_log)

    client = TestClient(app)
    resp = _post_with_invalid_field(client, "document_type", "a" * 65)

    assert resp.status_code == 400
    assert len(logged) == 1
    assert logged[0]["correlation_id"] == "corr-valid-1"
    assert logged[0]["source_system"] == "power_automate"


def test_health_endpoint_is_logged_too(monkeypatch):
    logged = []

    async def fake_insert_request_log(pool, **fields):
        logged.append(fields)

    monkeypatch.setattr(logging_middleware_module, "insert_request_log", fake_insert_request_log)

    client = TestClient(app)
    resp = client.get("/health")

    assert resp.status_code == 200
    assert len(logged) == 1
    assert logged[0]["endpoint"] == "/health"
    assert logged[0]["result"] == "SUCCESS"
