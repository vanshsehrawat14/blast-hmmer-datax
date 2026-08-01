#!/usr/bin/env python3
"""Load the public Swiss-Prot/PDB sequence set into an `enzymesdata` table.

The development fixture built by `dev_seed_fixture.py` assigns synthetic source
labels to reviewed Swiss-Prot sequences, so it exercises the source-selection
and deduplication code without containing a single PDB-derived record. This
script replaces that fixture with real data from two public `datax-lab`
repositories, so the same pipeline can be run against genuine PDB provenance:

  * `HIT-EC/data/SwissProt_PDB_2022.csv` — Sequence, SwissProt_Accession,
    PDB_ID. 273,500 rows, no EC column.
  * `IDF-EC/data/dataset.csv` — Sequence, EC_List. Supplies the EC annotation,
    joined on the exact sequence.

Both are Git LFS files; fetch them from `media.githubusercontent.com`, not the
normal blob URL, or you get a 134-byte pointer.

Source labels come from which identifier the row carries. A row with a
Swiss-Prot accession is `swissprot`; a row with only a PDB id is `pdb`. The
5,198 rows carrying both are one record with two identifiers, not two rows to
deduplicate, so they are loaded once as `swissprot` with the PDB id retained in
the description. Inventing a second row would manufacture duplicate-merge
statistics that the source data does not support.

Rows are inserted in file order, so the auto-increment key is stable across
reloads of the same input and the exported FASTA stays byte-identical.

Writing needs a privileged account. The application's own account is
SELECT-only; see docs/database.md. This loads into `enzymex_real` by default so
that an existing fixture build in `enzymex_copy` is left intact.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import sys
from pathlib import Path

import pymysql

SWISSPROT_PDB_URL = (
    "https://media.githubusercontent.com/media/datax-lab/HIT-EC/main/"
    "data/SwissProt_PDB_2022.csv"
)
EC_URL = (
    "https://media.githubusercontent.com/media/datax-lab/IDF-EC/main/"
    "data/dataset.csv"
)

DDL = """
CREATE TABLE IF NOT EXISTS enzymesdata (
    id             INT AUTO_INCREMENT PRIMARY KEY,
    accession      VARCHAR(32),
    description    TEXT,
    sequence       LONGTEXT,
    ec             VARCHAR(255),
    motif          TEXT,
    active         TEXT,
    binding        TEXT,
    interpretation TEXT,
    source         VARCHAR(64),
    modified       DATETIME DEFAULT CURRENT_TIMESTAMP,
    created        DATETIME DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

INSERT = (
    "INSERT INTO enzymesdata (accession, description, sequence, ec, source) "
    "VALUES (%s, %s, %s, %s, %s)"
)


def digest(sequence: str) -> bytes:
    """Join key. The raw sequence is ~350 bytes; a digest is 32."""
    return hashlib.sha256(sequence.upper().encode("utf-8")).digest()


def load_ec_labels(path: Path) -> dict[bytes, str]:
    labels: dict[bytes, str] = {}
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames != ["Sequence", "EC_List"]:
            raise SystemExit(f"{path.name}: unexpected columns {reader.fieldnames}")
        for row in reader:
            sequence = (row["Sequence"] or "").strip()
            ec = (row["EC_List"] or "").strip()
            if sequence and ec:
                labels[digest(sequence)] = ec
    return labels


def iter_rows(path: Path, labels: dict[bytes, str]):
    """Yield insert tuples in file order, plus a running tally by source."""
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        expected = ["Sequence", "SwissProt_Accession", "PDB_ID"]
        if reader.fieldnames != expected:
            raise SystemExit(f"{path.name}: unexpected columns {reader.fieldnames}")
        for row in reader:
            sequence = (row["Sequence"] or "").strip()
            accession = (row["SwissProt_Accession"] or "").strip()
            pdb_id = (row["PDB_ID"] or "").strip()
            if not sequence or not (accession or pdb_id):
                continue
            source = "swissprot" if accession else "pdb"
            description = f"PDB:{pdb_id}" if pdb_id else None
            yield (
                (accession or pdb_id)[:32],
                description,
                sequence,
                labels.get(digest(sequence)),
                source,
            )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sequences", type=Path, required=True,
                        help=f"SwissProt_PDB_2022.csv, from {SWISSPROT_PDB_URL}")
    parser.add_argument("--ec", type=Path, required=True,
                        help=f"IDF-EC dataset.csv, from {EC_URL}")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=3307)
    parser.add_argument("--user", default="root")
    parser.add_argument("--password", default="")
    parser.add_argument("--database", default="enzymex_real")
    parser.add_argument("--grant-to", default="enzymex_ro",
                        help="account given SELECT on the loaded database")
    parser.add_argument("--batch", type=int, default=5_000)
    parser.add_argument("--replace", action="store_true",
                        help="drop an existing enzymesdata table first")
    args = parser.parse_args()

    for path in (args.sequences, args.ec):
        if not path.is_file():
            raise SystemExit(f"not found: {path}")

    print(f"reading EC labels from {args.ec.name} …", flush=True)
    labels = load_ec_labels(args.ec)
    print(f"  {len(labels):,} sequences carry an EC annotation")

    conn = pymysql.connect(
        host=args.host, port=args.port, user=args.user,
        password=args.password, charset="utf8mb4", autocommit=False,
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"CREATE DATABASE IF NOT EXISTS `{args.database}` "
                "DEFAULT CHARACTER SET utf8mb4"
            )
            cur.execute(f"USE `{args.database}`")
            if args.replace:
                cur.execute("DROP TABLE IF EXISTS enzymesdata")
            cur.execute(DDL)
            cur.execute("SELECT COUNT(*) FROM enzymesdata")
            existing = cur.fetchone()[0]
            if existing:
                raise SystemExit(
                    f"{args.database}.enzymesdata already holds {existing:,} rows. "
                    "Re-run with --replace to rebuild it."
                )
        conn.commit()

        print(f"loading {args.sequences.name} into {args.database}.enzymesdata …",
              flush=True)
        batch: list[tuple] = []
        counts = {"swissprot": 0, "pdb": 0}
        with_ec = total = 0
        with conn.cursor() as cur:
            for record in iter_rows(args.sequences, labels):
                batch.append(record)
                counts[record[4]] += 1
                with_ec += record[3] is not None
                total += 1
                if len(batch) >= args.batch:
                    cur.executemany(INSERT, batch)
                    conn.commit()
                    batch.clear()
                    print(f"  {total:,} rows", flush=True)
            if batch:
                cur.executemany(INSERT, batch)
                conn.commit()

        with conn.cursor() as cur:
            cur.execute(
                f"GRANT SELECT ON `{args.database}`.* TO %s@'%%'", (args.grant_to,)
            )
            cur.execute("FLUSH PRIVILEGES")
        conn.commit()
    finally:
        conn.close()

    print()
    print(f"loaded {total:,} rows into {args.database}.enzymesdata")
    print(f"  swissprot   {counts['swissprot']:,}")
    print(f"  pdb         {counts['pdb']:,}")
    print(f"  with EC     {with_ec:,} ({with_ec / total:.1%})")
    print()
    print("build against it with:")
    print(f"  ENZYMEX_DB_NAME={args.database} enzymex-refbuild all --skip-profiles")
    return 0


if __name__ == "__main__":
    sys.exit(main())
