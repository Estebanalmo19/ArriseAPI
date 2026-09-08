import uuid
from typing import Any

from fastapi import Request
from psycopg_pool import AsyncConnectionPool

INSERT_DOCUMENT_SQL = """
    INSERT INTO documents (
        document_id, service, document_name, source_system, document_type,
        correlation_id, original_filename, stored_filename, storage_backend,
        storage_key, mime_type, size_bytes, sha256, status
    ) VALUES (
        %(document_id)s, %(service)s, %(document_name)s, %(source_system)s, %(document_type)s,
        %(correlation_id)s, %(original_filename)s, %(stored_filename)s, 'LOCAL',
        %(storage_key)s, %(mime_type)s, %(size_bytes)s, %(sha256)s, 'RECEIVED'
    )
"""

INSERT_REQUEST_LOG_SQL = """
    INSERT INTO request_logs (
        request_id, correlation_id, document_id, method, endpoint, source_system,
        client_ip, http_status, duration_ms, bytes_received, result, error_code
    ) VALUES (
        %(request_id)s, %(correlation_id)s, %(document_id)s, %(method)s, %(endpoint)s, %(source_system)s,
        %(client_ip)s, %(http_status)s, %(duration_ms)s, %(bytes_received)s, %(result)s, %(error_code)s
    )
"""


def create_db_pool(dsn: str) -> AsyncConnectionPool:
    return AsyncConnectionPool(conninfo=dsn, min_size=1, max_size=5, open=False)


def get_db_pool(request: Request) -> AsyncConnectionPool | None:
    return getattr(request.app.state, "db_pool", None)


async def insert_document(pool: AsyncConnectionPool, **fields: Any) -> None:
    async with pool.connection() as conn:
        async with conn.transaction():
            await conn.execute(INSERT_DOCUMENT_SQL, fields)


async def insert_request_log(pool: AsyncConnectionPool | None, *, request_id: uuid.UUID | None = None, **fields: Any) -> None:
    if pool is None:
        return
    params = {"request_id": request_id or uuid.uuid4(), **fields}
    async with pool.connection() as conn:
        async with conn.transaction():
            await conn.execute(INSERT_REQUEST_LOG_SQL, params)
