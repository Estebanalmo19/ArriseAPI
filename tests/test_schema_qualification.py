import re
from pathlib import Path

import app.db as db_module

REPO_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_SQL = (REPO_ROOT / "migrations" / "0001_create_documents_and_request_logs.sql").read_text()
PURGE_SQL = (REPO_ROOT / "scripts" / "purge_old_request_logs.sql").read_text()

ALL_SQL_FILES = {
    "migration": MIGRATION_SQL,
    "purge": PURGE_SQL,
}

# These tests inspect repository source/SQL text only - they never open a
# database connection or an SSH tunnel.


# --- app/db.py: application SQL is schema-qualified -------------------------


def test_document_insert_sql_targets_arrise_api_schema():
    assert "INSERT INTO arrise_api.documents" in db_module.INSERT_DOCUMENT_SQL


def test_request_log_insert_sql_targets_arrise_api_schema():
    assert "INSERT INTO arrise_api.request_logs" in db_module.INSERT_REQUEST_LOG_SQL


def test_no_unqualified_insert_into_documents():
    assert "INSERT INTO documents" not in db_module.INSERT_DOCUMENT_SQL
    assert "INSERT INTO documents" not in db_module.INSERT_REQUEST_LOG_SQL


def test_no_unqualified_insert_into_request_logs():
    assert "INSERT INTO request_logs" not in db_module.INSERT_DOCUMENT_SQL
    assert "INSERT INTO request_logs" not in db_module.INSERT_REQUEST_LOG_SQL


def test_db_module_source_has_no_unqualified_insert():
    source = Path(db_module.__file__).read_text()
    assert "INSERT INTO documents" not in source
    assert "INSERT INTO request_logs" not in source


# --- purge script -------------------------------------------------------


def test_retention_sql_targets_arrise_api_request_logs():
    assert "DELETE FROM arrise_api.request_logs" in PURGE_SQL


def test_purge_script_has_no_unqualified_request_logs_reference():
    assert "DELETE FROM request_logs" not in PURGE_SQL


# --- migration: schema creation ------------------------------------------


def test_migration_creates_arrise_api_schema():
    assert "CREATE SCHEMA IF NOT EXISTS arrise_api;" in MIGRATION_SQL


def test_migration_creates_schema_before_any_table():
    schema_pos = MIGRATION_SQL.index("CREATE SCHEMA IF NOT EXISTS arrise_api;")
    table_positions = [m.start() for m in re.finditer(r"CREATE TABLE", MIGRATION_SQL)]
    assert table_positions, "expected at least one CREATE TABLE statement"
    assert all(schema_pos < pos for pos in table_positions)


# --- migration: exactly the two expected schema-qualified tables ---------


def test_migration_creates_exactly_documents_and_request_logs_tables():
    created_tables = re.findall(r"CREATE TABLE IF NOT EXISTS (\S+)", MIGRATION_SQL)
    assert set(created_tables) == {"arrise_api.documents", "arrise_api.request_logs"}
    assert len(created_tables) == 2


# --- migration: foreign key ----------------------------------------------


def test_foreign_key_references_schema_qualified_documents():
    assert "REFERENCES arrise_api.documents(document_id)" in MIGRATION_SQL


# --- migration: indexes target schema-qualified tables --------------------


def test_all_create_index_statements_target_schema_qualified_tables():
    index_statements = re.findall(r"CREATE INDEX IF NOT EXISTS \S+ ON (\S+)", MIGRATION_SQL)
    assert index_statements, "expected at least one CREATE INDEX statement"
    for table in index_statements:
        assert table.startswith("arrise_api."), f"index targets unqualified table: {table}"


def test_index_count_matches_expected():
    index_statements = re.findall(r"CREATE INDEX IF NOT EXISTS", MIGRATION_SQL)
    assert len(index_statements) == 10


# --- safety: no database/role/grant statements anywhere in repo SQL -------


def test_no_sql_file_creates_a_database():
    for name, sql in ALL_SQL_FILES.items():
        assert not re.search(r"CREATE\s+DATABASE", sql, re.IGNORECASE), f"{name} creates a database"


def test_no_sql_file_creates_a_role_or_user():
    for name, sql in ALL_SQL_FILES.items():
        assert not re.search(r"CREATE\s+(ROLE|USER)", sql, re.IGNORECASE), f"{name} creates a role/user"


def test_no_sql_file_grants_privileges():
    for name, sql in ALL_SQL_FILES.items():
        assert not re.search(r"\bGRANT\b", sql, re.IGNORECASE), f"{name} grants privileges"
