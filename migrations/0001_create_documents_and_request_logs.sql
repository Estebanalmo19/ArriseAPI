-- Database: arrise_vm_db
-- Schema: arrise_api
--
-- Statically reviewed during development. Execution against PostgreSQL
-- remains pending.

CREATE SCHEMA IF NOT EXISTS arrise_api;

CREATE TABLE IF NOT EXISTS arrise_api.documents (
    document_id        UUID PRIMARY KEY,
    service             VARCHAR(64)  NOT NULL,
    document_name        VARCHAR(255) NOT NULL,
    source_system         VARCHAR(64)  NOT NULL,
    document_type          VARCHAR(64)  NOT NULL,
    correlation_id           VARCHAR(128) NOT NULL,
    original_filename         VARCHAR(255) NOT NULL,
    stored_filename             VARCHAR(255) NOT NULL,
    storage_backend               VARCHAR(20)  NOT NULL DEFAULT 'LOCAL',
    storage_key                     TEXT NOT NULL,
    mime_type                         VARCHAR(255) NOT NULL,
    size_bytes                          BIGINT NOT NULL,
    sha256                                CHAR(64) NOT NULL,
    status                                  VARCHAR(30) NOT NULL,
    received_at                               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    error_code                                    VARCHAR(100),
    error_message                                   TEXT,

    -- storage_backend is deliberately a CHECK, not a native ENUM: adding a
    -- future backend (e.g. S3, once approved) is a simple ALTER of this
    -- constraint rather than an enum-type migration.
    CONSTRAINT documents_storage_backend_check
        CHECK (storage_backend = 'LOCAL'),
    CONSTRAINT documents_status_check
        CHECK (status IN ('RECEIVED', 'PROCESSING', 'PROCESSED', 'FAILED')),
    CONSTRAINT documents_size_bytes_positive
        CHECK (size_bytes > 0),
    CONSTRAINT documents_sha256_format
        CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    -- Defense-in-depth only: the primary protection is that storage_key is
    -- always application-generated (received/{service}/{uuid}{ext}), never
    -- derived from user input. This rejects absolute Unix paths, backslashes,
    -- and a ".." *path segment* specifically - not any occurrence of ".."
    -- as a substring, so a harmless generated filename containing ".." is
    -- never rejected.
    CONSTRAINT documents_storage_key_relative
        CHECK (
            storage_key !~ '^/'
            AND storage_key !~ '\\'
            AND storage_key !~ '(^|/)\.\.($|/)'
        )
);

CREATE INDEX IF NOT EXISTS idx_documents_correlation_id ON arrise_api.documents (correlation_id);
CREATE INDEX IF NOT EXISTS idx_documents_sha256 ON arrise_api.documents (sha256);
CREATE INDEX IF NOT EXISTS idx_documents_status ON arrise_api.documents (status);
CREATE INDEX IF NOT EXISTS idx_documents_received_at ON arrise_api.documents (received_at);
CREATE INDEX IF NOT EXISTS idx_documents_service ON arrise_api.documents (service);

CREATE TABLE IF NOT EXISTS arrise_api.request_logs (
    request_id       UUID PRIMARY KEY,
    correlation_id     VARCHAR(128),
    document_id           UUID NULL REFERENCES arrise_api.documents(document_id) ON DELETE SET NULL,
    method                  VARCHAR(10) NOT NULL,
    endpoint                 TEXT NOT NULL,
    source_system              VARCHAR(64),
    client_ip                    INET,
    http_status                    INTEGER NOT NULL,
    duration_ms                      INTEGER NOT NULL,
    bytes_received                     BIGINT,
    result                                VARCHAR(30) NOT NULL,
    error_code                             VARCHAR(100),
    created_at                               TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT request_logs_http_status_range
        CHECK (http_status BETWEEN 100 AND 599),
    CONSTRAINT request_logs_duration_non_negative
        CHECK (duration_ms >= 0),
    CONSTRAINT request_logs_bytes_received_non_negative
        CHECK (bytes_received IS NULL OR bytes_received >= 0),
    CONSTRAINT request_logs_result_check
        CHECK (result IN ('SUCCESS', 'CLIENT_ERROR', 'SERVER_ERROR'))
);

CREATE INDEX IF NOT EXISTS idx_request_logs_created_at ON arrise_api.request_logs (created_at);
CREATE INDEX IF NOT EXISTS idx_request_logs_correlation_id ON arrise_api.request_logs (correlation_id);
CREATE INDEX IF NOT EXISTS idx_request_logs_document_id ON arrise_api.request_logs (document_id);
CREATE INDEX IF NOT EXISTS idx_request_logs_http_status ON arrise_api.request_logs (http_status);
CREATE INDEX IF NOT EXISTS idx_request_logs_result ON arrise_api.request_logs (result);
