import os
from pathlib import Path

ALLOWED_ENVIRONMENTS = {"development", "test", "production"}


def get_storage_root() -> Path:
    return Path(os.environ.get("ARRISE_STORAGE_ROOT", "data"))


def get_environment() -> str:
    value = os.environ.get("ARRISE_ENV", "development")
    if value not in ALLOWED_ENVIRONMENTS:
        raise RuntimeError(f"invalid ARRISE_ENV value: {value!r}")
    return value


def get_database_dsn() -> str | None:
    """Returns a database connection string, or None if none is available.

    This is the single seam a future increment will change to retrieve
    production credentials from AWS SSM Parameter Store (SecureString);
    no endpoint or connection-pool code depends on how this value is
    obtained.

    ARRISE_DB_DSN is a local development/test convenience only. It has no
    default, must never be committed anywhere with a real value, and is
    never consulted when ARRISE_ENV=production - in that case this always
    returns None, and AWS SSM retrieval (not implemented in this increment)
    is the only supported production credential path.
    """
    if get_environment() == "production":
        return None
    return os.environ.get("ARRISE_DB_DSN")
