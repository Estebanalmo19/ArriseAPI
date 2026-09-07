# ArriseAPI

## Purpose

ArriseAPI is a FastAPI service for receiving documents sent by Microsoft Power Automate. Future development will add document storage, PostgreSQL metadata tracking, and processing and normalization services.

## Current Scope

The current version provides:

- A minimal FastAPI application.
- A `GET /health` endpoint.
- A `POST /api/v1/documents` endpoint that accepts document uploads and stores them on the local filesystem for later processing.
- Local-only binding instructions.

Database integration, authentication, and production deployment configuration have not been implemented yet.

## Requirements

- Python 3.11 or later

## Setup on Windows PowerShell

Create a virtual environment:

```powershell
python -m venv venv
```

Activate it:

```powershell
.\venv\Scripts\Activate.ps1
```

Install the runtime dependencies:

```powershell
python -m pip install -r requirements.txt
```

To also run the test suite, install the development dependencies instead (this installs the runtime dependencies too):

```powershell
python -m pip install -r requirements-dev.txt
```

## Run Locally

Run FastAPI bound to localhost:

```powershell
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

## Test the Health Endpoint

From another PowerShell terminal:

```powershell
Invoke-RestMethod -Uri http://127.0.0.1:8000/health
```

Expected JSON response:

```json
{
  "status": "ok"
}
```

## Document Ingestion (Development)

**Endpoint:** `POST /api/v1/documents`
**Content type:** `multipart/form-data`

Required form fields:

| Field | Description |
|---|---|
| `service` | Identifies the future processing service responsible for the document. Lowercase letters, numbers, `-`, `_` only; maximum 64 characters. |
| `document_name` | Business-facing document name, independent of the uploaded file's original filename. Maximum 255 characters. |
| `source_system` | Identifies the sender, e.g. `power_automate`. Maximum 64 characters. |
| `document_type` | Classifies the document. Maximum 64 characters. |
| `correlation_id` | Caller-supplied identifier for request traceability. Maximum 128 characters. |
| `file` | The binary document. |

All five metadata fields are required and cannot be empty or whitespace-only.

Supported formats: PDF (`.pdf`), Word (`.docx`), Excel (`.xlsx`), CSV (`.csv`).
Maximum file size: 100 MiB (104857600 bytes).

### Validation error behavior

- A structurally **missing** required multipart field (the field itself absent from the request) returns HTTP **422**, using FastAPI's standard validation response. This also applies to a `file` part sent with an empty-string filename: the multipart parser does not construct a file object in that case, so it is rejected at the same structural level.
- A **supplied** field that is empty, whitespace-only, too long, has an invalid `service` format, an unsupported file extension, or a mismatched MIME type returns HTTP **400** with a clear JSON error. A `file` part with a whitespace-only filename (e.g. `" "`) falls into this category, since a file object is constructed but fails validation.
- A file exceeding the 100 MiB limit returns HTTP **413**.
- Unexpected filesystem errors return a generic HTTP **500** with no internal path or exception details.

### Local Storage (development only)

> **Warning:** this endpoint currently stores received files on the local filesystem.
> This is a development-only implementation and is not suitable for production use.
> A durable, production-grade storage backend will be introduced in a later increment.

Files are written under `ARRISE_STORAGE_ROOT/received/{service}/{document_id}{extension}`,
using a temporary `.part` file that is only renamed to its final name after the upload
completes and validates successfully. The temporary file is removed if validation or
writing fails at any point.

`ARRISE_STORAGE_ROOT` — non-secret environment variable controlling the storage root.
Local default: `data`.

### Scope limitation

This increment validates only the declared MIME type and the file extension. It does
not inspect file signatures (magic bytes) or ZIP-internal structure, so it does not
prove that the uploaded bytes are a genuine PDF, DOCX, or XLSX file. Content-level
verification may be added in a later increment.

### Example (PowerShell 7+)

```powershell
Invoke-RestMethod -Uri http://127.0.0.1:8000/api/v1/documents `
  -Method Post `
  -Form @{
    service        = "doc-processor"
    document_name  = "Invoice 2024-001"
    source_system  = "power_automate"
    document_type  = "invoice"
    correlation_id = "corr-12345"
    file           = Get-Item "C:\path\to\invoice.pdf"
  }
```

> Note: `-Form` requires PowerShell 6+ (`pwsh`) — it is not available in Windows PowerShell 5.1.

## Secrets

Production credentials will be retrieved at runtime from AWS Systems Manager Parameter Store using SecureString parameters encrypted with AWS KMS. Access will use the EC2 instance IAM role with least-privilege permissions.

Production secrets must never be stored in source code, local environment files, logs, or Git.
