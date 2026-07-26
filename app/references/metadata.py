"""SQLite metadata store: the only thing the web app reads at request time.

The copied MySQL database is touched exactly once, during the offline
reference build. Everything a search needs afterwards — description, EC,
source, provenance back to the original `enzymesdata` row — is written into
this file alongside the BLAST and HMMER artifacts.

Three reasons for the indirection rather than querying MySQL per request:

  * a user request can never reach the copied database, so a misconfigured
    credential cannot become a live query path;
  * metadata and search indexes are versioned together under one build id, so
    a hit can never resolve against metadata from a different build;
  * lookups are a keyed read of a local file, which keeps the request path
    free of network latency and connection pooling.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS reference (
    ref_id            TEXT PRIMARY KEY,
    source_pk         TEXT,
    description       TEXT,
    ec                TEXT,
    source            TEXT,
    length            INTEGER NOT NULL,
    sequence_sha256   TEXT NOT NULL,
    motif             TEXT,
    active_site       TEXT,
    binding_site      TEXT,
    interpretation    TEXT
);
CREATE INDEX IF NOT EXISTS reference_ec_idx ON reference(ec);
CREATE INDEX IF NOT EXISTS reference_sha_idx ON reference(sequence_sha256);

-- Rows from enzymesdata whose sequence was identical to an exported
-- reference. Kept so a hit can still be traced to every database row it
-- represents, which dedup would otherwise destroy.
CREATE TABLE IF NOT EXISTS reference_duplicate (
    ref_id            TEXT NOT NULL,
    source_pk         TEXT NOT NULL,
    description       TEXT,
    ec                TEXT,
    source            TEXT
);
CREATE INDEX IF NOT EXISTS reference_duplicate_idx ON reference_duplicate(ref_id);

CREATE TABLE IF NOT EXISTS profile (
    family_id              TEXT PRIMARY KEY,
    members                INTEGER NOT NULL,
    consensus_ec           TEXT,
    ec_purity              REAL,
    ec_distribution        TEXT,
    median_length          INTEGER,
    match_states           INTEGER,
    mean_pairwise_identity REAL,
    representative_ref_id  TEXT,
    description            TEXT
);

CREATE TABLE IF NOT EXISTS build_meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


@dataclass(frozen=True)
class ReferenceMeta:
    ref_id: str
    description: str | None
    ec: str | None
    source: str | None
    length: int
    source_pk: str | None = None
    duplicate_count: int = 0


@dataclass(frozen=True)
class ProfileMeta:
    family_id: str
    members: int
    consensus_ec: str | None
    ec_purity: float | None
    ec_distribution: dict[str, int]
    median_length: int | None
    match_states: int | None
    mean_pairwise_identity: float | None
    representative_ref_id: str | None
    description: str | None


def connect_write(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    return conn


def connect_read(path: Path) -> sqlite3.Connection:
    """Open read-only. A bug in a request handler cannot mutate the build."""
    if not path.exists():
        raise FileNotFoundError(path)
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


class MetadataStore:
    """Read-side accessor. Cheap enough to construct per request."""

    def __init__(self, path: Path):
        self.path = path
        self._conn = connect_read(path)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "MetadataStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def build_meta(self) -> dict[str, str]:
        return {r["key"]: r["value"] for r in self._conn.execute("SELECT key, value FROM build_meta")}

    def reference_count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM reference").fetchone()[0]

    def profile_count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM profile").fetchone()[0]

    def references(self, ref_ids: list[str]) -> dict[str, ReferenceMeta]:
        """Batch lookup. Chunked so a large hit list cannot exceed SQLite's
        999-parameter limit."""
        out: dict[str, ReferenceMeta] = {}
        unique = list(dict.fromkeys(ref_ids))
        for i in range(0, len(unique), 500):
            chunk = unique[i:i + 500]
            q = (
                "SELECT r.ref_id, r.description, r.ec, r.source, r.length, r.source_pk, "
                "  (SELECT COUNT(*) FROM reference_duplicate d WHERE d.ref_id = r.ref_id) AS dupes "
                f"FROM reference r WHERE r.ref_id IN ({','.join('?' * len(chunk))})"
            )
            for row in self._conn.execute(q, chunk):
                out[row["ref_id"]] = ReferenceMeta(
                    ref_id=row["ref_id"], description=row["description"], ec=row["ec"],
                    source=row["source"], length=row["length"], source_pk=row["source_pk"],
                    duplicate_count=row["dupes"],
                )
        return out

    def profiles(self, family_ids: list[str]) -> dict[str, ProfileMeta]:
        out: dict[str, ProfileMeta] = {}
        unique = list(dict.fromkeys(family_ids))
        for i in range(0, len(unique), 500):
            chunk = unique[i:i + 500]
            q = ("SELECT * FROM profile WHERE family_id IN "
                 f"({','.join('?' * len(chunk))})")
            for row in self._conn.execute(q, chunk):
                out[row["family_id"]] = ProfileMeta(
                    family_id=row["family_id"], members=row["members"],
                    consensus_ec=row["consensus_ec"], ec_purity=row["ec_purity"],
                    ec_distribution=json.loads(row["ec_distribution"] or "{}"),
                    median_length=row["median_length"], match_states=row["match_states"],
                    mean_pairwise_identity=row["mean_pairwise_identity"],
                    representative_ref_id=row["representative_ref_id"],
                    description=row["description"],
                )
        return out
