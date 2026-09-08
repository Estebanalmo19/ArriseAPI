from fastapi.testclient import TestClient

import app.logging_middleware as logging_middleware_module
import app.main as main_module
from app.main import app

# Clearly fake - never a real host/credential. create_db_pool is monkeypatched
# below to ignore this value entirely and return a fake in-memory pool, so no
# socket or database connection is ever attempted regardless of its content.
FAKE_DSN = "postgresql://fake_user:fake_password@fake-host:5432/arrise_api_fake"


class _FakeAsyncPool:
    """A completely fake async pool: no network code, no psycopg. Records
    whether open()/close() were awaited so the test can assert on lifecycle
    without touching anything real."""

    def __init__(self):
        self.opened = False
        self.closed = False

    async def open(self):
        self.opened = True

    async def close(self):
        self.closed = True


async def _noop_insert_request_log(pool, **fields):
    return None


def test_lifespan_creates_opens_and_closes_fake_pool(monkeypatch):
    fake_pool = _FakeAsyncPool()
    created_with_dsn = []

    def fake_create_db_pool(dsn):
        created_with_dsn.append(dsn)
        return fake_pool

    monkeypatch.setattr(main_module, "get_database_dsn", lambda: FAKE_DSN)
    monkeypatch.setattr(main_module, "create_db_pool", fake_create_db_pool)
    # Request logging is disabled via the existing insert_request_log seam
    # (the same one used throughout tests/test_request_logging.py) so this
    # test cannot reach any database code, real or fake-pool-shaped.
    monkeypatch.setattr(logging_middleware_module, "insert_request_log", _noop_insert_request_log)

    assert fake_pool.opened is False
    try:
        with TestClient(app) as client:
            assert created_with_dsn == [FAKE_DSN]
            assert app.state.db_pool is fake_pool
            assert fake_pool.opened is True
            assert fake_pool.closed is False

            resp = client.get("/health")
            assert resp.status_code == 200
            assert resp.json() == {"status": "ok"}

        assert fake_pool.closed is True
    finally:
        app.state.db_pool = None


def test_lifespan_with_no_db_config_creates_no_pool(monkeypatch):
    def fail_if_called(dsn):
        raise AssertionError("create_db_pool must not be called when no DSN is configured")

    monkeypatch.setattr(main_module, "get_database_dsn", lambda: None)
    monkeypatch.setattr(main_module, "create_db_pool", fail_if_called)
    monkeypatch.setattr(logging_middleware_module, "insert_request_log", _noop_insert_request_log)

    try:
        with TestClient(app) as client:
            assert app.state.db_pool is None

            resp = client.get("/health")
            assert resp.status_code == 200
            assert resp.json() == {"status": "ok"}
    finally:
        app.state.db_pool = None
