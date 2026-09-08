from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import get_database_dsn
from app.db import create_db_pool
from app.documents import router as documents_router
from app.logging_middleware import request_logging_middleware


@asynccontextmanager
async def lifespan(app: FastAPI):
    dsn = get_database_dsn()
    if dsn is None:
        app.state.db_pool = None
    else:
        pool = create_db_pool(dsn)
        await pool.open()
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
