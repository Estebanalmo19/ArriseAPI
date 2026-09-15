import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import get_database_dsn
from app.db import DB_POOL_READY_TIMEOUT, create_db_pool
from app.documents import router as documents_router
from app.logging_middleware import request_logging_middleware

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    dsn = get_database_dsn()
    if dsn is None:
        app.state.db_pool = None
    else:
        pool = create_db_pool(dsn)
        try:
            await pool.open()
            await pool.wait(timeout=DB_POOL_READY_TIMEOUT)
        except Exception:
            # The original readiness failure is what the caller needs to
            # see; a failure while cleaning up must never replace it.
            try:
                await pool.close()
            except Exception:
                logger.exception("failed to close database pool after a startup failure")
            raise
        app.state.db_pool = pool
    yield
    if app.state.db_pool is not None:
        await app.state.db_pool.close()


app = FastAPI(
    title="ArriseAPI",
    version="0.1.0",
    description="API for receiving documents sent by Microsoft Power Automate.",
    lifespan=lifespan,
)

app.add_middleware(BaseHTTPMiddleware, dispatch=request_logging_middleware)

app.include_router(documents_router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
