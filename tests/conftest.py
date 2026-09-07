import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import get_storage_root
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


@pytest.fixture()
def client(storage_root: Path) -> TestClient:
    return TestClient(app)
