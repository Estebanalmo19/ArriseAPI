from types import SimpleNamespace

import pytest
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
    whether open()/wait()/close() were awaited so the test can assert on
    lifecycle without touching anything real."""

    def __init__(self):
        self.opened = False
        self.closed = False
        self.waited_timeout = None

    async def open(self):
        self.opened = True

    async def wait(self, timeout):
        self.waited_timeout = timeout

    async def close(self):
        self.closed = True


class _FailingReadinessPool(_FakeAsyncPool):
    """A fake pool whose wait() never becomes ready - simulates PostgreSQL
    being unreachable/misconfigured."""

    async def wait(self, timeout):
        self.waited_timeout = timeout
        raise TimeoutError("pool initialization incomplete after 10 sec")


class _FailingReadinessAndClosePool(_FailingReadinessPool):
    """Also fails while being closed during cleanup, to prove the cleanup
    failure never replaces the original readiness failure."""

    async def close(self):
        self.closed = True
        raise RuntimeError("cleanup failure - must never surface to the caller")


async def _noop_insert_request_log(pool, **fields):
    return None


def _fake_app():
    return SimpleNamespace(state=SimpleNamespace())


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


# --- Fail-fast pool readiness at startup --------------------------------


@pytest.mark.anyio
async def test_lifespan_opens_the_pool(monkeypatch):
    fake_pool = _FakeAsyncPool()
    monkeypatch.setattr(main_module, "get_database_dsn", lambda: FAKE_DSN)
    monkeypatch.setattr(main_module, "create_db_pool", lambda dsn: fake_pool)

    assert fake_pool.opened is False
    async with main_module.lifespan(_fake_app()):
        assert fake_pool.opened is True


@pytest.mark.anyio
async def test_lifespan_waits_for_pool_readiness_before_yielding(monkeypatch):
    events = []

    class _TrackingFakePool(_FakeAsyncPool):
        async def open(self):
            events.append("open")
            await super().open()

        async def wait(self, timeout):
            events.append("wait")
            await super().wait(timeout)

    fake_pool = _TrackingFakePool()
    monkeypatch.setattr(main_module, "get_database_dsn", lambda: FAKE_DSN)
    monkeypatch.setattr(main_module, "create_db_pool", lambda dsn: fake_pool)

    async with main_module.lifespan(_fake_app()):
        events.append("yielded")

    assert events == ["open", "wait", "yielded"]


@pytest.mark.anyio
async def test_pool_wait_uses_a_bounded_timeout(monkeypatch):
    fake_pool = _FakeAsyncPool()
    monkeypatch.setattr(main_module, "get_database_dsn", lambda: FAKE_DSN)
    monkeypatch.setattr(main_module, "create_db_pool", lambda dsn: fake_pool)

    async with main_module.lifespan(_fake_app()):
        pass

    assert fake_pool.waited_timeout is not None
    assert 0 < fake_pool.waited_timeout <= 10


@pytest.mark.anyio
async def test_readiness_failure_closes_pool_and_prevents_startup(monkeypatch):
    fake_pool = _FailingReadinessPool()
    monkeypatch.setattr(main_module, "get_database_dsn", lambda: FAKE_DSN)
    monkeypatch.setattr(main_module, "create_db_pool", lambda dsn: fake_pool)

    yielded = False
    with pytest.raises(TimeoutError) as exc_info:
        async with main_module.lifespan(_fake_app()):
            yielded = True

    assert yielded is False
    assert fake_pool.closed is True
    # No credential or connection-string fragment may leak through the
    # startup failure.
    assert "fake_password" not in str(exc_info.value)
    assert FAKE_DSN not in str(exc_info.value)


@pytest.mark.anyio
async def test_readiness_exception_is_not_replaced_by_cleanup_exception(monkeypatch):
    fake_pool = _FailingReadinessAndClosePool()
    monkeypatch.setattr(main_module, "get_database_dsn", lambda: FAKE_DSN)
    monkeypatch.setattr(main_module, "create_db_pool", lambda dsn: fake_pool)

    with pytest.raises(TimeoutError, match="pool initialization incomplete"):
        async with main_module.lifespan(_fake_app()):
            pass

    assert fake_pool.closed is True


def test_startup_failure_fails_fastapi_uvicorn_startup(monkeypatch):
    fake_pool = _FailingReadinessPool()
    monkeypatch.setattr(main_module, "get_database_dsn", lambda: FAKE_DSN)
    monkeypatch.setattr(main_module, "create_db_pool", lambda dsn: fake_pool)
    monkeypatch.setattr(logging_middleware_module, "insert_request_log", _noop_insert_request_log)

    try:
        with pytest.raises(TimeoutError):
            with TestClient(app):
                pytest.fail("TestClient must not start successfully")
    finally:
        app.state.db_pool = None
