import asyncio
import selectors
from pathlib import Path

import run_local

# These tests never bind a real socket or start a real server: uvicorn.Server
# and uvicorn.run are always monkeypatched with fakes before run_local.main()
# is invoked.

SOURCE = Path(run_local.__file__).read_text()


class _RecordingFakeServer:
    """Stands in for uvicorn.Server. Records the config it was built with
    and the event loop/selector serve() actually ran on, without opening a
    socket. The selector type is captured while the loop is still open -
    closing an event loop discards its `_selector` reference."""

    last_instance = None

    def __init__(self, config):
        self.config = config
        self.served = False
        self.loop_type = None
        self.selector_type = None
        _RecordingFakeServer.last_instance = self

    async def serve(self):
        self.served = True
        loop = asyncio.get_running_loop()
        self.loop_type = type(loop)
        self.selector_type = type(loop._selector)


# --- Source-level guarantees ------------------------------------------------


def test_no_deprecated_event_loop_policy_api_used():
    assert "set_event_loop_policy" not in SOURCE


def test_no_windows_selector_event_loop_policy_used():
    assert "WindowsSelectorEventLoopPolicy" not in SOURCE


def test_uses_asyncio_runner():
    assert "asyncio.Runner(" in SOURCE


def test_calls_server_serve_directly():
    assert "server.serve()" in SOURCE


# --- Selector event loop factory --------------------------------------------


def test_selector_event_loop_factory_returns_select_selector_loop():
    loop = run_local._selector_event_loop()
    try:
        assert isinstance(loop, asyncio.SelectorEventLoop)
        assert isinstance(loop._selector, selectors.SelectSelector)
    finally:
        loop.close()


# --- Windows branch: Runner + explicit selector loop + direct serve() -----


def test_windows_branch_runs_server_on_explicit_selector_event_loop(monkeypatch):
    monkeypatch.setattr(run_local.sys, "platform", "win32")
    monkeypatch.setattr(run_local.uvicorn, "Server", _RecordingFakeServer)

    run_local.main()

    instance = _RecordingFakeServer.last_instance
    assert instance is not None
    assert instance.served is True
    assert issubclass(instance.loop_type, asyncio.SelectorEventLoop)
    assert instance.selector_type is selectors.SelectSelector
    assert instance.config.host == run_local.HOST
    assert instance.config.port == run_local.PORT


def test_windows_branch_does_not_call_uvicorn_run(monkeypatch):
    monkeypatch.setattr(run_local.sys, "platform", "win32")
    monkeypatch.setattr(run_local.uvicorn, "Server", _RecordingFakeServer)

    def fail_if_called(*args, **kwargs):
        raise AssertionError("uvicorn.run() must not be used on Windows")

    monkeypatch.setattr(run_local.uvicorn, "run", fail_if_called)

    run_local.main()


# --- Non-Windows branch: ordinary uvicorn.run() -----------------------------


def test_non_windows_branch_uses_uvicorn_run(monkeypatch):
    monkeypatch.setattr(run_local.sys, "platform", "linux")
    calls = []

    def fake_run(app_arg, host=None, port=None):
        calls.append((app_arg, host, port))

    monkeypatch.setattr(run_local.uvicorn, "run", fake_run)

    run_local.main()

    assert calls == [(run_local.app, run_local.HOST, run_local.PORT)]


# --- No credentials or environment files ------------------------------------


def test_no_env_file_reading_or_credential_material():
    assert "os.environ" not in SOURCE
    assert ".env" not in SOURCE
    assert "password" not in SOURCE.lower()
