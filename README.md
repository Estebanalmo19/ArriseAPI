# ArriseAPI

## Purpose

ArriseAPI is a FastAPI service for receiving documents sent by Microsoft Power Automate. Future development will add document storage, PostgreSQL metadata tracking, and processing and normalization services.

## Current Scope

The current version provides:

- A minimal FastAPI application.
- A `GET /health` endpoint.
- A `POST /api/v1/documents` endpoint that accepts document uploads, stores them on the local filesystem, and records their metadata in PostgreSQL.
- Request logging (method, endpoint, status, duration, correlation/document identifiers) for every API request, written to PostgreSQL on a best-effort basis.
- Local-only binding instructions.

**This increment is not production-deployable.** Production database credentials will be retrieved from AWS SSM Parameter Store (SecureString) in a later increment; that retrieval is not implemented yet, and there is currently no supported way to run this application against a production database. Authentication and production deployment configuration also remain unimplemented.

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

## Database Persistence and Request Logging (Development)

**Not production-deployable yet.** This section describes a local-development-only
setup. Production database credentials will be retrieved from AWS SSM Parameter
Store (SecureString) in a later increment - that retrieval is not implemented here.

### Database

- Database name: `arrise_api`, schema: `public`.
- Migration: `migrations/0001_create_documents_and_request_logs.sql` (plain SQL,
  run manually against a local PostgreSQL instance you provide - there is no
  migration framework in this increment). Apply it with, e.g.:
  ```powershell
  psql -d arrise_api -f migrations/0001_create_documents_and_request_logs.sql
  ```
- Two tables: `documents` (one row per successfully ingested document) and
  `request_logs` (one row per API request, including failed ones).
- Document files remain on the local filesystem; PostgreSQL stores only
  metadata and a **relative** `storage_key` (e.g.
  `received/doc-processor/550e8400-e29b-41d4-a716-446655440000.pdf`) - the
  absolute filesystem path is never written to the database.
- No binary or Base64 file content is ever stored in PostgreSQL.

### Configuration

| Variable | Purpose | Default |
|---|---|---|
| `ARRISE_ENV` | `development`, `test`, or `production`. | `development` |
| `ARRISE_DB_DSN` | A PostgreSQL connection string, **local development/test only**. | none (required to enable DB features) |

`ARRISE_DB_DSN` must never be given a real value in this README, in tests, in
source code, or in Git - set it only in your own shell environment. It is
never read from a committed `.env` file (this project does not use one).
When `ARRISE_ENV=production`, `ARRISE_DB_DSN` is **always ignored**, even if
set - production credentials are AWS-SSM-only (not implemented in this
increment). All database configuration is read through a single provider
function (`app.config.get_database_dsn`) so a future SSM-backed
implementation can replace it without touching endpoint or connection-pool
logic.

### Behavior with no database configured

If `ARRISE_DB_DSN` is not set (or `ARRISE_ENV=production`, where it's always
ignored):
- `GET /health` continues to work normally.
- `POST /api/v1/documents` returns HTTP **503** before touching the
  filesystem or streaming any data - no final file and no `.part` file are
  ever created. The response never reveals configuration details.

### Consistency between the file and the database row

A successful `201` response is returned only when **both** the file exists
on disk and its metadata row has been committed to PostgreSQL, in this order:
validate → confirm DB is configured → stream to a `.part` file → rename to
the final file → insert the `documents` row and commit → return `201`.

If the database insert fails after the file has already been renamed, the
file is deleted (best-effort) and a generic `500` is returned. **This is not
a true atomic transaction across PostgreSQL and the filesystem** - it is a
best-effort ordering with a compensating delete. In the rare case where that
compensating delete itself fails, no filesystem detail is exposed to the
caller; a minimal, secret-free note (document id, service, which file kind)
is logged server-side so the orphaned file can be investigated manually.

### Request logging

Every API request (including `GET /health` and structurally invalid
requests) is logged to `request_logs` on a best-effort basis via ASGI
middleware - a logging failure never changes the response or exception the
caller receives, and the middleware never reads the request body, form
fields, or an `UploadFile` (so it cannot interfere with the upload stream).
For a request that fails before the endpoint runs (e.g. a required
multipart field is missing entirely, producing FastAPI's own `422`),
`correlation_id`, `source_system`, and `document_id` are logged as `NULL` -
the middleware does not attempt to recover them by reading the body itself.

`client_ip` is populated from `request.client.host` only when Python's
`ipaddress` module validates it as an IPv4/IPv6 address (rejected otherwise
- e.g. a test client's placeholder host). **`X-Forwarded-For` is not
consulted in this increment.** Trusted-proxy IP resolution will be
configured once Nginx/Uvicorn are deployed in front of this service.

Retention is 90 days. Document records are never auto-deleted; only
`request_logs` rows are. Run manually, or schedule externally (not wired up
in this increment):
```powershell
psql -d arrise_api -f scripts/purge_old_request_logs.sql
```

## Secrets

Production credentials will be retrieved at runtime from AWS Systems Manager Parameter Store using SecureString parameters encrypted with AWS KMS. Access will use the EC2 instance IAM role with least-privilege permissions.

Production secrets must never be stored in source code, local environment files, logs, or Git.
