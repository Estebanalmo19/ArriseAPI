import os
from pathlib import Path


def get_storage_root() -> Path:
    return Path(os.environ.get("ARRISE_STORAGE_ROOT", "data"))
