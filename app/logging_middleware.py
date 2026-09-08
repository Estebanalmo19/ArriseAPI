import logging
import time
import uuid
from ipaddress import ip_address
from typing import Awaitable, Callable

from fastapi import Request, Response

from app.db import insert_request_log

logger = logging.getLogger(__name__)

# X-Forwarded-For is deliberately not consulted in this increment - the app
# is not yet behind a trusted reverse proxy. Trusted-proxy IP resolution
# (Nginx setting a verified header, Uvicorn configured to trust it) is a
# later, deployment-stage increment.


def _parse_content_length(raw: str | None) -> int | None:
    if raw is None:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    if value < 0:
        return None
    return value


def _parse_client_ip(host: str | None) -> str | None:
    if not host:
        return None
    try:
        ip_address(host)
    except ValueError:
        return None
    return host


def _map_result(status_code: int) -> str:
    if status_code >= 500:
        return "SERVER_ERROR"
    if status_code >= 400:
        return "CLIENT_ERROR"
    return "SUCCESS"


async def _log_best_effort(pool: object, **fields: object) -> None:
    try:
        await insert_request_log(pool, request_id=uuid.uuid4(), **fields)
    except Exception:
        # A logging failure must never affect the caller's response or
        # replace/mask the exception (if any) already being handled.
        logger.error("failed to write request log (best-effort, no client impact)")


async def request_logging_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    start = time.monotonic()
    pool = getattr(request.app.state, "db_pool", None)
    bytes_received = _parse_content_length(request.headers.get("content-length"))
    client_ip = _parse_client_ip(request.client.host if request.client else None)

    try:
        response = await call_next(request)
    except Exception:
        duration_ms = int((time.monotonic() - start) * 1000)
        await _log_best_effort(
            pool,
            method=request.method,
            endpoint=request.url.path,
            http_status=500,
            duration_ms=duration_ms,
            correlation_id=getattr(request.state, "correlation_id", None),
            document_id=getattr(request.state, "document_id", None),
            source_system=getattr(request.state, "source_system", None),
            client_ip=client_ip,
            bytes_received=bytes_received,
            result="SERVER_ERROR",
            error_code=getattr(request.state, "error_code", None),
        )
        raise

    duration_ms = int((time.monotonic() - start) * 1000)
    await _log_best_effort(
        pool,
        method=request.method,
        endpoint=request.url.path,
        http_status=response.status_code,
        duration_ms=duration_ms,
        correlation_id=getattr(request.state, "correlation_id", None),
        document_id=getattr(request.state, "document_id", None),
        source_system=getattr(request.state, "source_system", None),
        client_ip=client_ip,
        bytes_received=bytes_received,
        result=_map_result(response.status_code),
        error_code=getattr(request.state, "error_code", None),
    )
    return response
