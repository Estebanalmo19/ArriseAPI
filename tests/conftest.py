import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import get_storage_root
from app.db import get_db_pool
from app.main import app


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture()
def storage_root(tmp_path: Path) -> Path:
    root = tmp_path / "storage"
    root.mkdir()
    app.dependency_overrides[get_storage_root] = lambda: root
    yield root
    app.dependency_overrides.pop(get_storage_root, None)
    shutil.rmtree(root, ignore_errors=True)


class _FakePool:
    """Stand-in for a real AsyncConnectionPool. Its methods are never
    actually invoked because insert_document is monkeypatched below -
    this object only needs to be non-None so `pool is None` (the
    503-triggering check) is false."""


@pytest.fixture()
def db_pool(monkeypatch):
    """Overrides get_db_pool with a working (successful) fake, and stubs
    insert_document so upload tests exercise the normal success path
    without ever touching a real PostgreSQL instance. Returns the list of
    recorded insert calls for tests that want to assert on them."""
    fake_pool = _FakePool()
    app.dependency_overrides[get_db_pool] = lambda: fake_pool
    calls: list[dict] = []

    async def fake_insert_document(pool, **fields):
        calls.append(fields)

    monkeypatch.setattr("app.documents.insert_document", fake_insert_document)
    yield calls
    app.dependency_overrides.pop(get_db_pool, None)


@pytest.fixture()
def client(storage_root: Path, db_pool) -> TestClient:
    return TestClient(app)
