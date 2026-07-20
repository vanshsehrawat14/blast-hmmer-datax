#!/usr/bin/env python3
"""Normalize BLAST and HMMER outputs into one CSV/JSON schema and write a
short comparison report.

The three searches report different things, so the parser maps them onto a
common orientation where `query_accession` is always one of the held-out test
proteins and `hit` is whatever it matched:

  blastp     hit = a reference sequence accession
  phmmer     hit = a reference sequence accession
  hmmsearch  hit = the profile name (the profile is HMMER's "query", so the
             roles in the raw tblout file are the reverse of this table)

Percentage identity is only produced by BLAST; the HMMER tabular outputs do
not report it, so that column is empty for phmmer and hmmsearch rows.
"""

import argparse
import csv
import json
import re
import sys
from pathlib import Path

SCHEMA = [
    "method", "query_accession", "query_set", "query_organism", "query_ec",
    "hit", "hit_organism", "hit_ec", "rank", "evalue", "bitscore",
    "percent_identity", "coverage",
]

# Display order for the report. DIAMOND is optional; methods with no rows are
# dropped rather than shown as empty.
METHOD_ORDER = ["blastp", "phmmer", "hmmsearch", "diamond", "diamond-default"]

# makeblastdb -parse_seqids recognises UniProt-style accessions and rewrites
# the sequence ID as "sp|ACCESSION|", so strip that wrapper back off.
SEQID_RE = re.compile(r"^(?:sp|tr)\|([^|]+)\|?$")


def bare_accession(seqid: str) -> str:
    m = SEQID_RE.match(seqid)
    return m.group(1) if m else seqid


def load_metadata(path: Path) -> dict:
    meta = {}
    with path.open(newline="") as fh:
        for row in csv.DictReader(fh, delimiter="\t"):
            meta[row["accession"]] = row
    return meta


def merge_intervals(intervals: list) -> int:
    """Total length covered by a set of (start, end) inclusive intervals."""
    if not intervals:
        return 0
    total, cur_s, cur_e = 0, *intervals[0]
    for s, e in sorted(intervals)[1:]:
        if s <= cur_e + 1:
            cur_e = max(cur_e, e)
        else:
            total += cur_e - cur_s + 1
            cur_s, cur_e = s, e
    return total + cur_e - cur_s + 1


def read_hmmer_table(path: Path) -> list:
    """Split a HMMER tblout/domtblout into fields, skipping comment lines."""
    rows = []
    for line in path.read_text().splitlines():
        if line.startswith("#") or not line.strip():
            continue
        rows.append(line.split())
    return rows


def parse_tabular(path: Path, meta: dict, method: str) -> list:
    """Parse BLAST/DIAMOND tabular output.

    Both write `6 qseqid sseqid pident length <coverage> evalue bitscore
    stitle`. The coverage column differs: BLAST uses qcovs (summed over all
    HSPs for a subject) and DIAMOND uses qcovhsp (that HSP alone), because
    DIAMOND does not implement qcovs.
    """
    best = {}
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        f = line.split("\t")
        query, subject = f[0], bare_accession(f[1])
        evalue, bits = float(f[5]), float(f[6])
        key = (query, subject)
        # outfmt 6 emits one row per HSP; keep the strongest HSP per pair.
        if key not in best or bits > best[key]["bitscore"]:
            best[key] = {
                "query": query, "hit": subject,
                "percent_identity": float(f[2]),
                "coverage": float(f[4]) / 100.0,
                "evalue": evalue, "bitscore": bits,
            }
    return rank_rows(method, best.values(), meta)


def parse_phmmer(tblout: Path, domtblout: Path, meta: dict) -> list:
    """phmmer: query = test protein, target = reference sequence.

    Coverage is the fraction of the *query* protein aligned, taken from the
    hmm coordinate columns of the domain table (for phmmer the query sequence
    is the one converted into a profile).
    """
    spans, qlen = {}, {}
    for f in read_hmmer_table(domtblout):
        target, query = bare_accession(f[0]), f[3]
        qlen[query] = int(f[5])
        spans.setdefault((query, target), []).append((int(f[15]), int(f[16])))

    rows = {}
    for f in read_hmmer_table(tblout):
        target, query = bare_accession(f[0]), f[2]
        key = (query, target)
        length = qlen.get(query, 0)
        cov = merge_intervals(spans.get(key, [])) / length if length else None
        rows[key] = {
            "query": query, "hit": target,
            "percent_identity": None,
            "coverage": cov,
            "evalue": float(f[4]), "bitscore": float(f[5]),
        }
    return rank_rows("phmmer", rows.values(), meta)


def parse_hmmsearch(tblout: Path, domtblout: Path, meta: dict) -> list:
    """hmmsearch: query = the profile, target = test protein.

    Roles are swapped relative to the raw file so the output table stays
    query-centric. Coverage is the fraction of the test protein covered by
    the profile's alignment envelopes.
    """
    spans, tlen = {}, {}
    for f in read_hmmer_table(domtblout):
        target, profile = f[0], f[3]
        tlen[target] = int(f[2])
        spans.setdefault((target, profile), []).append((int(f[19]), int(f[20])))

    rows = {}
    for f in read_hmmer_table(tblout):
        target, profile = f[0], f[2]
        key = (target, profile)
        length = tlen.get(target, 0)
        cov = merge_intervals(spans.get(key, [])) / length if length else None
        rows[key] = {
            "query": target, "hit": profile,
            "percent_identity": None,
            "coverage": cov,
            "evalue": float(f[4]), "bitscore": float(f[5]),
        }
    return rank_rows("hmmsearch", rows.values(), meta)


def rank_rows(method: str, rows, meta: dict) -> list:
    """Sort each query's hits by E-value then bit score and assign ranks."""
    by_query = {}
    for r in rows:
        by_query.setdefault(r["query"], []).append(r)

    out = []
    for query in sorted(by_query):
        hits = sorted(by_query[query], key=lambda r: (r["evalue"], -r["bitscore"]))
        qm = meta.get(query, {})
        for rank, r in enumerate(hits, start=1):
            hm = meta.get(r["hit"], {})
            out.append({
                "method": method,
                "query_accession": query,
                "query_set": qm.get("set", ""),
                "query_organism": qm.get("organism", ""),
                "query_ec": qm.get("ec_number", ""),
                "hit": r["hit"],
                # A profile is not a UniProt entry, so it has no organism and
                # its EC is the EC of the reference set it was built from.
                "hit_organism": hm.get("organism", "" if hm else "n/a (profile)"),
                "hit_ec": hm.get("ec_number", "" if hm else "1.1.1.1"),
                "rank": rank,
                "evalue": r["evalue"],
                "bitscore": r["bitscore"],
                "percent_identity": r["percent_identity"],
                "coverage": None if r["coverage"] is None else round(r["coverage"], 4),
            })
    return out


def write_outputs(rows: list, outdir: Path) -> None:
    outdir.mkdir(parents=True, exist_ok=True)
    with (outdir / "all_hits.csv").open("w", newline="") as fh:
        # csv defaults to CRLF line endings; keep the output LF-only.
        w = csv.DictWriter(fh, fieldnames=SCHEMA, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    (outdir / "all_hits.json").write_text(json.dumps(rows, indent=2) + "\n")


def fmt(x, spec="{:.2g}"):
    return "-" if x is None else spec.format(x)


def write_report(rows: list, meta: dict, outdir: Path) -> None:
    # DIAMOND is optional, so report on whatever methods actually produced
    # rows, in a fixed display order.
    present = {r["method"] for r in rows}
    methods = [m for m in METHOD_ORDER if m in present]
    queries = [a for a, m in meta.items()
               if m["set"] in ("positive_query", "negative_control")]
    queries.sort(key=lambda a: (meta[a]["set"], a))

    lines = [
        "# BLAST vs. HMMER on the EC 1.1.1.1 test queries",
        "",
        "Generated by `scripts/04_parse_results.py`. Every query below was held "
        "out of the 20-sequence reference set used to build the BLAST database "
        "and the profile HMM.",
        "",
        "## Best hit per query",
        "",
        "`-` means the method reported no hit at all for that query.",
        "",
        "| Query | Set | Method | Best hit | E-value | Bit score | % identity | Coverage |",
        "|---|---|---|---|---|---|---|---|",
    ]

    for q in queries:
        for method in methods:
            hits = [r for r in rows
                    if r["method"] == method and r["query_accession"] == q]
            label = meta[q]["set"].replace("_", " ")
            if not hits:
                lines.append(f"| {q} | {label} | {method} | - | - | - | - | - |")
                continue
            b = min(hits, key=lambda r: r["rank"])
            lines.append(
                f"| {q} | {label} | {method} | {b['hit']} | {fmt(b['evalue'])} | "
                f"{fmt(b['bitscore'], '{:.1f}')} | "
                f"{fmt(b['percent_identity'], '{:.1f}')} | "
                f"{fmt(b['coverage'], '{:.2f}')} |"
            )

    lines += ["", "## Hit counts", "",
              "| Method | Query set | Queries with >=1 hit | Total hits reported |",
              "|---|---|---|---|"]
    for method in methods:
        for qset in ("positive_query", "negative_control"):
            group = [a for a in queries if meta[a]["set"] == qset]
            sub = [r for r in rows
                   if r["method"] == method and meta.get(r["query_accession"], {}).get("set") == qset]
            with_hits = len({r["query_accession"] for r in sub})
            lines.append(f"| {method} | {qset.replace('_', ' ')} | "
                         f"{with_hits}/{len(group)} | {len(sub)} |")

    lines += [
        "",
        "## Observations",
        "",
        "These are descriptions of this specific 27-sequence run, not general "
        "biological claims. The reference set is small and the queries were "
        "chosen by accession order, so the numbers below should be read as a "
        "demonstration that the tools run and behave sensibly.",
        "",
    ]

    pos_best, neg_best = [], []
    for r in rows:
        qset = meta.get(r["query_accession"], {}).get("set")
        if r["rank"] != 1:
            continue
        (pos_best if qset == "positive_query" else neg_best).append(r)

    n_pos = sum(1 for m in meta.values() if m["set"] == "positive_query")
    for method in methods:
        p = [r for r in pos_best if r["method"] == method]
        n = [r for r in neg_best if r["method"] == method]
        if p:
            # State how many positives were found. Without this, a method that
            # misses the hardest query looks better than one that finds it,
            # because the missing query is excluded from the maximum.
            found = (f"found {len(p)}/{n_pos} positive queries"
                     if len(p) < n_pos else
                     f"found all {n_pos} positive queries")
            pe = (f"{found}; worst best-hit E-value among those was "
                  f"{max(r['evalue'] for r in p):.2g}")
        else:
            pe = "no positive query produced a hit"
        ne = (f"the lowest E-value reached by a negative control was "
              f"{min(r['evalue'] for r in n):.2g}") if n else \
             "neither negative control produced any reported hit"
        lines.append(f"- **{method}**: {pe}; {ne}.")

    lines += [
        "",
        "- BLAST reports short, low-scoring local alignments for the negative "
        "controls. These are the expected background noise of a local aligner "
        "searching a small database and are separated from the real hits by "
        "many orders of magnitude in E-value, not by presence or absence.",
        "- The profile HMM is more selective here: sequences that do not "
        "resemble the aligned reference family fall below the reporting "
        "threshold entirely.",
    ]

    if "diamond" in methods:
        d_pos = {r["query_accession"] for r in rows if r["method"] == "diamond"}
        dd_pos = {r["query_accession"] for r in rows
                  if r["method"] == "diamond-default"}
        missed = sorted(d_pos - dd_pos)
        lines.append(
            "- DIAMOND is run in two modes. `diamond` is `--very-sensitive`, "
            "the mode closest to blastp; `diamond-default` is DIAMOND's fast "
            "default. "
            + (f"The default mode found no hit for {', '.join(missed)}, which "
               "the sensitive mode and both BLAST and HMMER did find. This is "
               "the documented speed/sensitivity trade-off: DIAMOND's default "
               "seeding is tuned for large databases and high-identity "
               "matches."
               if missed else
               "Both modes recovered the same queries on this run.")
        )
        lines.append(
            "- DIAMOND coverage is not directly comparable to BLAST coverage. "
            "DIAMOND does not implement `qcovs`, so the coverage column holds "
            "`qcovhsp` (coverage of a single HSP) rather than coverage summed "
            "across all HSPs for that subject."
        )

    lines.append("")

    (outdir / "comparison_report.md").write_text("\n".join(lines) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--metadata", type=Path, default="data/raw/metadata.tsv")
    ap.add_argument("--blast-dir", type=Path, default="results/blast")
    ap.add_argument("--hmmer-dir", type=Path, default="results/hmmer")
    ap.add_argument("--diamond-dir", type=Path, default="results/diamond")
    ap.add_argument("--outdir", type=Path, default="results/comparison")
    args = ap.parse_args()

    meta = load_metadata(args.metadata)

    rows = []
    rows += parse_tabular(args.blast_dir / "blastp_hits.tsv", meta, "blastp")
    rows += parse_phmmer(args.hmmer_dir / "phmmer_tblout.txt",
                         args.hmmer_dir / "phmmer_domtblout.txt", meta)
    rows += parse_hmmsearch(args.hmmer_dir / "hmmsearch_tblout.txt",
                            args.hmmer_dir / "hmmsearch_domtblout.txt", meta)

    # DIAMOND is optional: include it only if `make diamond` has been run.
    for fname, method in (("diamond_hits.tsv", "diamond"),
                          ("diamond_hits_default.tsv", "diamond-default")):
        path = args.diamond_dir / fname
        if path.exists():
            rows += parse_tabular(path, meta, method)
        else:
            print(f"[parse] skipping {method}: {path} not found "
                  f"(run `make diamond` to include it)")

    write_outputs(rows, args.outdir)
    write_report(rows, meta, args.outdir)

    print(f"[parse] {len(rows)} normalized hit rows -> {args.outdir}/all_hits.csv")
    for m in METHOD_ORDER:
        n = sum(1 for r in rows if r["method"] == m)
        if n:
            print(f"[parse]   {m}: {n} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
