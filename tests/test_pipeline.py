#!/usr/bin/env python3
"""Checks that the pipeline produced the files and counts it should have.

Run after `make all`. Exits non-zero on the first failed check so it can be
used as a build gate.
"""

import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FAILURES = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label}{' - ' + detail if detail else ''}")
        FAILURES.append(label)


def count_fasta(path: Path) -> int:
    return sum(1 for line in path.read_text().splitlines() if line.startswith(">"))


def main() -> int:
    print("Input data")
    ref = ROOT / "data/raw/reference_ec_1_1_1_1.fasta"
    pos = ROOT / "data/raw/queries_positive.fasta"
    neg = ROOT / "data/raw/queries_negative.fasta"
    meta = ROOT / "data/raw/metadata.tsv"

    for p in (ref, pos, neg, meta):
        check(f"{p.relative_to(ROOT)} exists", p.exists())
    if FAILURES:
        return 1

    check("reference set has 20 sequences", count_fasta(ref) == 20,
          f"got {count_fasta(ref)}")
    check("positive query set has 5 sequences", count_fasta(pos) == 5,
          f"got {count_fasta(pos)}")
    check("negative control set has 2 sequences", count_fasta(neg) == 2,
          f"got {count_fasta(neg)}")

    rows = list(csv.DictReader(meta.open(newline=""), delimiter="\t"))
    check("metadata has 27 rows", len(rows) == 27, f"got {len(rows)}")
    check("all metadata entries are reviewed",
          all(r["reviewed"] == "reviewed" for r in rows))
    check("every reference entry is annotated EC 1.1.1.1",
          all("1.1.1.1" in r["ec_number"].split(";")
              for r in rows if r["set"] == "reference"))
    check("no negative control is annotated EC 1.1.1.1",
          all("1.1.1.1" not in r["ec_number"].split(";")
              for r in rows if r["set"] == "negative_control"))

    accs = [r["accession"] for r in rows]
    check("no duplicate accessions across all sets", len(accs) == len(set(accs)))

    print("\nTool outputs")
    expected = [
        "results/blast/blastp_pairwise.txt",
        "results/blast/blastp_hits.tsv",
        "results/hmmer/phmmer_full.txt",
        "results/hmmer/phmmer_tblout.txt",
        "results/hmmer/phmmer_domtblout.txt",
        "results/hmmer/hmmsearch_full.txt",
        "results/hmmer/hmmsearch_tblout.txt",
        "results/hmmer/hmmsearch_domtblout.txt",
        "data/processed/reference_aligned.afa",
        "data/processed/ec_1_1_1_1.hmm",
        "results/comparison/all_hits.csv",
        "results/comparison/all_hits.json",
        "results/comparison/comparison_report.md",
    ]
    for rel in expected:
        p = ROOT / rel
        check(f"{rel} exists and is non-empty",
              p.exists() and p.stat().st_size > 0)

    aln = ROOT / "data/processed/reference_aligned.afa"
    if aln.exists():
        check("alignment contains all 20 reference sequences",
              count_fasta(aln) == 20, f"got {count_fasta(aln)}")

    print("\nNormalized results")
    hits_path = ROOT / "results/comparison/all_hits.csv"
    if not hits_path.exists():
        return 1
    hits = list(csv.DictReader(hits_path.open(newline="")))
    check("normalized table is non-empty", len(hits) > 0)

    methods = {h["method"] for h in hits}
    for m in ("blastp", "phmmer", "hmmsearch"):
        check(f"{m} produced normalized rows", m in methods)

    # DIAMOND is optional. Only assert on it when `make diamond` has run.
    diamond_ran = "diamond" in methods
    if diamond_ran:
        print("  ..    DIAMOND results present, including in checks")
    else:
        print("  ..    DIAMOND results absent, skipping (optional step)")

    positives = {r["accession"] for r in rows if r["set"] == "positive_query"}
    sensitive = ["blastp", "phmmer", "hmmsearch"] + (["diamond"] if diamond_ran else [])
    for m in sensitive:
        hit_queries = {h["query_accession"] for h in hits if h["method"] == m}
        missing = positives - hit_queries
        check(f"{m} found a hit for all 5 positive queries", not missing,
              f"missing {sorted(missing)}")

    if diamond_ran:
        # Documented behaviour, asserted so a DIAMOND upgrade that silently
        # changes default-mode sensitivity is not missed: the fast default
        # recovers fewer positive queries than --very-sensitive.
        d = {h["query_accession"] for h in hits if h["method"] == "diamond"}
        dd = {h["query_accession"] for h in hits
              if h["method"] == "diamond-default"}
        check("diamond-default finds no more than --very-sensitive",
              dd <= d, f"default found extra {sorted(dd - d)}")

    # Every positive query should beat every negative control by a wide margin.
    negatives = {r["accession"] for r in rows if r["set"] == "negative_control"}
    for m in sensitive:
        best_pos = [float(h["evalue"]) for h in hits
                    if h["method"] == m and h["query_accession"] in positives
                    and h["rank"] == "1"]
        best_neg = [float(h["evalue"]) for h in hits
                    if h["method"] == m and h["query_accession"] in negatives
                    and h["rank"] == "1"]
        if not best_neg:
            check(f"{m} reported no hits for negative controls", True)
        else:
            check(f"{m} separates positives from negatives by >1e10",
                  max(best_pos) * 1e10 < min(best_neg),
                  f"worst positive {max(best_pos):.2g} vs "
                  f"best negative {min(best_neg):.2g}")

    js = json.loads((ROOT / "results/comparison/all_hits.json").read_text())
    check("CSV and JSON row counts agree", len(js) == len(hits))

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) failed")
        return 1
    print("All checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
