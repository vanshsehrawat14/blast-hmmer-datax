"""Read-only access to the copied EnzymeX MySQL database.

Only the reference build uses this module; the web application has no MySQL
dependency at all. Three safety properties are enforced here:

  * the session is opened `READ ONLY` and never committed, so no statement in
    this process can write to the copied database even by mistake;
  * `ENZYMEX_DB_CONFIRM_COPY=true` is required, which is a deliberate speed
    bump against pointing the tool at production by editing one host variable;
  * the password lives in a SecretStr and is passed straight to the driver —
    it is not interpolated into any string that gets logged.

The schema is discovered rather than assumed. The handover notes list the
`enzymesdata` columns as description / sequence / EC / motif / active /
binding / interpretation / source / modified / created, but the live copy is
the authority, so column names are resolved case-insensitively against a set
of plausible aliases and anything missing is simply reported as absent.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Iterator

import pymysql
import pymysql.cursors

from app.config import Settings

log = logging.getLogger(__name__)

# Logical field -> acceptable column names, first match wins (case-insensitive).
COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "sequence": ("sequence", "seq", "protein_sequence", "aa_sequence"),
    "description": ("description", "desc", "name", "protein_name", "title"),
    "ec": ("ec", "ec_number", "ecnumber", "ec_num"),
    "source": ("source", "db_source", "database", "origin"),
    "motif": ("motif", "motifs"),
    "active": ("active", "active_site", "activesite"),
    "binding": ("binding", "binding_site", "bindingsite"),
    "interpretation": ("interpretation", "interpret"),
    "created": ("created", "created_at", "create_time"),
    "modified": ("modified", "modified_at", "updated", "updated_at"),
    "accession": (
        "accession", "acc", "uniprot", "uniprot_id", "uniprotid", "entry",
        "identifier",
    ),
}

# Anything not in this set is refused before it reaches an f-string. Table and
# column names cannot be passed as bound parameters, so they are validated
# instead of quoted-and-hoped.
_IDENTIFIER_OK = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_$")


class DatabaseNotConfirmed(RuntimeError):
    pass


def _check_identifier(name: str, kind: str) -> str:
    if not name or not set(name) <= _IDENTIFIER_OK:
        raise ValueError(f"refusing to use unsafe {kind} name: {name!r}")
    return name


@dataclass
class TableSchema:
    table: str
    columns: list[str]                 # actual column names, in table order
    mapping: dict[str, str]            # logical field -> actual column name
    primary_key: str | None
    row_count: int
    engine: str | None = None

    def has(self, field: str) -> bool:
        return field in self.mapping

    def column(self, field: str) -> str | None:
        return self.mapping.get(field)

    def missing(self) -> list[str]:
        return [f for f in COLUMN_ALIASES if f not in self.mapping]


def connect(settings: Settings) -> pymysql.connections.Connection:
    if not settings.db_confirm_copy:
        raise DatabaseNotConfirmed(
            "Refusing to connect: set ENZYMEX_DB_CONFIRM_COPY=true to confirm that "
            f"{settings.dsn_summary()} is a copy of the EnzymeX database and not "
            "the production instance."
        )
    log.info("connecting read-only to %s", settings.dsn_summary())
    conn = pymysql.connect(
        host=settings.db_host,
        port=settings.db_port,
        user=settings.db_user,
        password=settings.db_password.get_secret_value(),
        database=settings.db_name,
        connect_timeout=settings.db_connect_timeout,
        charset="utf8mb4",
        autocommit=True,
        cursorclass=pymysql.cursors.SSDictCursor,
    )
    # Belt and braces on top of a SELECT-only grant: if the account happens to
    # have write privileges, this session still cannot use them.
    with conn.cursor(pymysql.cursors.DictCursor) as cur:
        cur.execute("SET SESSION TRANSACTION READ ONLY")
    return conn


def begin_consistent_snapshot(conn) -> None:
    """Pin both export scans to one read-only view of the copied table."""
    with conn.cursor(pymysql.cursors.DictCursor) as cur:
        cur.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        cur.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")


def ping(settings: Settings) -> tuple[bool, str]:
    """Connectivity probe for /health. Returns (ok, message) and never raises."""
    try:
        conn = connect(settings)
    except DatabaseNotConfirmed as exc:
        return False, str(exc)
    except Exception as exc:  # driver errors carry host/user but not the password
        return False, f"{type(exc).__name__}: {exc}"
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute("SELECT VERSION() AS v")
            version = cur.fetchone()["v"]
        return True, f"MySQL {version} at {settings.dsn_summary()}"
    finally:
        conn.close()


def inspect_schema(conn, table: str) -> TableSchema:
    table = _check_identifier(table, "table")
    with conn.cursor(pymysql.cursors.DictCursor) as cur:
        cur.execute(
            "SELECT COLUMN_NAME, COLUMN_KEY, DATA_TYPE FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s ORDER BY ORDINAL_POSITION",
            (table,),
        )
        cols = cur.fetchall()
        if not cols:
            raise ValueError(f"table {table!r} not found in the configured database")

        names = [c["COLUMN_NAME"] for c in cols]
        lowered = {n.lower(): n for n in names}
        mapping = {}
        for field, aliases in COLUMN_ALIASES.items():
            for alias in aliases:
                if alias in lowered:
                    mapping[field] = lowered[alias]
                    break

        pk = next((c["COLUMN_NAME"] for c in cols if c["COLUMN_KEY"] == "PRI"), None)
        if pk is None:
            # Without a stable key, internal reference ids would change on
            # every rebuild. Fall back to an `id` column if one exists.
            pk = lowered.get("id")

        cur.execute(
            "SELECT ENGINE FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
            (table,),
        )
        table_info = cur.fetchone() or {}
        engine = table_info.get("ENGINE")

        cur.execute(f"SELECT COUNT(*) AS n FROM `{table}`")
        n = cur.fetchone()["n"]

    return TableSchema(table=table, columns=names, mapping=mapping,
                       primary_key=pk, row_count=n, engine=engine)


def iter_rows(conn, schema: TableSchema, *, limit: int = 0,
              batch: int = 5_000) -> Iterator[dict[str, Any]]:
    """Stream the table in a deterministic order.

    Ordering by the primary key is what makes the exported FASTA byte-stable
    between runs, which in turn makes the build id meaningful. A server-side
    cursor keeps memory flat regardless of table size.
    """
    if schema.primary_key is None:
        raise ValueError(
            f"{schema.table} has no primary key; a stable export order and stable "
            "reference identifiers cannot be derived. Add a key to the copy, or "
            "export from a view that provides one."
        )
    pk = _check_identifier(schema.primary_key, "column")
    wanted = [pk] + [c for c in schema.mapping.values() if c != pk]
    select = ", ".join(f"`{_check_identifier(c, 'column')}`" for c in wanted)

    sql = f"SELECT {select} FROM `{schema.table}` ORDER BY `{pk}` ASC"
    if limit > 0:
        sql += f" LIMIT {int(limit)}"

    with conn.cursor(pymysql.cursors.SSDictCursor) as cur:
        cur.execute(sql)
        while True:
            rows = cur.fetchmany(batch)
            if not rows:
                return
            yield from rows


def profile_table(conn, schema: TableSchema, sample: int = 0) -> dict[str, Any]:
    """Cheap characterisation of the table, used by `refbuild inspect`.

    Counts are computed in SQL so the whole table never has to cross the wire.
    """
    stats: dict[str, Any] = {
        "table": schema.table,
        "rows": schema.row_count,
        "primary_key": schema.primary_key,
        "engine": schema.engine,
        "columns_present": schema.mapping,
        "columns_missing": schema.missing(),
        "all_columns": schema.columns,
    }
    seq_col = schema.column("sequence")
    if not seq_col:
        return stats

    seq = f"`{_check_identifier(seq_col, 'column')}`"
    with conn.cursor(pymysql.cursors.DictCursor) as cur:
        cur.execute(
            f"SELECT COUNT(*) AS n_null FROM `{schema.table}` WHERE {seq} IS NULL"
        )
        stats["null_sequences"] = cur.fetchone()["n_null"]
        cur.execute(
            f"SELECT COUNT(*) AS n_empty FROM `{schema.table}` "
            f"WHERE {seq} IS NOT NULL AND CHAR_LENGTH(TRIM({seq})) = 0"
        )
        stats["empty_sequences"] = cur.fetchone()["n_empty"]
        cur.execute(
            f"SELECT MIN(CHAR_LENGTH({seq})) AS lo, MAX(CHAR_LENGTH({seq})) AS hi, "
            f"AVG(CHAR_LENGTH({seq})) AS mean FROM `{schema.table}` WHERE {seq} IS NOT NULL"
        )
        row = cur.fetchone()
        stats["sequence_length"] = {
            "min": row["lo"], "max": row["hi"],
            "mean": round(float(row["mean"]), 1) if row["mean"] is not None else None,
        }
        cur.execute(
            f"SELECT COUNT(*) - COUNT(DISTINCT {seq}) AS dupes FROM `{schema.table}` "
            f"WHERE {seq} IS NOT NULL"
        )
        stats["duplicate_sequences"] = cur.fetchone()["dupes"]

        ec_col = schema.column("ec")
        if ec_col:
            ec = f"`{_check_identifier(ec_col, 'column')}`"
            cur.execute(
                f"SELECT COUNT(*) AS n FROM `{schema.table}` "
                f"WHERE {ec} IS NULL OR TRIM({ec}) = ''"
            )
            stats["missing_ec"] = cur.fetchone()["n"]
            cur.execute(
                f"SELECT COUNT(*) AS n FROM `{schema.table}` "
                f"WHERE {ec} LIKE '%;%' OR {ec} LIKE '%,%'"
            )
            stats["multi_ec_rows"] = cur.fetchone()["n"]
            cur.execute(
                f"SELECT {ec} AS ec, COUNT(*) AS n FROM `{schema.table}` "
                f"WHERE {ec} IS NOT NULL AND TRIM({ec}) <> '' "
                f"GROUP BY {ec} ORDER BY n DESC LIMIT 15"
            )
            stats["top_ec"] = [(r["ec"], r["n"]) for r in cur.fetchall()]

        src_col = schema.column("source")
        if src_col:
            src = f"`{_check_identifier(src_col, 'column')}`"
            cur.execute(
                f"SELECT {src} AS source, COUNT(*) AS n FROM `{schema.table}` "
                f"GROUP BY {src} ORDER BY n DESC LIMIT 25"
            )
            stats["sources"] = [(r["source"], r["n"]) for r in cur.fetchall()]

    return stats
