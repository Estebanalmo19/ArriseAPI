import re
import uuid
from hashlib import sha256
from pathlib import Path
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel

from app.config import get_storage_root

router = APIRouter(prefix="/api/v1", tags=["documents"])

CHUNK_SIZE = 1024 * 1024  # 1 MiB
MAX_FILE_SIZE_BYTES = 104_857_600  # 100 MiB

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


def _require_non_blank(value: str, field_name: str, max_length: int | None = None) -> str:
    stripped = value.strip()
    if not stripped:
        raise HTTPException(status_code=400, detail=f"{field_name} must not be empty or whitespace-only")
    if max_length is not None and len(stripped) > max_length:
        raise HTTPException(status_code=400, detail=f"{field_name} must not exceed {max_length} characters")
    return stripped


def _validate_service(value: str) -> str:
    stripped = _require_non_blank(value, "service", max_length=64)
    if not SERVICE_PATTERN.fullmatch(stripped):
        raise HTTPException(
            status_code=400,
            detail="service must contain only lowercase letters, numbers, hyphens, and underscores",
        )
    return stripped


def _validate_extension(original_filename: str) -> str:
    suffix = Path(original_filename).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"unsupported file extension: '{suffix or '(none)'}'")
    return suffix


def _validate_mime(content_type: str | None, extension: str) -> None:
    allowed = ALLOWED_MIME_TYPES[extension]
    if not content_type or content_type.lower() not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"declared MIME type '{content_type or '(none)'}' is not compatible with extension '{extension}'",
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


@router.post(
    "/documents",
    response_model=DocumentIngestResponse,
    status_code=status.HTTP_201_CREATED,
)
async def ingest_document(
    service: str = Form(...),
    document_name: str = Form(...),
    source_system: str = Form(...),
    document_type: str = Form(...),
    correlation_id: str = Form(...),
    file: UploadFile = File(...),
    storage_root: Path = Depends(get_storage_root),
) -> DocumentIngestResponse:
    try:
        service_v = _validate_service(service)
        document_name_v = _require_non_blank(document_name, "document_name", max_length=255)
        source_system_v = _require_non_blank(source_system, "source_system", max_length=64)
        document_type_v = _require_non_blank(document_type, "document_type", max_length=64)
        correlation_id_v = _require_non_blank(correlation_id, "correlation_id", max_length=128)

        original_filename = (file.filename or "").strip()
        if not original_filename:
            raise HTTPException(status_code=400, detail="original filename must be provided")

        extension = _validate_extension(original_filename)
        _validate_mime(file.content_type, extension)

        document_id = uuid.uuid4()
        dest_dir = storage_root / "received" / service_v
        final_name = f"{document_id}{extension}"
        final_path = dest_dir / final_name
        tmp_path = dest_dir / f"{final_name}.part"

        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
            size_bytes, sha256_hex = await _stream_to_temp_file(file, tmp_path, max_size=MAX_FILE_SIZE_BYTES)
            tmp_path.replace(final_path)
        except HTTPException:
            raise
        except Exception as exc:
            tmp_path.unlink(missing_ok=True)
            raise HTTPException(status_code=500, detail="failed to store uploaded file") from exc

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
