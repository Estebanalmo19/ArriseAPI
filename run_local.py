"""Local-development-only launcher for ArriseAPI.

On Windows, asyncio's default event loop is ProactorEventLoop, which
Psycopg's async mode cannot run on. `uvicorn.run()` would construct that
incompatible loop itself, so on Windows this launcher instead builds an
explicit `SelectorEventLoop` (backed by `selectors.SelectSelector`) via
`asyncio.Runner(loop_factory=...)` - the non-deprecated way to choose a
loop implementation - and drives `uvicorn.Server.serve()` directly inside
it. Non-Windows platforms use the ordinary `uvicorn.run()`.

Not suitable for production use: host/port are fixed, and no credentials
or environment files are read here.
"""

import asyncio
import selectors
import sys

import uvicorn

from app.main import app

HOST = "127.0.0.1"
PORT = 8000


def _selector_event_loop() -> asyncio.SelectorEventLoop:
    return asyncio.SelectorEventLoop(selectors.SelectSelector())


async def _serve() -> None:
    config = uvicorn.Config(app, host=HOST, port=PORT)
    server = uvicorn.Server(config)
    await server.serve()


def main() -> None:
    if sys.platform == "win32":
        with asyncio.Runner(loop_factory=_selector_event_loop) as runner:
            runner.run(_serve())
    else:
        uvicorn.run(app, host=HOST, port=PORT)


if __name__ == "__main__":
    main()
