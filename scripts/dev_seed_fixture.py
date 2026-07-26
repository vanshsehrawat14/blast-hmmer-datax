#!/usr/bin/env python3
"""Create a local MySQL stand-in for the copied EnzymeX `enzymesdata` table.

DEVELOPMENT ONLY. This is not part of the deployment path and it is not a
substitute for the real copied database. It exists so that the export,
clustering and profile pipeline can be exercised end to end on a machine that
has no EnzymeX access — and so that the automated end-to-end test has
something to run against.

What it builds:

  * a table with the columns the EnzymeX handover notes document
    (description / sequence / EC / motif / active / binding / interpretation /
    source / modified / created), populated with real reviewed UniProtKB
    sequences for a set of EC numbers chosen because several of them are
    served by more than one unrelated protein family — superoxide dismutase
    (Cu/Zn, Mn/Fe and Ni enzymes) and carbonic anhydrase (α/β/γ classes) are
    the textbook cases, and they are exactly what the clustering step has to
    handle correctly;
  * a holdout set of sequences excluded from the table, used as positive
    queries that cannot trivially match themselves;
  * deliberately damaged rows — nulls, empties, nucleotide text, internal stop
    codons, fragments, duplicated sequences, missing and multi-valued EC — so
    the export's validation and skip reporting are exercised against dirt that
    resembles the real thing.

Writing requires a privileged account. The application's own account should be
SELECT-only; see docs/database.md.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from pathlib import Path

import pymysql
import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
UNIPROT = "https://rest.uniprot.org/uniprotkb/search"

# Well-populated ECs. The first two are polyphyletic on purpose.
EC_TARGETS = [
    "1.15.1.1",   # superoxide dismutase - Cu/Zn, Mn/Fe and Ni families
    "4.2.1.1",    # carbonic anhydrase - alpha, beta, gamma classes
    "1.1.1.1",    # alcohol dehydrogenase
    "3.2.1.1",    # alpha-amylase
    "3.4.21.4",   # trypsin
    "1.11.1.6",   # catalase
    "2.5.1.18",   # glutathione transferase
    "5.3.1.9",    # glucose-6-phosphate isomerase
    "6.1.1.1",    # tyrosine--tRNA ligase
    "2.7.1.1",    # hexokinase
    "3.1.1.7",    # acetylcholinesterase
    "1.1.1.37",   # malate dehydrogenase
]

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


def fetch_uniprot(limit_per_ec: int, verbose: bool = True) -> list[dict]:
    """Reviewed entries for each target EC, in accession order."""
    session = requests.Session()
    session.headers["User-Agent"] = "enzymex-blast-hmmer-test-fixture/0.2"
    rows: list[dict] = []

    for ec in EC_TARGETS:
        got, url = 0, UNIPROT
        params = {
            "query": f"(ec:{ec}) AND (reviewed:true)",
            "fields": "accession,protein_name,ec,sequence,organism_name,length",
            "format": "tsv",
            "size": "500",
        }
        while url and got < limit_per_ec:
            resp = session.get(url, params=params if url == UNIPROT else None, timeout=90)
            resp.raise_for_status()
            lines = resp.text.strip().split("\n")
            header = lines[0].split("\t")
            for line in lines[1:]:
                values = dict(zip(header, line.split("\t")))
                if not values.get("Sequence"):
                    continue
                rows.append({
                    "accession": values["Entry"],
                    "description": values.get("Protein names", "")[:400],
                    "sequence": values["Sequence"],
                    "ec": values.get("EC number", "") or ec,
                    "organism": values.get("Organism", ""),
                    "query_ec": ec,
                })
                got += 1
                if got >= limit_per_ec:
                    break
            url = resp.links.get("next", {}).get("url")
            params = None
            time.sleep(0.2)
        if verbose:
            print(f"[fixture] {ec}: {got} reviewed entries", file=sys.stderr)

    # Deduplicate across ECs (a bifunctional enzyme appears under several).
    by_acc: dict[str, dict] = {}
    for r in rows:
        if r["accession"] in by_acc:
            existing = by_acc[r["accession"]]["ec"]
            merged = sorted({*existing.split("; "), *r["ec"].split("; ")} - {""})
            by_acc[r["accession"]]["ec"] = "; ".join(merged)
        else:
            by_acc[r["accession"]] = r
    return [by_acc[a] for a in sorted(by_acc)]


def assign_sources(rows: list[dict]) -> None:
    """Spread rows over the source labels the real table mixes.

    All the sequences are genuinely Swiss-Prot; the labels simulate the
    provenance mix so grouping and reporting by source can be exercised.
    """
    rng = random.Random(20260725)
    weights = [("Swiss-Prot", 0.55), ("TrEMBL", 0.30), ("PDB", 0.09), ("KEGG", 0.06)]
    for r in rows:
        x, acc = rng.random(), 0.0
        for label, w in weights:
            acc += w
            if x <= acc:
                r["source"] = label
                break
        else:
            r["source"] = "Swiss-Prot"


def damaged_rows(clean: list[dict]) -> list[dict]:
    """Rows that must be rejected or merged by the export, one per reason."""
    donor = clean[0]["sequence"]
    return [
        {"accession": "BAD_NULL", "description": "null sequence", "sequence": None,
         "ec": "1.1.1.1", "source": "TrEMBL"},
        {"accession": "BAD_EMPTY", "description": "empty sequence", "sequence": "   ",
         "ec": "1.1.1.1", "source": "TrEMBL"},
        {"accession": "BAD_DNA", "description": "nucleotide text in a protein column",
         "sequence": "ATGGCGTAGCTAGCTAGCATCGATCGATCGATCGTAGCTAGCTAGCTAGCATCGATCGAT" * 2,
         "ec": "1.1.1.1", "source": "KEGG"},
        {"accession": "BAD_STOP", "description": "internal stop codon",
         "sequence": donor[:80] + "*" + donor[80:160], "ec": "1.1.1.1", "source": "TrEMBL"},
        {"accession": "BAD_SHORT", "description": "peptide fragment",
         "sequence": donor[:12], "ec": "1.1.1.1", "source": "PDB"},
        {"accession": "BAD_AMBIG", "description": "mostly ambiguous residues",
         "sequence": "X" * 200 + donor[:40], "ec": "1.1.1.1", "source": "TrEMBL"},
        {"accession": "DUP_SEQ", "description": "exact duplicate of another row",
         "sequence": donor, "ec": "1.1.1.1", "source": "PDB"},
        {"accession": "NO_EC", "description": "no EC annotation",
         "sequence": clean[1]["sequence"], "ec": None, "source": "TrEMBL"},
        {"accession": "WEIRD_EC", "description": "unparseable EC text",
         "sequence": clean[2]["sequence"], "ec": "not an ec number", "source": "KEGG"},
        {"accession": "WHITESPACE", "description": "sequence with FASTA wrapping and digits",
         "sequence": "  1 " + clean[3]["sequence"][:60] + "\n 61 " + clean[3]["sequence"][60:180],
         "ec": "1.1.1.1; 1.1.1.71", "source": "Swiss-Prot"},
    ]


def write_holdout(rows: list[dict], path: Path, every: int) -> list[dict]:
    """Remove every Nth entry from the table and keep it as a query."""
    holdout = [r for i, r in enumerate(rows) if i % every == every - 1]
    kept = [r for i, r in enumerate(rows) if i % every != every - 1]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="ascii", newline="\n") as fh:
        for r in holdout:
            fh.write(f">{r['accession']} EC={r['ec']} {r['organism'][:60]}\n")
            for i in range(0, len(r["sequence"]), 60):
                fh.write(r["sequence"][i:i + 60] + "\n")
    return kept


def load(conn, rows: list[dict], batch: int = 500) -> int:
    with conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS enzymesdata")
        cur.execute(DDL)
        sql = ("INSERT INTO enzymesdata "
               "(accession, description, sequence, ec, motif, active, binding, "
               "interpretation, source) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)")
        payload = [
            (r.get("accession"), r.get("description"), r.get("sequence"), r.get("ec"),
             r.get("motif"), r.get("active"), r.get("binding"),
             r.get("interpretation"), r.get("source", "Swiss-Prot"))
            for r in rows
        ]
        for i in range(0, len(payload), batch):
            cur.executemany(sql, payload[i:i + batch])
    conn.commit()
    return len(payload)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default=os.environ.get("FIXTURE_DB_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("FIXTURE_DB_PORT", "3306")))
    ap.add_argument("--user", default=os.environ.get("FIXTURE_DB_USER", "root"))
    ap.add_argument("--password", default=os.environ.get("FIXTURE_DB_PASSWORD", ""))
    ap.add_argument("--database", default=os.environ.get("FIXTURE_DB_NAME", "enzymex_copy"))
    ap.add_argument("--per-ec", type=int, default=400, help="max reviewed entries per EC")
    ap.add_argument("--holdout-every", type=int, default=25,
                    help="hold out every Nth entry as a query sequence")
    ap.add_argument("--cache", type=Path, default=REPO_ROOT / "var" / "fixture" / "uniprot.json")
    ap.add_argument("--holdout-out", type=Path,
                    default=REPO_ROOT / "var" / "fixture" / "holdout_queries.fasta")
    args = ap.parse_args()

    if args.cache.exists():
        print(f"[fixture] reusing cached download {args.cache}", file=sys.stderr)
        rows = json.loads(args.cache.read_text(encoding="utf-8"))
    else:
        rows = fetch_uniprot(args.per_ec)
        args.cache.parent.mkdir(parents=True, exist_ok=True)
        args.cache.write_text(json.dumps(rows), encoding="utf-8")

    assign_sources(rows)
    kept = write_holdout(rows, args.holdout_out, args.holdout_every)
    all_rows = kept + damaged_rows(kept)

    conn = pymysql.connect(host=args.host, port=args.port, user=args.user,
                           password=args.password, database=args.database,
                           charset="utf8mb4")
    try:
        n = load(conn, all_rows)
    finally:
        conn.close()

    print(json.dumps({
        "downloaded": len(rows),
        "inserted": n,
        "clean_rows": len(kept),
        "damaged_rows": len(all_rows) - len(kept),
        "holdout_queries": len(rows) - len(kept),
        "holdout_fasta": str(args.holdout_out),
        "database": f"{args.user}@{args.host}:{args.port}/{args.database}",
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
