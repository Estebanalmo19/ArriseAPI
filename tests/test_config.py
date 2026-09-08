from pathlib import Path

import pytest

from app.config import get_database_dsn, get_environment, get_storage_root

# Clearly fake - never a real host/credential. Used only to prove this value
# is never returned or leaked when ARRISE_ENV=production.
FAKE_DEV_DSN = "postgresql://fake_user:fake_password@fake-host:5432/arrise_api_fake"
FAKE_TEST_DSN = "postgresql://fake_test_user:fake_test_password@fake-test-host:5432/arrise_api_test_fake"


def _clear_arrise_env(monkeypatch):
    monkeypatch.delenv("ARRISE_ENV", raising=False)
    monkeypatch.delenv("ARRISE_DB_DSN", raising=False)


# --- production always refuses ARRISE_DB_DSN --------------------------------


def test_production_env_returns_none_even_with_dsn_set(monkeypatch, capsys):
    _clear_arrise_env(monkeypatch)
    monkeypatch.setenv("ARRISE_ENV", "production")
    monkeypatch.setenv("ARRISE_DB_DSN", FAKE_DEV_DSN)

    result = get_database_dsn()

    assert result is None
    assert result != FAKE_DEV_DSN
    captured = capsys.readouterr()
    assert FAKE_DEV_DSN not in captured.out
    assert FAKE_DEV_DSN not in captured.err


def test_production_env_dsn_never_appears_in_a_raised_exception(monkeypatch):
    """Even if get_database_dsn() were to raise for an unrelated reason, the
    DSN must never be part of that exception's text."""
    _clear_arrise_env(monkeypatch)
    monkeypatch.setenv("ARRISE_ENV", "production")
    monkeypatch.setenv("ARRISE_DB_DSN", FAKE_DEV_DSN)

    try:
        result = get_database_dsn()
    except Exception as exc:  # pragma: no cover - defensive, not expected
        assert FAKE_DEV_DSN not in str(exc)
        raise
    else:
        assert result is None


# --- development ------------------------------------------------------------


def test_development_env_without_dsn_returns_none(monkeypatch):
    _clear_arrise_env(monkeypatch)
    monkeypatch.setenv("ARRISE_ENV", "development")

    assert get_database_dsn() is None


def test_development_env_with_dsn_returns_the_configured_dsn(monkeypatch):
    _clear_arrise_env(monkeypatch)
    monkeypatch.setenv("ARRISE_ENV", "development")
    monkeypatch.setenv("ARRISE_DB_DSN", FAKE_DEV_DSN)

    assert get_database_dsn() == FAKE_DEV_DSN


# --- test environment ---------------------------------------------------------


def test_test_env_with_dsn_returns_the_configured_dsn(monkeypatch):
    _clear_arrise_env(monkeypatch)
    monkeypatch.setenv("ARRISE_ENV", "test")
    monkeypatch.setenv("ARRISE_DB_DSN", FAKE_TEST_DSN)

    assert get_database_dsn() == FAKE_TEST_DSN


# --- invalid ARRISE_ENV -------------------------------------------------------


def test_invalid_environment_raises_runtime_error(monkeypatch):
    _clear_arrise_env(monkeypatch)
    monkeypatch.setenv("ARRISE_ENV", "staging")

    with pytest.raises(RuntimeError) as exc_info:
        get_environment()

    assert "staging" in str(exc_info.value)


def test_invalid_environment_error_does_not_expose_dsn(monkeypatch):
    _clear_arrise_env(monkeypatch)
    monkeypatch.setenv("ARRISE_ENV", "staging")
    monkeypatch.setenv("ARRISE_DB_DSN", FAKE_DEV_DSN)

    with pytest.raises(RuntimeError) as exc_info:
        get_environment()

    assert "staging" in str(exc_info.value)
    assert FAKE_DEV_DSN not in str(exc_info.value)


def test_invalid_environment_also_makes_get_database_dsn_raise_without_leaking(monkeypatch):
    _clear_arrise_env(monkeypatch)
    monkeypatch.setenv("ARRISE_ENV", "staging")
    monkeypatch.setenv("ARRISE_DB_DSN", FAKE_DEV_DSN)

    with pytest.raises(RuntimeError) as exc_info:
        get_database_dsn()

    assert FAKE_DEV_DSN not in str(exc_info.value)


# --- storage root -------------------------------------------------------------


def test_storage_root_defaults_to_data_when_unset(monkeypatch):
    monkeypatch.delenv("ARRISE_STORAGE_ROOT", raising=False)

    assert get_storage_root() == Path("data")


def test_storage_root_returns_configured_path_without_creating_it(monkeypatch, tmp_path):
    custom_root = tmp_path / "custom_storage_root"
    monkeypatch.setenv("ARRISE_STORAGE_ROOT", str(custom_root))

    result = get_storage_root()

    assert result == custom_root
    assert not custom_root.exists()
