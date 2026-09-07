# ArriseAPI

## Purpose

ArriseAPI is a FastAPI service for receiving documents sent by Microsoft Power Automate. Future development will add document storage, PostgreSQL metadata tracking, and processing and normalization services.

## Current Scope

The current version provides:

- A minimal FastAPI application.
- A `GET /health` endpoint.
- Local-only binding instructions.

Database integration, document ingestion, authentication, and production deployment configuration have not been implemented yet.

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

Install the dependencies:

```powershell
python -m pip install -r requirements.txt
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

## Secrets

Production credentials will be retrieved at runtime from AWS Systems Manager Parameter Store using SecureString parameters encrypted with AWS KMS. Access will use the EC2 instance IAM role with least-privilege permissions.

Production secrets must never be stored in source code, local environment files, logs, or Git.
