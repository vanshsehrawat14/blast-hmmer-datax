"""The MySQL layer, against a real copied database when one is configured.

Everything here is read-only. Run with credentials in the environment:

    ENZYMEX_DB_HOST=... ENZYMEX_DB_PASSWORD=... ENZYMEX_DB_CONFIRM_COPY=true \
      pytest tests/test_database.py -m mysql

Never point these at production.
"""

from __future__ import annotations

import pytest

from app.config import Settings
from app.references import db as dbmod
from tests.conftest import needs_mysql

pytestmark = [pytest.mark.mysql, needs_mysql]


@pytest.fixture
def live() -> Settings:
    s = Settings()
    if not s.db_confirm_copy:
        pytest.skip("ENZYMEX_DB_CONFIRM_COPY is not set")
    return s


# ---------------------------------------------------------------- guards (no DB needed)
def test_connect_refuses_without_the_copy_acknowledgement():
    s = Settings(_env_file=None, db_password="x", db_confirm_copy=False)
    with pytest.raises(dbmod.DatabaseNotConfirmed, match="copy"):
        dbmod.connect(s)


def test_ping_reports_the_refusal_instead_of_raising():
    s = Settings(_env_file=None, db_password="x", db_confirm_copy=False)
    ok, message = dbmod.ping(s)
    assert ok is False and "ENZYMEX_DB_CONFIRM_COPY" in message


@pytest.mark.parametrize("bad", ["users; DROP TABLE x", "a b", "`quoted`", "", "tbl'"])
def test_identifier_validation_rejects_anything_unquotable(bad):
    with pytest.raises(ValueError, match="unsafe"):
        dbmod._check_identifier(bad, "table")


def test_dsn_summary_never_contains_the_password():
    # Host and port are pinned here rather than left to the defaults: this
    # module is marked `mysql`, so it runs with ENZYMEX_DB_* exported and
    # would otherwise assert against the caller's connection settings.
    s = Settings(_env_file=None, db_password="hunter2", db_user="u", db_name="d",
                 db_host="127.0.0.1", db_port=3306)
    assert "hunter2" not in s.dsn_summary()
    assert "hunter2" not in repr(s)
    assert s.dsn_summary() == "u@127.0.0.1:3306/d"


# ---------------------------------------------------------------- live database
def test_connects_and_reports_a_version(live):
    ok, detail = dbmod.ping(live)
    assert ok, detail
    assert "MySQL" in detail


def test_schema_discovery_finds_the_documented_columns(live):
    conn = dbmod.connect(live)
    try:
        schema = dbmod.inspect_schema(conn, live.db_table)
    finally:
        conn.close()
    assert schema.primary_key, "enzymesdata needs a primary key for a stable export"
    assert schema.has("sequence"), f"no sequence column found in {schema.columns}"
    assert schema.row_count > 0
    assert schema.engine is None or schema.engine.lower() == "innodb"


def test_the_session_cannot_write(live):
    """Belt and braces on top of a SELECT-only grant."""
    import pymysql

    conn = dbmod.connect(live)
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            with pytest.raises(pymysql.err.MySQLError):
                cur.execute(f"CREATE TABLE zz_should_not_exist_{id(cur)} (a INT)")
    finally:
        conn.close()


def test_rows_stream_in_primary_key_order(live):
    conn = dbmod.connect(live)
    try:
        schema = dbmod.inspect_schema(conn, live.db_table)
        keys = [r[schema.primary_key]
                for r in dbmod.iter_rows(conn, schema, limit=50)]
    finally:
        conn.close()
    assert keys == sorted(keys)


def test_profiling_characterises_the_table(live):
    conn = dbmod.connect(live)
    try:
        schema = dbmod.inspect_schema(conn, live.db_table)
        stats = dbmod.profile_table(conn, schema)
    finally:
        conn.close()
    assert stats["rows"] == schema.row_count
    for key in ("null_sequences", "empty_sequences", "duplicate_sequences",
                "sequence_length"):
        assert key in stats
