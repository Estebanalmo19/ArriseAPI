import logging
import re
import uuid
from hashlib import sha256
from pathlib import Path
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel

from app.config import get_storage_root
from app.db import get_db_pool, insert_document

router = APIRouter(prefix="/api/v1", tags=["documents"])

logger = logging.getLogger(__name__)

CHUNK_SIZE = 1024 * 1024  # 1 MiB
MAX_FILE_SIZE_BYTES = 104_857_600  # 100 MiB
MAX_ORIGINAL_FILENAME_LENGTH = 255

SERVICE_PATTERN = re.compile(r"^[a-z0-9_-]+$")

ALLOWED_EXTENSIONS = {".pdf", ".docx", ".xlsx", ".csv"}

ALLOWED_MIME_TYPES: dict[str, set[str]] = {
    ".pdf": {"application/pdf", "application/octet-stream"},
    ".docx": {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/octet-stream",
    },
    ".xlsx": {
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/octet-stream",
    },
    ".csv": {
        "text/csv",
        "application/csv",
        "application/vnd.ms-excel",
        "application/octet-stream",
    },
}


class DocumentIngestResponse(BaseModel):
    document_id: UUID
    status: Literal["RECEIVED"]
    service: str
    document_name: str
    source_system: str
    document_type: str
    correlation_id: str
    original_filename: str
    stored_filename: str
    size_bytes: int
    sha256: str


def _fail(request: Request, status_code: int, detail: str, error_code: str) -> None:
    request.state.error_code = error_code
    raise HTTPException(status_code=status_code, detail=detail)


def _require_non_blank(request: Request, value: str, field_name: str, max_length: int | None = None) -> str:
    stripped = value.strip()
    if not stripped:
        _fail(request, 400, f"{field_name} must not be empty or whitespace-only", "METADATA_INVALID")
    if max_length is not None and len(stripped) > max_length:
        _fail(request, 400, f"{field_name} must not exceed {max_length} characters", "METADATA_INVALID")
    return stripped


def _validate_service(request: Request, value: str) -> str:
    stripped = _require_non_blank(request, value, "service", max_length=64)
    if not SERVICE_PATTERN.fullmatch(stripped):
        _fail(
            request,
            400,
            "service must contain only lowercase letters, numbers, hyphens, and underscores",
            "METADATA_INVALID",
        )
    return stripped


def _validate_extension(request: Request, original_filename: str) -> str:
    suffix = Path(original_filename).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        _fail(request, 400, f"unsupported file extension: '{suffix or '(none)'}'", "EXTENSION_UNSUPPORTED")
    return suffix


def _validate_mime(request: Request, content_type: str | None, extension: str) -> None:
    allowed = ALLOWED_MIME_TYPES[extension]
    if not content_type or content_type.lower() not in allowed:
        _fail(
            request,
            400,
            f"declared MIME type '{content_type or '(none)'}' is not compatible with extension '{extension}'",
            "MIME_MISMATCH",
        )


def _open_for_write(path: Path):
    return open(path, "wb")


async def _stream_to_temp_file(upload: UploadFile, tmp_path: Path, max_size: int) -> tuple[int, str]:
    digest = sha256()
    size = 0
    try:
        with _open_for_write(tmp_path) as out:
            while True:
                chunk = await upload.read(CHUNK_SIZE)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_size:
                    raise HTTPException(
                        status_code=413,
                        detail=f"file exceeds maximum allowed size of {max_size} bytes",
                    )
                digest.update(chunk)
                out.write(chunk)
    except HTTPException:
        tmp_path.unlink(missing_ok=True)
        raise
    except Exception as exc:
        tmp_path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail="failed to store uploaded file") from exc

    if size == 0:
        tmp_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="uploaded file is empty")

    return size, digest.hexdigest()


def _cleanup_after_failure(tmp_path: Path, final_path: Path, document_id: uuid.UUID, service: str) -> None:
    """Best-effort removal of any file left behind by a failed request.
    Never raises: a cleanup failure must not mask the response already
    being returned to the caller. Logged server-side only, with no
    exception text/traceback and no user-supplied filenames or content."""
    for path_kind, path in (("tmp", tmp_path), ("final", final_path)):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.error(
                "orphan risk: failed to remove %s file after document persistence failure "
                "(document_id=%s, service=%s)",
                path_kind,
                document_id,
                service,
            )


@router.post(
    "/documents",
    response_model=DocumentIngestResponse,
    status_code=status.HTTP_201_CREATED,
)
async def ingest_document(
    request: Request,
    service: str = Form(...),
    document_name: str = Form(...),
    source_system: str = Form(...),
    document_type: str = Form(...),
    correlation_id: str = Form(...),
    file: UploadFile = File(...),
    storage_root: Path = Depends(get_storage_root),
    pool: AsyncConnectionPool | None = Depends(get_db_pool),
) -> DocumentIngestResponse:
    try:
        # 1-4. correlation_id and source_system are validated first, and each
        # is stored in request.state immediately after it individually
        # validates - never before - so the request-logging middleware can
        # attribute the log row correctly even if service/document_name/
        # document_type turn out to be invalid. Only a validated, trimmed
        # value is ever placed in request.state; a field that fails
        # validation raises before its assignment line runs, so it is never
        # logged as if it were valid.
        #
        # This ordering intentionally changes which field's error is
        # returned first when multiple fields are simultaneously invalid
        # (correlation_id/source_system now take precedence over service/
        # document_name/document_type). That tradeoff is accepted in favor
        # of correct log attribution rather than preserving the old
        # precedence.
        correlation_id_v = _require_non_blank(request, correlation_id, "correlation_id", max_length=128)
        request.state.correlation_id = correlation_id_v

        source_system_v = _require_non_blank(request, source_system, "source_system", max_length=64)
        request.state.source_system = source_system_v

        # 5-7. Validate the remaining metadata fields.
        service_v = _validate_service(request, service)
        document_name_v = _require_non_blank(request, document_name, "document_name", max_length=255)
        document_type_v = _require_non_blank(request, document_type, "document_type", max_length=64)

        original_filename = (file.filename or "").strip()
        if not original_filename:
            _fail(request, 400, "original filename must be provided", "FILENAME_MISSING")
        if len(original_filename) > MAX_ORIGINAL_FILENAME_LENGTH:
            _fail(
                request,
                400,
                f"original_filename must not exceed {MAX_ORIGINAL_FILENAME_LENGTH} characters",
                "FILENAME_TOO_LONG",
            )

        extension = _validate_extension(request, original_filename)
        _validate_mime(request, file.content_type, extension)

        # 2. Confirm database availability before streaming the file.
        if pool is None:
            _fail(request, 503, "document storage is temporarily unavailable", "DATABASE_UNAVAILABLE")

        document_id = uuid.uuid4()
        dest_dir = storage_root / "received" / service_v
        final_name = f"{document_id}{extension}"
        final_path = dest_dir / final_name
        tmp_path = dest_dir / f"{final_name}.part"
        storage_key = f"received/{service_v}/{final_name}"

        try:
            # 3. Stream into .part while calculating size and SHA-256.
            dest_dir.mkdir(parents=True, exist_ok=True)
            size_bytes, sha256_hex = await _stream_to_temp_file(file, tmp_path, max_size=MAX_FILE_SIZE_BYTES)
            # 4. Rename .part to the final file.
            tmp_path.replace(final_path)
            # 5. Insert the documents row in PostgreSQL and commit.
            await insert_document(
                pool,
                document_id=document_id,
                service=service_v,
                document_name=document_name_v,
                source_system=source_system_v,
                document_type=document_type_v,
                correlation_id=correlation_id_v,
                original_filename=original_filename,
                stored_filename=final_name,
                storage_key=storage_key,
                mime_type=file.content_type,
                size_bytes=size_bytes,
                sha256=sha256_hex,
            )
        except HTTPException as exc:
            if exc.status_code == 413:
                request.state.error_code = "FILE_TOO_LARGE"
            elif exc.status_code == 500:
                request.state.error_code = "STORAGE_FAILURE"
            else:
                request.state.error_code = "FILE_EMPTY"
            # 8. Best-effort deletion of both final and .part paths.
            _cleanup_after_failure(tmp_path, final_path, document_id, service_v)
            raise
        except Exception as exc:
            request.state.error_code = "STORAGE_FAILURE"
            _cleanup_after_failure(tmp_path, final_path, document_id, service_v)
            raise HTTPException(status_code=500, detail="failed to store uploaded file") from exc

        # 6. Set request.state.document_id only after the insert commits.
        request.state.document_id = document_id

        # 7. Return HTTP 201 only after the file and database row both exist.
        return DocumentIngestResponse(
            document_id=document_id,
            status="RECEIVED",
            service=service_v,
            document_name=document_name_v,
            source_system=source_system_v,
            document_type=document_type_v,
            correlation_id=correlation_id_v,
            original_filename=original_filename,
            stored_filename=final_name,
            size_bytes=size_bytes,
            sha256=sha256_hex,
        )
    finally:
        await file.close()
