#!/usr/bin/env python3
"""Download EC 1.1.1.1 protein sequences from the UniProt REST API.

Produces three disjoint sequence sets:

  reference  20 reviewed EC 1.1.1.1 sequences -> BLAST database / HMM profile
  positive    5 reviewed EC 1.1.1.1 sequences -> held-out queries, expected to hit
  negative    2 reviewed sequences with a different EC -> expected not to hit

Selection is deterministic: results are sorted by accession and the sets are
taken in order, so re-running the script on the same UniProt release yields the
same sequences. The retrieval date is recorded in the metadata file.
"""

import argparse
import csv
import json
import re
import sys
import time
from datetime import date, timezone, datetime
from pathlib import Path

import requests

API = "https://rest.uniprot.org/uniprotkb/search"

# Negative controls: EC numbers from other top-level enzyme classes.
# 3.2.1.1 = alpha-amylase (hydrolase), 2.7.1.1 = hexokinase (transferase).
NEGATIVE_ECS = ["3.2.1.1", "2.7.1.1"]

TARGET_EC = "1.1.1.1"
N_REFERENCE = 20
N_POSITIVE = 5


def query_uniprot(query: str, page_size: int = 500, max_records: int = 2000):
    """Run a UniProt search, following cursor pagination via the Link header."""
    params = {
        "query": query,
        "format": "json",
        "size": page_size,
        "fields": "accession,protein_name,organism_name,ec,length,reviewed,sequence",
    }
    url = API
    out = []
    session = requests.Session()
    session.headers["User-Agent"] = "blast-hmmer-datax/1.0 (UNLV independent study)"

    while url and len(out) < max_records:
        resp = session.get(url, params=params if url == API else None, timeout=60)
        resp.raise_for_status()
        out.extend(resp.json().get("results", []))

        url = None
        link = resp.headers.get("Link")
        if link:
            m = re.search(r'<([^>]+)>;\s*rel="next"', link)
            if m:
                url = m.group(1)
                time.sleep(0.3)  # be polite to the public API
    return out


def ec_numbers(entry) -> list:
    """Collect EC numbers from an entry's recommended and alternative names."""
    desc = entry.get("proteinDescription", {})
    ecs = []
    blocks = []
    if "recommendedName" in desc:
        blocks.append(desc["recommendedName"])
    blocks.extend(desc.get("alternativeNames", []))
    for inc in desc.get("includes", []):
        if "recommendedName" in inc:
            blocks.append(inc["recommendedName"])
    for block in blocks:
        for ec in block.get("ecNumbers", []):
            if ec.get("value") and ec["value"] not in ecs:
                ecs.append(ec["value"])
    return ecs


def protein_name(entry) -> str:
    desc = entry.get("proteinDescription", {})
    rec = desc.get("recommendedName", {}).get("fullName", {}).get("value")
    if rec:
        return rec
    subs = desc.get("submissionNames", [])
    if subs:
        return subs[0].get("fullName", {}).get("value", "Unknown protein")
    return "Unknown protein"


def normalize(entry) -> dict:
    return {
        "accession": entry["primaryAccession"],
        "protein_name": protein_name(entry),
        "organism": entry.get("organism", {}).get("scientificName", "Unknown"),
        "ec_number": ";".join(ec_numbers(entry)),
        "length": entry.get("sequence", {}).get("length", 0),
        "reviewed": "reviewed" in entry.get("entryType", "").lower(),
        "sequence": entry.get("sequence", {}).get("value", ""),
    }


def dedupe(records: list) -> list:
    """Drop entries with a duplicate accession or an identical sequence."""
    seen_acc, seen_seq, out = set(), set(), []
    for r in records:
        if r["accession"] in seen_acc or r["sequence"] in seen_seq:
            continue
        if not r["sequence"]:
            continue
        seen_acc.add(r["accession"])
        seen_seq.add(r["sequence"])
        out.append(r)
    return out


def write_fasta(records: list, path: Path) -> None:
    with path.open("w", newline="\n") as fh:
        for r in records:
            fh.write(
                f">{r['accession']} {r['protein_name']} | "
                f"OS={r['organism']} | EC={r['ec_number']}\n"
            )
            seq = r["sequence"]
            for i in range(0, len(seq), 60):
                fh.write(seq[i:i + 60] + "\n")


def write_metadata(records: list, path: Path, retrieved: str) -> None:
    cols = ["accession", "protein_name", "organism", "ec_number", "length",
            "reviewed", "set", "retrieval_date"]
    # lineterminator="\n": csv defaults to CRLF, which leaves stray carriage
    # returns for anything reading this file with awk/cut on Linux.
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, delimiter="\t",
                           lineterminator="\n")
        w.writeheader()
        for r in records:
            w.writerow({
                "accession": r["accession"],
                "protein_name": r["protein_name"],
                "organism": r["organism"],
                "ec_number": r["ec_number"],
                "length": r["length"],
                "reviewed": "reviewed" if r["reviewed"] else "unreviewed",
                "set": r["set"],
                "retrieval_date": retrieved,
            })


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--outdir", default="data/raw", type=Path)
    args = ap.parse_args()
    args.outdir.mkdir(parents=True, exist_ok=True)

    retrieved = date.today().isoformat()

    ec_query = (f"(ec:{TARGET_EC}) AND (reviewed:true) AND (fragment:false)")
    print(f"[uniprot] query: {ec_query}")
    hits = query_uniprot(ec_query)
    print(f"[uniprot] {len(hits)} raw entries returned")

    records = dedupe(sorted((normalize(h) for h in hits),
                            key=lambda r: r["accession"]))
    # Keep only entries where EC 1.1.1.1 is actually present.
    records = [r for r in records if TARGET_EC in r["ec_number"].split(";")]
    print(f"[uniprot] {len(records)} unique non-fragment EC {TARGET_EC} entries")

    need = N_REFERENCE + N_POSITIVE
    if len(records) < need:
        print(f"ERROR: need {need} sequences, found {len(records)}", file=sys.stderr)
        return 1

    reference = records[:N_REFERENCE]
    positive = records[N_REFERENCE:need]
    for r in reference:
        r["set"] = "reference"
    for r in positive:
        r["set"] = "positive_query"

    chosen = {r["accession"] for r in records[:need]}
    chosen_seqs = {r["sequence"] for r in records[:need]}

    negative = []
    for ec in NEGATIVE_ECS:
        q = f"(ec:{ec}) AND (reviewed:true) AND (fragment:false)"
        print(f"[uniprot] negative control query: {q}")
        cand = dedupe(sorted((normalize(h) for h in query_uniprot(q, max_records=500)),
                             key=lambda r: r["accession"]))
        for r in cand:
            ecs = r["ec_number"].split(";")
            if TARGET_EC in ecs:
                continue  # must not also be an alcohol dehydrogenase
            if r["accession"] in chosen or r["sequence"] in chosen_seqs:
                continue
            if any(r["accession"] == n["accession"] for n in negative):
                continue
            r["set"] = "negative_control"
            negative.append(r)
            break
        else:
            print(f"ERROR: no negative control found for EC {ec}", file=sys.stderr)
            return 1

    write_fasta(reference, args.outdir / "reference_ec_1_1_1_1.fasta")
    write_fasta(positive, args.outdir / "queries_positive.fasta")
    write_fasta(negative, args.outdir / "queries_negative.fasta")
    write_fasta(positive + negative, args.outdir / "queries_all.fasta")

    all_records = reference + positive + negative
    write_metadata(all_records, args.outdir / "metadata.tsv", retrieved)

    provenance = {
        "retrieved_utc": datetime.now(timezone.utc).isoformat(),
        "retrieval_date": retrieved,
        "api_endpoint": API,
        "target_ec": TARGET_EC,
        "reference_query": ec_query,
        "negative_control_queries": [
            f"(ec:{ec}) AND (reviewed:true) AND (fragment:false)"
            for ec in NEGATIVE_ECS
        ],
        "selection_rule": (
            "Entries sorted by accession; deduplicated by accession and by "
            "identical sequence; first 20 -> reference, next 5 -> positive "
            "queries. Negative controls: lowest accession for each control EC "
            "that is not also annotated EC 1.1.1.1."
        ),
        "counts": {
            "reference": len(reference),
            "positive_query": len(positive),
            "negative_control": len(negative),
        },
        "accessions": {
            "reference": [r["accession"] for r in reference],
            "positive_query": [r["accession"] for r in positive],
            "negative_control": [r["accession"] for r in negative],
        },
    }
    (args.outdir / "provenance.json").write_text(
        json.dumps(provenance, indent=2) + "\n")

    print(f"[uniprot] wrote {len(reference)} reference, {len(positive)} positive, "
          f"{len(negative)} negative sequences to {args.outdir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
