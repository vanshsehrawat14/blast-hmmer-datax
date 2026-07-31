#!/usr/bin/env python3
"""Reproduce the external Swiss-Prot BLAST benchmark and test EnzymeX.

The three large input files stay outside the repository. Generated FASTA,
indexes and raw tool output go under ``var/validation`` (gitignored); only the
small JSON and Markdown summaries are written under ``results/validation``.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import pickle
import re
import shutil
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from app.config import Settings
from app.references import blast_build
from app.references.cli import build_hmmer_layer
from app.references.cluster import read_fasta
from app.references.metadata import connect_read, connect_write
from app.search.blast import blastp_args, run_blastp
from app.search.hmmer import run_hmmscan, run_phmmer
from app.search.subprocess_utils import run_tool, tool_version

log = logging.getLogger("external-validation")
COMPLETE_EC = re.compile(r"^\d+\.\d+\.\d+\.\d+$")


@dataclass(frozen=True)
class Annotation:
    sequence: str
    ecs: tuple[str, ...]
    digest: str


@dataclass(frozen=True)
class BlastHit:
    query: str
    subject: str
    percent_identity: float
    alignment_length: int
    evalue: float
    bitscore: float


@dataclass
class BlastAnalysis:
    rows: int
    duplicate_hsps: int
    self_rows: int
    subjects: set[str]
    first: dict[str, BlastHit]
    max_identity: dict[str, BlastHit]
    ranked: dict[str, BlastHit]
    raw_order: dict[str, list[str]]
    ranked_order: dict[str, list[str]]


class RestrictedUnpickler(pickle.Unpickler):
    """Load the supplied NumPy arrays without allowing arbitrary globals."""

    _ALLOWED = {
        ("numpy._core.multiarray", "_reconstruct"),
        ("numpy.core.multiarray", "_reconstruct"),
        ("numpy", "ndarray"),
        ("numpy", "dtype"),
    }

    def find_class(self, module: str, name: str):
        if (module, name) in self._ALLOWED:
            return super().find_class(module, name)
        raise pickle.UnpicklingError(f"blocked global in fold pickle: {module}.{name}")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def artifact_hashes(paths: dict[str, Path]) -> dict[str, str | None]:
    return {
        name: file_sha256(path) if path.exists() else None
        for name, path in paths.items()
    }


def sequence_sha256(sequence: str) -> str:
    return hashlib.sha256(sequence.encode("ascii")).hexdigest()


def normalize_ec(raw: str | None) -> tuple[str, ...]:
    if not raw:
        return ()
    return tuple(sorted({token.strip() for token in raw.replace(",", ";").split(";")
                         if token.strip()}))


def load_annotations(path: Path) -> dict[str, Annotation]:
    annotations: dict[str, Annotation] = {}
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        required = {"accession", "sequence", "ec_numbers"}
        missing = required - set(reader.fieldnames or ())
        if missing:
            raise ValueError(f"annotation CSV is missing columns: {sorted(missing)}")
        for lineno, row in enumerate(reader, start=2):
            accession = (row["accession"] or "").strip()
            sequence = (row["sequence"] or "").strip().upper()
            if not accession or not sequence:
                raise ValueError(f"empty accession or sequence at CSV line {lineno}")
            if accession in annotations:
                raise ValueError(f"duplicate accession in annotation CSV: {accession}")
            annotations[accession] = Annotation(
                sequence=sequence,
                ecs=normalize_ec(row["ec_numbers"]),
                digest=sequence_sha256(sequence),
            )
    return annotations


def _as_strings(values) -> list[str]:
    if hasattr(values, "tolist"):
        values = values.tolist()
    if not isinstance(values, (list, tuple)):
        raise ValueError(f"fold partition must be an array or list, got {type(values).__name__}")
    if not all(isinstance(value, str) for value in values):
        raise ValueError("fold partition contains a non-string accession")
    return list(values)


def load_fold(path: Path, fold_number: int,
              annotations: dict[str, Annotation]) -> dict[str, list[str]]:
    with path.open("rb") as fh:
        root = RestrictedUnpickler(fh).load()
    fold_key = f"fold_{fold_number}"
    if not isinstance(root, dict) or fold_key not in root:
        raise ValueError(f"{fold_key} not found in fold pickle")
    raw = root[fold_key]
    required = ("train_acc", "val_acc", "test_acc")
    if not isinstance(raw, dict) or any(key not in raw for key in required):
        raise ValueError(f"{fold_key} must contain {', '.join(required)}")

    fold = {key: _as_strings(raw[key]) for key in required}
    sets = {key: set(values) for key, values in fold.items()}
    for key, values in fold.items():
        if len(values) != len(sets[key]):
            raise ValueError(f"{fold_key}.{key} contains duplicate accessions")
        unknown = sets[key] - annotations.keys()
        if unknown:
            raise ValueError(f"{fold_key}.{key} contains {len(unknown)} unknown accessions")
    for left, right in (("train_acc", "val_acc"), ("train_acc", "test_acc"),
                        ("val_acc", "test_acc")):
        overlap = sets[left] & sets[right]
        if overlap:
            raise ValueError(f"{fold_key} accession leakage between {left} and {right}")
    return fold


def split_summary(fold: dict[str, list[str]],
                  annotations: dict[str, Annotation]) -> dict:
    summary: dict[str, object] = {}
    digest_sets: dict[str, set[str]] = {}
    for key, accessions in fold.items():
        digest_sets[key] = {annotations[acc].digest for acc in accessions}
        summary[key] = {
            "sequences": len(accessions),
            "residues": sum(len(annotations[acc].sequence) for acc in accessions),
            "unique_sequence_hashes": len(digest_sets[key]),
        }
    summary["accession_overlap"] = {
        "train_val": len(set(fold["train_acc"]) & set(fold["val_acc"])),
        "train_test": len(set(fold["train_acc"]) & set(fold["test_acc"])),
        "val_test": len(set(fold["val_acc"]) & set(fold["test_acc"])),
    }
    summary["sequence_overlap"] = {
        "train_val": len(digest_sets["train_acc"] & digest_sets["val_acc"]),
        "train_test": len(digest_sets["train_acc"] & digest_sets["test_acc"]),
        "val_test": len(digest_sets["val_acc"] & digest_sets["test_acc"]),
    }
    return summary


def write_fasta(path: Path, accessions: Iterable[str],
                annotations: dict[str, Annotation]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="ascii", newline="\n") as fh:
        for accession in accessions:
            annotation = annotations[accession]
            fh.write(f">{accession}\n")
            for offset in range(0, len(annotation.sequence), 60):
                fh.write(annotation.sequence[offset:offset + 60] + "\n")
            count += 1
    return count


def in_annotation_order(annotations: dict[str, Annotation],
                        accessions: Iterable[str]) -> list[str]:
    wanted = set(accessions)
    return [accession for accession in annotations if accession in wanted]


def _parse_blast_row(fields: list[str], lineno: int) -> BlastHit:
    if len(fields) not in (12, 13):
        raise ValueError(
            f"BLAST TSV line {lineno} has {len(fields)} columns; expected 12 or 13"
        )
    try:
        return BlastHit(
            query=fields[0],
            subject=fields[1],
            percent_identity=float(fields[2]),
            alignment_length=int(fields[3]),
            evalue=float(fields[10]),
            bitscore=float(fields[11]),
        )
    except ValueError as exc:
        raise ValueError(f"malformed BLAST TSV line {lineno}: {exc}") from exc


def analyze_blast_tsv(path: Path, order_limit: int = 25) -> BlastAnalysis:
    """Stream either default outfmt 6 or the application's extended outfmt."""
    first: dict[str, BlastHit] = {}
    max_identity: dict[str, BlastHit] = {}
    ranked: dict[str, BlastHit] = {}
    raw_order: dict[str, list[str]] = {}
    ranked_order: dict[str, list[str]] = {}
    subjects: set[str] = set()
    finalized_queries: set[str] = set()

    current_query: str | None = None
    current_first: BlastHit | None = None
    current_identity: BlastHit | None = None
    best_by_subject: dict[str, BlastHit] = {}
    subject_order: list[str] = []
    subject_seen: set[str] = set()
    rows = duplicate_hsps = self_rows = 0

    def finalize() -> None:
        nonlocal current_query
        if current_query is None or current_first is None or current_identity is None:
            return
        ordered = sorted(
            best_by_subject.values(),
            key=lambda hit: (hit.evalue, -hit.bitscore, hit.subject),
        )
        first[current_query] = current_first
        max_identity[current_query] = current_identity
        ranked[current_query] = ordered[0]
        raw_order[current_query] = subject_order[:order_limit]
        ranked_order[current_query] = [hit.subject for hit in ordered[:order_limit]]
        finalized_queries.add(current_query)

    with path.open(newline="", encoding="utf-8", errors="replace") as fh:
        reader = csv.reader(fh, delimiter="\t")
        for lineno, fields in enumerate(reader, start=1):
            if not fields or fields[0].startswith("#"):
                continue
            hit = _parse_blast_row(fields, lineno)
            rows += 1
            subjects.add(hit.subject)
            self_rows += hit.query == hit.subject

            if hit.query != current_query:
                finalize()
                if hit.query in finalized_queries:
                    raise ValueError(f"BLAST TSV query {hit.query} is not in one contiguous block")
                current_query = hit.query
                current_first = hit
                current_identity = hit
                best_by_subject = {}
                subject_order = []
                subject_seen = set()

            assert current_identity is not None
            if hit.percent_identity > current_identity.percent_identity:
                current_identity = hit
            if hit.subject in subject_seen:
                duplicate_hsps += 1
            else:
                subject_seen.add(hit.subject)
                subject_order.append(hit.subject)
            prior = best_by_subject.get(hit.subject)
            if prior is None or hit.bitscore > prior.bitscore:
                best_by_subject[hit.subject] = hit
    finalize()

    return BlastAnalysis(
        rows=rows,
        duplicate_hsps=duplicate_hsps,
        self_rows=self_rows,
        subjects=subjects,
        first=first,
        max_identity=max_identity,
        ranked=ranked,
        raw_order=raw_order,
        ranked_order=ranked_order,
    )


def evaluate_hits(query_ids: Iterable[str], selected: dict[str, str],
                  truth: dict[str, Annotation],
                  ec_by_subject: dict[str, tuple[str, ...]]) -> dict:
    query_ids = list(query_ids)
    exact = any_overlap = all_true = no_extra = 0
    intersection_tokens = true_tokens = predicted_tokens = 0
    missing_metadata = unannotated = 0

    for query in query_ids:
        expected = set(truth[query].ecs)
        true_tokens += len(expected)
        subject = selected.get(query)
        if subject is None:
            continue
        if subject not in ec_by_subject:
            missing_metadata += 1
            continue
        predicted = set(ec_by_subject[subject])
        if not predicted:
            unannotated += 1
            continue
        intersection = expected & predicted
        exact += predicted == expected
        any_overlap += bool(intersection)
        all_true += expected <= predicted
        no_extra += predicted <= expected
        intersection_tokens += len(intersection)
        predicted_tokens += len(predicted)

    predictions = sum(query in selected for query in query_ids)
    precision = intersection_tokens / predicted_tokens if predicted_tokens else 0.0
    recall = intersection_tokens / true_tokens if true_tokens else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    total = len(query_ids)
    return {
        "queries": total,
        "predictions": predictions,
        "no_hits": total - predictions,
        "missing_hit_metadata": missing_metadata,
        "unannotated_top_hits": unannotated,
        "exact_ec_set": exact,
        "exact_ec_set_rate": exact / total if total else 0.0,
        "any_ec_overlap": any_overlap,
        "any_ec_overlap_rate": any_overlap / total if total else 0.0,
        "all_true_ecs_predicted": all_true,
        "all_true_ecs_predicted_rate": all_true / total if total else 0.0,
        "no_extra_ecs_predicted": no_extra,
        "no_extra_ecs_predicted_rate": no_extra / total if total else 0.0,
        "ec_token_micro_precision": precision,
        "ec_token_micro_recall": recall,
        "ec_token_micro_f1": f1,
    }


def evaluate_ranking(query_ids: Iterable[str], ranked_subjects: dict[str, list[str]],
                     truth: dict[str, Annotation],
                     ec_by_subject: dict[str, tuple[str, ...]]) -> dict:
    query_ids = list(query_ids)
    found_at_5 = found_at_25 = 0
    reciprocal_rank = 0.0
    for query in query_ids:
        expected = set(truth[query].ecs)
        first_match: int | None = None
        for rank, subject in enumerate(ranked_subjects.get(query, [])[:25], start=1):
            if expected & set(ec_by_subject.get(subject, ())):
                first_match = rank
                break
        if first_match is None:
            continue
        found_at_5 += first_match <= 5
        found_at_25 += 1
        reciprocal_rank += 1.0 / first_match
    total = len(query_ids)
    return {
        "any_ec_hits_at_5": found_at_5,
        "any_ec_hit_at_5": found_at_5 / total if total else 0.0,
        "any_ec_hits_at_25": found_at_25,
        "any_ec_hit_at_25": found_at_25 / total if total else 0.0,
        "mean_reciprocal_rank_first_ec_overlap": (
            reciprocal_rank / total if total else 0.0
        ),
    }


def per_ec_top1(query_ids: Iterable[str], selected: dict[str, str],
                truth: dict[str, Annotation],
                ec_by_subject: dict[str, tuple[str, ...]]) -> dict[str, dict]:
    counts: dict[str, list[int]] = {}
    for query in query_ids:
        subject = selected.get(query)
        predicted = set(ec_by_subject.get(subject, ())) if subject else set()
        for ec in truth[query].ecs:
            if not COMPLETE_EC.fullmatch(ec):
                continue
            row = counts.setdefault(ec, [0, 0])
            row[0] += 1
            row[1] += ec in predicted
    return {
        ec: {
            "support": support,
            "top1_correct": correct,
            "top1_rate": correct / support,
        }
        for ec, (support, correct) in sorted(counts.items())
    }


def accession_ec_map(annotations: dict[str, Annotation]) -> dict[str, tuple[str, ...]]:
    return {accession: annotation.ecs for accession, annotation in annotations.items()}


def choices(selection: dict[str, BlastHit]) -> dict[str, str]:
    return {query: hit.subject for query, hit in selection.items()}


def identity_summary(query_ids: Iterable[str],
                     selected: dict[str, BlastHit]) -> dict:
    values = [
        selected[query].percent_identity
        for query in query_ids
        if query in selected
    ]
    return {
        "top_hits_with_identity": len(values),
        "median_percent_identity": statistics.median(values) if values else None,
        "at_least_90_percent": sum(value >= 90.0 for value in values),
        "at_least_90_percent_rate": (
            sum(value >= 90.0 for value in values) / len(values) if values else 0.0
        ),
        "at_least_99_percent": sum(value >= 99.0 for value in values),
        "at_least_99_percent_rate": (
            sum(value >= 99.0 for value in values) / len(values) if values else 0.0
        ),
    }


def compare_orders(expected_queries: Iterable[str], baseline: BlastAnalysis,
                   reproduced: BlastAnalysis, limit: int = 25) -> dict:
    queries = list(expected_queries)
    both = [query for query in queries
            if query in baseline.raw_order and query in reproduced.raw_order]
    baseline_only = [query for query in queries
                     if query in baseline.raw_order and query not in reproduced.raw_order]
    reproduced_only = [query for query in queries
                       if query not in baseline.raw_order and query in reproduced.raw_order]
    no_hit_both = [query for query in queries
                   if query not in baseline.raw_order and query not in reproduced.raw_order]
    top1_same = sum(
        baseline.raw_order[query][:1] == reproduced.raw_order[query][:1]
        for query in both
    )
    top1_score_same = sum(
        baseline.first[query].evalue == reproduced.first[query].evalue
        and baseline.first[query].bitscore == reproduced.first[query].bitscore
        for query in both
    )
    tied_id_changes = sum(
        baseline.first[query].subject != reproduced.first[query].subject
        and baseline.first[query].evalue == reproduced.first[query].evalue
        and baseline.first[query].bitscore == reproduced.first[query].bitscore
        for query in both
    )
    ordered_same = sum(
        baseline.raw_order[query][:limit] == reproduced.raw_order[query][:limit]
        for query in both
    )
    sets_same = sum(
        set(baseline.raw_order[query][:limit]) == set(reproduced.raw_order[query][:limit])
        for query in both
    )
    overlap_sum = 0.0
    for query in both:
        left = set(baseline.raw_order[query][:limit])
        right = set(reproduced.raw_order[query][:limit])
        overlap_sum += len(left & right) / max(len(left | right), 1)
    return {
        "queries_with_hits_in_both": len(both),
        "baseline_only_hits": len(baseline_only),
        "reproduction_only_hits": len(reproduced_only),
        "no_hits_in_both": len(no_hit_both),
        "raw_top1_same": top1_same,
        "raw_top1_same_rate": top1_same / len(both) if both else 0.0,
        "raw_top1_score_same": top1_score_same,
        "raw_top1_score_same_rate": (
            top1_score_same / len(both) if both else 0.0
        ),
        "raw_top1_tied_score_id_changes": tied_id_changes,
        f"ordered_top{limit}_identical": ordered_same,
        f"ordered_top{limit}_identical_rate": ordered_same / len(both) if both else 0.0,
        f"top{limit}_set_identical": sets_same,
        f"top{limit}_set_identical_rate": sets_same / len(both) if both else 0.0,
        f"mean_top{limit}_jaccard": overlap_sum / len(both) if both else 0.0,
    }


def write_top_hit_diff(path: Path, queries: Iterable[str],
                       annotations: dict[str, Annotation],
                       baseline: BlastAnalysis, reproduced: BlastAnalysis) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh, delimiter="\t")
        writer.writerow([
            "query", "query_ec", "baseline_top1", "reproduced_raw_top1",
            "reproduced_display_top1",
        ])
        for query in queries:
            left = baseline.first.get(query)
            raw = reproduced.first.get(query)
            shown = reproduced.ranked.get(query)
            left_id = left.subject if left else ""
            raw_id = raw.subject if raw else ""
            shown_id = shown.subject if shown else ""
            if len({left_id, raw_id, shown_id}) > 1:
                writer.writerow([
                    query, ";".join(annotations[query].ecs),
                    left_id, raw_id, shown_id,
                ])


def _reset_generated_dir(path: Path, root: Path) -> None:
    resolved = path.resolve()
    allowed = root.resolve()
    if resolved == allowed or allowed not in resolved.parents:
        raise ValueError(f"refusing to reset generated directory outside {allowed}: {resolved}")
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)


def _promote_generated_dir(staging: Path, destination: Path, root: Path) -> None:
    staging_resolved = staging.resolve()
    destination_resolved = destination.resolve()
    allowed = root.resolve()
    if (allowed not in staging_resolved.parents
            or allowed not in destination_resolved.parents
            or staging_resolved == destination_resolved):
        raise ValueError("refusing to promote generated directories outside work root")
    if destination.exists():
        shutil.rmtree(destination)
    staging.replace(destination)


def benchmark_settings(reference_dir: Path, job_dir: Path, threads: int,
                       timeout: int, *, baseline: bool = False) -> Settings:
    values = {
        "_env_file": None,
        "reference_dir": reference_dir,
        "job_dir": job_dir,
        "blastp_bin": "blastp",
        "makeblastdb_bin": "makeblastdb",
        "phmmer_bin": "phmmer",
        "hmmscan_bin": "hmmscan",
        "hmmbuild_bin": "hmmbuild",
        "hmmpress_bin": "hmmpress",
        "mafft_bin": "mafft",
        "mmseqs_bin": "mmseqs",
        "blast_evalue": 1e-3,
        "blast_max_target_seqs": 500,
        "phmmer_evalue": 1e-3,
        "hmmscan_evalue": 1e-3,
        "cluster_min_seq_id": 0.35,
        "cluster_coverage": 0.80,
        "profile_min_members": 5,
        "profile_max_members": 500,
        "profile_min_match_states": 40,
        "search_threads": threads,
        "build_threads": threads,
        "blast_timeout_seconds": timeout,
        "hmmer_timeout_seconds": timeout,
        "build_timeout_seconds": timeout,
        "max_query_sequences": 100_000,
        "max_hits_per_query": 100 if baseline else 25,
    }
    if baseline:
        values.update(blast_evalue=1e-5, blast_max_target_seqs=100)
    return Settings(**values)


def effective_configuration(settings: Settings) -> dict:
    return {
        "blast_evalue": settings.blast_evalue,
        "blast_max_target_seqs": settings.blast_max_target_seqs,
        "phmmer_evalue": settings.phmmer_evalue,
        "hmmscan_evalue": settings.hmmscan_evalue,
        "max_hits_per_query": settings.max_hits_per_query,
        "search_threads": settings.search_threads,
        "build_threads": settings.build_threads,
        "cluster_min_seq_id": settings.cluster_min_seq_id,
        "cluster_coverage": settings.cluster_coverage,
        "profile_min_members": settings.profile_min_members,
        "profile_max_members": settings.profile_max_members,
        "profile_min_match_states": settings.profile_min_match_states,
    }


def benchmark_tool_versions(settings: Settings) -> dict[str, str | None]:
    return {
        "makeblastdb": tool_version(settings.makeblastdb_bin, ["-version"]),
        "blastp": tool_version(settings.blastp_bin, ["-version"]),
        "phmmer": tool_version(settings.phmmer_bin, ["-h"]),
        "hmmscan": tool_version(settings.hmmscan_bin, ["-h"]),
        "hmmbuild": tool_version(settings.hmmbuild_bin, ["-h"]),
        "hmmpress": tool_version(settings.hmmpress_bin, ["-h"]),
        "mafft": tool_version(settings.mafft_bin, ["--version"]),
        "mmseqs": tool_version(settings.mmseqs_bin, ["version"]),
    }


def reproduction_cohort(test_accessions: list[str], shared: BlastAnalysis,
                        size: int) -> list[str]:
    """Deterministic spread across the fold, retaining every shared no-hit."""
    if size <= 0 or size >= len(test_accessions):
        return list(test_accessions)
    no_hits = [accession for accession in test_accessions if accession not in shared.first]
    if len(no_hits) >= size:
        return no_hits[:size]
    candidates = [accession for accession in test_accessions if accession in shared.first]
    wanted = size - len(no_hits)
    sampled = [candidates[(index * len(candidates)) // wanted] for index in range(wanted)]
    return no_hits + sampled


def run_baseline_reproduction(annotations: dict[str, Annotation],
                              train_accessions: list[str],
                              query_accessions: list[str], work_dir: Path,
                              threads: int, timeout: int) -> tuple[Path, dict]:
    root = work_dir / "baseline"
    _reset_generated_dir(root, work_dir)
    reference_dir = root / "reference"
    query_path = root / "test.fasta"
    train_in_csv_order = in_annotation_order(annotations, train_accessions)
    queries_in_csv_order = in_annotation_order(annotations, query_accessions)
    write_fasta(
        reference_dir / "references.fasta", train_in_csv_order, annotations
    )
    write_fasta(query_path, queries_in_csv_order, annotations)

    settings = benchmark_settings(reference_dir, root / "jobs", threads, timeout,
                                  baseline=True)
    build_stats = blast_build.build(settings, "external-fold-benchmark")
    search_dir = root / "search"
    output = search_dir / "blastp_hits.tsv"
    args = blastp_args(settings, query_path, output)
    run = run_tool(
        settings.blastp_bin,
        args,
        timeout=timeout,
        log_dir=search_dir,
        log_name="blastp",
    )
    if not run.ok:
        raise RuntimeError(
            f"fold BLAST failed (exit={run.returncode}, timeout={run.timed_out}): "
            f"{run.stderr_snippet[:500]}"
        )
    return output, {
        "build": build_stats,
        "search_seconds": round(run.duration, 2),
        "tool_version": tool_version(settings.blastp_bin, ["-version"]),
        "query_cohort": {
            "queries": len(query_accessions),
            "definition": "deterministic fold spread including every shared no-hit",
        },
        "parameters": {
            "evalue": settings.blast_evalue,
            "max_target_seqs": settings.blast_max_target_seqs,
            "comp_based_stats": 2,
            "threads": settings.search_threads,
            "outfmt": args[args.index("-outfmt") + 1],
        },
        "artifacts": artifact_hashes({
            "train_fasta": settings.references_fasta,
            "query_fasta": query_path,
            "blast_tsv": output,
        }),
    }


def prepare_filtered_reference(source_dir: Path, destination: Path,
                               heldout_hashes: set[str], work_root: Path) -> dict:
    _reset_generated_dir(destination, work_root)
    sequences = read_fasta(source_dir / "references.fasta")

    source = connect_read(source_dir / "metadata.sqlite3")
    destination_db = connect_write(destination / "metadata.sqlite3")
    try:
        reference_rows = {
            row["ref_id"]: tuple(row)
            for row in source.execute("SELECT * FROM reference ORDER BY ref_id")
        }
        keep_ids = [
            ref_id for ref_id, sequence in sequences.items()
            if sequence_sha256(sequence) not in heldout_hashes
        ]
        removed_ids = set(sequences) - set(keep_ids)

        with (destination / "references.fasta").open(
            "w", encoding="ascii", newline="\n"
        ) as fh:
            for ref_id in keep_ids:
                row = reference_rows[ref_id]
                ec = row[3] or "NA"
                source_name = row[4] or "unknown"
                fh.write(f">{ref_id} EC={ec} src={source_name}\n")
                sequence = sequences[ref_id]
                for offset in range(0, len(sequence), 60):
                    fh.write(sequence[offset:offset + 60] + "\n")

        placeholders = ",".join("?" * 11)
        destination_db.executemany(
            f"INSERT INTO reference VALUES ({placeholders})",
            (reference_rows[ref_id] for ref_id in keep_ids),
        )
        duplicate_rows = [
            tuple(row) for row in source.execute("SELECT * FROM reference_duplicate")
            if row["ref_id"] not in removed_ids
        ]
        if duplicate_rows:
            destination_db.executemany(
                "INSERT INTO reference_duplicate VALUES (?,?,?,?,?)", duplicate_rows
            )
        destination_db.execute(
            "INSERT INTO build_meta (key, value) VALUES (?, ?)",
            ("benchmark_filter", "removed fold test sequence hashes"),
        )
        destination_db.commit()
    finally:
        source.close()
        destination_db.close()

    kept_hashes = {sequence_sha256(sequences[ref_id]) for ref_id in keep_ids}
    leaked = kept_hashes & heldout_hashes
    if leaked:
        raise RuntimeError(f"filtered reference still contains {len(leaked)} held-out sequences")
    return {
        "source_references": len(sequences),
        "removed_reference_records_matching_test_hashes": len(removed_ids),
        "kept_references": len(keep_ids),
        "kept_residues": sum(len(sequences[ref_id]) for ref_id in keep_ids),
        "sequence_hash_overlap_after_filter": len(leaked),
    }


def _reference_ec_maps(metadata_path: Path) -> tuple[
    dict[str, tuple[str, ...]], dict[str, tuple[str, ...]]
]:
    conn = connect_read(metadata_path)
    try:
        references = {
            row["ref_id"]: normalize_ec(row["ec"])
            for row in conn.execute("SELECT ref_id, ec FROM reference")
        }
        profiles = {
            row["family_id"]: normalize_ec(row["consensus_ec"])
            for row in conn.execute("SELECT family_id, consensus_ec FROM profile")
        }
    finally:
        conn.close()
    return references, profiles


def reference_annotation_concordance(
    metadata_path: Path, annotations: dict[str, Annotation]
) -> dict:
    csv_by_digest = {
        annotation.digest: annotation.ecs for annotation in annotations.values()
    }
    matched = exact = any_overlap = 0
    conn = connect_read(metadata_path)
    try:
        for row in conn.execute("SELECT sequence_sha256, ec FROM reference"):
            csv_ec = csv_by_digest.get(row["sequence_sha256"])
            if csv_ec is None:
                continue
            matched += 1
            reference_ec = normalize_ec(row["ec"])
            exact += reference_ec == csv_ec
            any_overlap += bool(set(reference_ec) & set(csv_ec))
    finally:
        conn.close()
    return {
        "exact_sequence_matches": matched,
        "exact_ec_set_matches": exact,
        "exact_ec_set_match_rate": exact / matched if matched else 0.0,
        "any_ec_overlap": any_overlap,
        "any_ec_overlap_rate": any_overlap / matched if matched else 0.0,
    }


def _outcome_selection(outcome) -> dict[str, str]:
    return {
        query: hits[0].hit_id
        for query, hits in outcome.hits_by_query.items()
        if hits
    }


def _outcome_ranking(outcome) -> dict[str, list[str]]:
    return {
        query: [hit.hit_id for hit in hits]
        for query, hits in outcome.hits_by_query.items()
    }


def _blast_outcome_identity(outcome, query_ids: Iterable[str]) -> dict:
    values = [
        outcome.hits_by_query[query][0].percent_identity
        for query in query_ids
        if outcome.hits_by_query.get(query)
        and outcome.hits_by_query[query][0].percent_identity is not None
    ]
    return {
        "top_hits_with_identity": len(values),
        "median_percent_identity": statistics.median(values) if values else None,
    }


def run_enzymex_benchmark(annotations: dict[str, Annotation],
                          fold: dict[str, list[str]], source_reference_dir: Path,
                          work_dir: Path, threads: int, timeout: int) -> dict:
    root = work_dir / "enzymex"
    _reset_generated_dir(root, work_dir)
    reference_dir = root / "reference"
    test_hashes = {annotations[acc].digest for acc in fold["test_acc"]}
    filter_stats = prepare_filtered_reference(
        source_reference_dir, reference_dir, test_hashes, root
    )

    settings = benchmark_settings(reference_dir, root / "jobs", threads, timeout)
    blast_stats = blast_build.build(settings, "external-fold-filtered")
    hmmer_stats = build_hmmer_layer(settings, keep_work=False)
    reference_ec, profile_ec = _reference_ec_maps(settings.metadata_db)
    supported_ecs = {
        token for ecs in reference_ec.values() for token in ecs
        if COMPLETE_EC.fullmatch(token)
    }
    profile_ecs = {
        token for ecs in profile_ec.values() for token in ecs
        if COMPLETE_EC.fullmatch(token)
    }
    reference_covered = [
        accession for accession in fold["test_acc"]
        if set(annotations[accession].ecs) & supported_ecs
    ]
    exact_set_eligible = [
        accession for accession in reference_covered
        if set(annotations[accession].ecs) <= supported_ecs
    ]
    profile_common_single_label = [
        accession for accession in reference_covered
        if len(annotations[accession].ecs) == 1
        and annotations[accession].ecs[0] in profile_ecs
    ]
    profile_common_set = set(profile_common_single_label)
    profile_outside = [
        accession for accession in reference_covered
        if accession not in profile_common_set
    ]
    query_path = root / "supported_test_queries.fasta"
    write_fasta(query_path, reference_covered, annotations)

    method_rows: dict[str, dict] = {}
    blast_identity: dict | None = None
    for method, runner, ec_map in (
        ("blastp", run_blastp, reference_ec),
        ("phmmer", run_phmmer, reference_ec),
        ("hmmscan", run_hmmscan, profile_ec),
    ):
        job_dir = root / "jobs" / method
        outcome = runner(settings, job_dir, query_path)
        if outcome.failed:
            raise RuntimeError(
                f"{method} benchmark failed: {outcome.error_code}: {outcome.error_message}"
            )
        selection = _outcome_selection(outcome)
        ranking = _outcome_ranking(outcome)
        scopes = {}
        for scope_name, cohort in (
            ("reference_covered", reference_covered),
            ("exact_set_eligible", exact_set_eligible),
            ("profile_common_single_label", profile_common_single_label),
            ("profile_outside", profile_outside),
        ):
            per_ec = per_ec_top1(cohort, selection, annotations, ec_map)
            scopes[scope_name] = {
                "top1": evaluate_hits(cohort, selection, annotations, ec_map),
                "ranking": evaluate_ranking(cohort, ranking, annotations, ec_map),
                "per_ec_top1": per_ec,
                "per_ec_macro_top1_rate": (
                    statistics.mean(row["top1_rate"] for row in per_ec.values())
                    if per_ec else 0.0
                ),
            }
        method_rows[method] = {
            "tool_version": outcome.version,
            "runtime_seconds": round(outcome.runtime or 0.0, 2),
            "scopes": scopes,
        }
        if method == "blastp":
            blast_identity = {
                "reference_covered": _blast_outcome_identity(
                    outcome, reference_covered
                ),
                "profile_common_single_label": _blast_outcome_identity(
                    outcome, profile_common_single_label
                ),
                "profile_outside": _blast_outcome_identity(
                    outcome, profile_outside
                ),
            }

    return {
        "source_reference": source_reference_summary(source_reference_dir),
        "configuration": effective_configuration(settings),
        "tool_versions": benchmark_tool_versions(settings),
        "reference_filter": filter_stats,
        "reference_annotation_concordance": reference_annotation_concordance(
            settings.metadata_db, annotations
        ),
        "reference_ec_tokens": len(supported_ecs),
        "profile_consensus_ec_tokens": len(profile_ecs),
        "cohorts": {
            "reference_covered": {
                "definition": (
                    "fold test queries sharing at least one complete EC with "
                    "the filtered reference"
                ),
                "queries": len(reference_covered),
                "residues": sum(
                    len(annotations[acc].sequence) for acc in reference_covered
                ),
            },
            "exact_set_eligible": {
                "definition": (
                    "reference-covered queries whose complete truth EC set is "
                    "fully represented"
                ),
                "queries": len(exact_set_eligible),
            },
            "profile_common_single_label": {
                "definition": (
                    "single-label queries whose EC is a filtered profile consensus"
                ),
                "queries": len(profile_common_single_label),
            },
            "profile_outside": {
                "definition": (
                    "reference-covered queries outside the profile-common "
                    "single-label slice"
                ),
                "queries": len(profile_outside),
            },
        },
        "sequence_hash_overlap_with_reference": 0,
        "build": {
            "blast": blast_stats,
            "hmmer": hmmer_stats,
        },
        "methods": method_rows,
        "blast_top_hit_identity": blast_identity,
        "artifacts": artifact_hashes({
            "references_fasta": settings.references_fasta,
            "metadata_sqlite": settings.metadata_db,
            "profiles_hmm": settings.profile_db,
            "query_fasta": query_path,
            "blast_tsv": root / "jobs" / "blastp" / "blastp_hits.tsv",
            "phmmer_tblout": root / "jobs" / "phmmer" / "phmmer_tblout.txt",
            "phmmer_domtblout": root / "jobs" / "phmmer" / "phmmer_domtblout.txt",
            "hmmscan_tblout": root / "jobs" / "hmmscan" / "hmmscan_tblout.txt",
            "hmmscan_domtblout": root / "jobs" / "hmmscan" / "hmmscan_domtblout.txt",
        }),
    }


def source_reference_summary(reference_dir: Path) -> dict:
    manifest_path = reference_dir / "build_manifest.json"
    manifest = (
        json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest_path.exists() else {}
    )
    return {
        "reference_build_id": manifest.get("reference_build_id"),
        "reference_sources": (manifest.get("configuration") or {}).get(
            "reference_sources"
        ),
        "source_counts": (manifest.get("export") or {}).get("sources"),
        "references_fasta_sha256": file_sha256(reference_dir / "references.fasta"),
        "metadata_sqlite_sha256": file_sha256(reference_dir / "metadata.sqlite3"),
        "manifest_sha256": file_sha256(manifest_path) if manifest_path.exists() else None,
    }


def baseline_report(analysis: BlastAnalysis, fold: dict[str, list[str]],
                    annotations: dict[str, Annotation]) -> dict:
    expected = fold["test_acc"]
    train = set(fold["train_acc"])
    seen = set(analysis.first)
    ec_map = accession_ec_map(annotations)
    return {
        "tsv": {
            "rows": analysis.rows,
            "queries_with_hits": len(seen),
            "no_hit_queries": len(set(expected) - seen),
            "unexpected_queries": len(seen - set(expected)),
            "unique_subjects": len(analysis.subjects),
            "subjects_outside_train": len(analysis.subjects - train),
            "self_match_rows": analysis.self_rows,
            "additional_hsp_rows": analysis.duplicate_hsps,
        },
        "raw_first_subject": evaluate_hits(
            expected, choices(analysis.first), annotations, ec_map
        ),
        "application_ranking": evaluate_hits(
            expected, choices(analysis.ranked), annotations, ec_map
        ),
        "notebook_max_identity": evaluate_hits(
            expected, choices(analysis.max_identity), annotations, ec_map
        ),
        "raw_first_subject_identity": identity_summary(expected, analysis.first),
    }


def _percent(value: float) -> str:
    return f"{100 * value:.2f}%"


def render_markdown(report: dict) -> str:
    baseline = report["shared_baseline"]
    reproduced = report["reproduction"]
    enzymex = report["enzymex"]
    baseline_identity = baseline["raw_first_subject_identity"]
    covered_n = enzymex["cohorts"]["reference_covered"]["queries"]
    exact_n = enzymex["cohorts"]["exact_set_eligible"]["queries"]
    profile_n = enzymex["cohorts"]["profile_common_single_label"]["queries"]
    source_reference = enzymex["source_reference"]
    source_counts = source_reference.get("source_counts") or {}
    source_order = [
        source for source in ("swissprot", "pdb") if source in source_counts
    ] + sorted(set(source_counts) - {"swissprot", "pdb"})
    source_count_text = ", ".join(
        f"{source}={source_counts[source]:,}" for source in source_order
    ) or "not recorded"
    profile_median_identity = enzymex["blast_top_hit_identity"][
        "profile_common_single_label"
    ]["median_percent_identity"]
    profile_identity_text = (
        f"{profile_median_identity:.1f}%"
        if profile_median_identity is not None else "not available"
    )
    baseline_median_identity = baseline_identity["median_percent_identity"]
    baseline_identity_text = (
        f"{baseline_median_identity:.1f}%"
        if baseline_median_identity is not None else "not available"
    )
    blast_scopes = enzymex["methods"]["blastp"]["scopes"]
    phmmer_scopes = enzymex["methods"]["phmmer"]["scopes"]
    top1_gap = abs(
        blast_scopes["reference_covered"]["top1"]["any_ec_overlap"]
        - phmmer_scopes["reference_covered"]["top1"]["any_ec_overlap"]
    )
    exact_gap = abs(
        blast_scopes["exact_set_eligible"]["top1"]["exact_ec_set"]
        - phmmer_scopes["exact_set_eligible"]["top1"]["exact_ec_set"]
    )
    lines = [
        "# External BLAST/HMMER validation",
        "",
        f"- Fold: `{report['fold']}`",
        f"- BLAST baseline input: `{report['inputs']['baseline_tsv']['name']}`",
        "",
        "## Split and shared baseline",
        "",
        f"- Train: {report['split']['train_acc']['sequences']:,} sequences, "
        f"{report['split']['train_acc']['residues']:,} residues",
        f"- Validation: {report['split']['val_acc']['sequences']:,} sequences",
        f"- Test: {report['split']['test_acc']['sequences']:,} sequences",
        f"- Train/test accession overlap: "
        f"{report['split']['accession_overlap']['train_test']}",
        f"- Train/test exact-sequence overlap: "
        f"{report['split']['sequence_overlap']['train_test']}",
        f"- Shared TSV queries with hits: {baseline['tsv']['queries_with_hits']:,}; "
        f"no hits: {baseline['tsv']['no_hit_queries']:,}",
        f"- Raw top-hit identity: median {baseline_identity_text}; "
        f"{_percent(baseline_identity['at_least_90_percent_rate'])} are at least "
        f"90% identical and "
        f"{_percent(baseline_identity['at_least_99_percent_rate'])} are at least "
        "99% identical",
        "",
        "| Shared-output selection | Exact EC set | Any EC overlap | EC micro-F1 |",
        "|---|---:|---:|---:|",
    ]
    for label, key in (
        ("BLAST first subject", "raw_first_subject"),
        ("Application E-value/bit-score rank", "application_ranking"),
        ("Notebook maximum identity", "notebook_max_identity"),
    ):
        metrics = baseline[key]
        lines.append(
            f"| {label} | {_percent(metrics['exact_ec_set_rate'])} | "
            f"{_percent(metrics['any_ec_overlap_rate'])} | "
            f"{metrics['ec_token_micro_f1']:.4f} |"
        )

    parity = reproduced["parity"]
    reported_baseline = reproduced["reported_baseline"]
    lines += [
        "",
        "## BLAST 2.16 reproduction",
        "",
        f"- Reported baseline: `{reported_baseline['tool_version']}` with "
        f"`-evalue {reported_baseline['parameters']['evalue']:g} "
        f"-comp_based_stats {reported_baseline['parameters']['comp_based_stats']} "
        f"-max_target_seqs {reported_baseline['parameters']['max_target_seqs']} "
        f"-outfmt {reported_baseline['parameters']['outfmt']} "
        f"-num_threads {reported_baseline['parameters']['threads']}`",
        f"- Reproduction version: `{reproduced['run']['tool_version']}`",
        f"- Reproduction cohort: {reproduced['run']['query_cohort']['queries']:,} queries",
        f"- The cohort includes all "
        f"{reproduced['run']['query_cohort']['shared_no_hits']:,} shared no-hits; "
        f"hit-order parity uses the {parity['queries_with_hits_in_both']:,} queries "
        "with hits in both outputs.",
        f"- Search time: {reproduced['run']['search_seconds']:.2f} s",
        f"- Raw top-1 agreement with the reported "
        f"{reported_baseline['tool_version']} output: "
        f"{parity['raw_top1_same']:,}/{parity['queries_with_hits_in_both']:,} "
        f"({_percent(parity['raw_top1_same_rate'])})",
        f"- Raw top-score agreement (E-value and bit score): "
        f"{parity['raw_top1_score_same']:,}/"
        f"{parity['queries_with_hits_in_both']:,} "
        f"({_percent(parity['raw_top1_score_same_rate'])}); "
        f"{parity['raw_top1_tied_score_id_changes']:,} tied-score cases selected "
        "a different subject ID",
        f"- Identical ordered top 25: "
        f"{_percent(parity['ordered_top25_identical_rate'])}",
        f"- Identical top-25 subject set: "
        f"{_percent(parity['top25_set_identical_rate'])}",
        f"- Mean top-25 set Jaccard: {parity['mean_top25_jaccard']:.4f}",
        "",
        "## Leakage-safe copied-development EnzymeX benchmark",
        "",
        f"- Source reference build: "
        f"`{source_reference['reference_build_id'] or 'unversioned'}`",
        f"- Selected source labels: "
        f"{', '.join(source_reference.get('reference_sources') or ['not recorded'])}",
        f"- Canonical references by source: {source_count_text}",
        f"- References: {enzymex['reference_filter']['source_references']:,} source, "
        f"{enzymex['reference_filter']['removed_reference_records_matching_test_hashes']:,} "
        "records matching test hashes removed, "
        f"{enzymex['reference_filter']['kept_references']:,} searched",
        f"- Rebuilt profiles: {enzymex['build']['hmmer']['profiles']:,}",
        f"- Of {enzymex['reference_annotation_concordance']['exact_sequence_matches']:,} "
        "filtered references exactly matching a supplied CSV sequence, "
        f"{enzymex['reference_annotation_concordance']['exact_ec_set_matches']:,} "
        f"({_percent(enzymex['reference_annotation_concordance']['exact_ec_set_match_rate'])}) "
        "have the same normalized EC set",
        f"- Reference-covered selected slice: "
        f"{enzymex['cohorts']['reference_covered']['queries']:,}/"
        f"{report['split']['test_acc']['sequences']:,} queries "
        f"({_percent(enzymex['cohorts']['reference_covered']['queries'] / report['split']['test_acc']['sequences'])})",
        f"- Exact-set eligible selected slice: "
        f"{enzymex['cohorts']['exact_set_eligible']['queries']:,}/"
        f"{report['split']['test_acc']['sequences']:,} queries "
        f"({_percent(enzymex['cohorts']['exact_set_eligible']['queries'] / report['split']['test_acc']['sequences'])})",
        f"- Profile-common single-label selected slice: "
        f"{enzymex['cohorts']['profile_common_single_label']['queries']:,}/"
        f"{report['split']['test_acc']['sequences']:,} queries "
        f"({_percent(enzymex['cohorts']['profile_common_single_label']['queries'] / report['split']['test_acc']['sequences'])})",
        f"- Exact query/reference sequence overlap: "
        f"{enzymex['sequence_hash_overlap_with_reference']}",
        "",
        "Source labels in this section come from the copied table and are not "
        "independent upstream-provenance verification. In the committed "
        "development fixture, `pdb` is a synthetic label on reviewed Swiss-Prot "
        "sequences, so this report does not validate genuine PDB-derived data.",
        "",
        "### Sequence-reference scope",
        "",
        "Runtime is for the whole reference-covered batch. Exact-set concordance "
        "uses only queries whose full truth set exists in the reference. These are "
        "single local invocations, not stable performance estimates.",
        "",
        f"| Method | Runtime | Any EC overlap (n={covered_n}) | "
        f"Exact EC set (n={exact_n}) | Any-EC hit@5 (n={covered_n}) | "
        f"Any-EC hit@25 (n={covered_n}) |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for method in ("blastp", "phmmer"):
        row = enzymex["methods"][method]
        covered = row["scopes"]["reference_covered"]
        exact = row["scopes"]["exact_set_eligible"]["top1"]
        lines.append(
            f"| {method} | {row['runtime_seconds']:.2f} s | "
            f"{covered['top1']['any_ec_overlap']:,}/{covered_n:,} "
            f"({_percent(covered['top1']['any_ec_overlap_rate'])}) | "
            f"{exact['exact_ec_set']:,}/{exact_n:,} "
            f"({_percent(exact['exact_ec_set_rate'])}) | "
            f"{covered['ranking']['any_ec_hits_at_5']:,}/{covered_n:,} "
            f"({_percent(covered['ranking']['any_ec_hit_at_5'])}) | "
            f"{covered['ranking']['any_ec_hits_at_25']:,}/{covered_n:,} "
            f"({_percent(covered['ranking']['any_ec_hit_at_25'])}) |"
        )
    lines += [
        "",
        f"BLAST and phmmer differ by only {top1_gap:,} queries on top-1 "
        f"overlap and {exact_gap:,} on exact sets. One fold does not establish "
        "a winner.",
        "",
        f"The exact-set slice spans "
        f"{len(enzymex['methods']['blastp']['scopes']['exact_set_eligible']['per_ec_top1']):,} "
        "complete EC labels. Its unweighted per-EC top-1 token concordance is "
        f"{_percent(enzymex['methods']['blastp']['scopes']['exact_set_eligible']['per_ec_macro_top1_rate'])} "
        "for BLAST and "
        f"{_percent(enzymex['methods']['phmmer']['scopes']['exact_set_eligible']['per_ec_macro_top1_rate'])} "
        "for phmmer; the lower macro rates expose weaker rare-label behavior.",
        "",
        "### Common profile scope",
        "",
        f"The BLAST top-hit median identity in this selected slice is "
        f"{profile_identity_text}.",
        "",
        f"| Method | Queries with hits | Top-1 EC overlap (n={profile_n}) | "
        f"Any-EC hit@5 (n={profile_n}) | Any-EC hit@25 (n={profile_n}) | "
        "First-overlap MRR |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for method in ("blastp", "phmmer", "hmmscan"):
        scope = enzymex["methods"][method]["scopes"]["profile_common_single_label"]
        top1 = scope["top1"]
        ranking = scope["ranking"]
        lines.append(
            f"| {method} | {top1['predictions']:,}/{top1['queries']:,} | "
            f"{top1['any_ec_overlap']:,}/{profile_n:,} "
            f"({_percent(top1['any_ec_overlap_rate'])}) | "
            f"{ranking['any_ec_hits_at_5']:,}/{profile_n:,} "
            f"({_percent(ranking['any_ec_hit_at_5'])}) | "
            f"{ranking['any_ec_hits_at_25']:,}/{profile_n:,} "
            f"({_percent(ranking['any_ec_hit_at_25'])}) | "
            f"{ranking['mean_reciprocal_rank_first_ec_overlap']:.4f} |"
        )
    lines += [
        "",
        f"The common-profile slice is the easiest part of the selected cohort. "
        f"Outside it, BLAST produced hits for "
        f"{enzymex['methods']['blastp']['scopes']['profile_outside']['top1']['predictions']:,}/"
        f"{enzymex['cohorts']['profile_outside']['queries']:,} queries and its top hit "
        f"shared an EC for "
        f"{enzymex['methods']['blastp']['scopes']['profile_outside']['top1']['any_ec_overlap']:,}/"
        f"{enzymex['cohorts']['profile_outside']['queries']:,}.",
        "",
        "The Swiss-Prot reproduction tests cross-version hit-order concordance "
        "under the reported search parameters. The EnzymeX table measures top-hit "
        "EC concordance within the copied development reference's covered labels. "
        "They answer different questions and should not be combined into one "
        "accuracy.",
        "",
        f"{enzymex['reference_annotation_concordance']['exact_sequence_matches']:,}/"
        f"{enzymex['reference_filter']['kept_references']:,} filtered development-"
        "reference sequences occur verbatim in the supplied Swiss-Prot dataset, "
        "which also supplies every query. Even after exact-sequence removal this is "
        "a close-homolog benchmark. The accession split removes exact sequences but "
        "does not impose a homology or identity cutoff.",
        "",
        f"The methods were run only on the {covered_n:,} queries selected using known "
        f"truth labels; the other "
        f"{report['split']['test_acc']['sequences'] - covered_n:,} fold queries were "
        "not searched against EnzymeX. These results therefore do not measure "
        "out-of-scope false assignments, specificity, broad EC prediction, or "
        "remote-homology performance.",
        "",
        "All methods use the current E-value threshold of 1e-3, but their E-values "
        "come from different models and database sizes. The table describes current "
        "default behavior, not a calibrated method contest.",
        "",
    ]
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--annotations", type=Path, required=True)
    parser.add_argument("--folds", type=Path, required=True)
    parser.add_argument("--baseline-tsv", type=Path, required=True)
    parser.add_argument("--source-reference-dir", type=Path, default=Path("var/reference"))
    parser.add_argument("--work-dir", type=Path, default=Path("var/validation"))
    parser.add_argument("--output-dir", type=Path, default=Path("results/validation"))
    parser.add_argument("--fold", type=int, default=0)
    parser.add_argument("--threads", type=int, default=16)
    parser.add_argument("--timeout", type=int, default=14_400)
    parser.add_argument(
        "--reproduction-queries",
        type=int,
        default=500,
        help="BLAST parity cohort size; 0 runs all fold test queries",
    )
    parser.add_argument("--log-level", default="INFO")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    for path in (args.annotations, args.folds, args.baseline_tsv,
                 args.source_reference_dir / "references.fasta",
                 args.source_reference_dir / "metadata.sqlite3"):
        if not path.exists():
            raise FileNotFoundError(path)

    started = time.monotonic()
    args.work_dir.mkdir(parents=True, exist_ok=True)
    staging_work = args.work_dir / f".fold_{args.fold}_staging"
    final_work = args.work_dir / f"fold_{args.fold}"
    _reset_generated_dir(staging_work, args.work_dir)
    log.info("loading annotations and fold %d", args.fold)
    annotations = load_annotations(args.annotations)
    fold = load_fold(args.folds, args.fold, annotations)
    split = split_summary(fold, annotations)
    leaked_pairs = [
        pair for pair, count in split["sequence_overlap"].items() if count
    ]
    if leaked_pairs:
        raise ValueError(
            "exact-sequence leakage in supplied fold: " + ", ".join(leaked_pairs)
        )

    log.info("analysing shared BLAST output")
    shared_analysis = analyze_blast_tsv(args.baseline_tsv)
    shared = baseline_report(shared_analysis, fold, annotations)
    if shared["tsv"]["unexpected_queries"] or shared["tsv"]["subjects_outside_train"]:
        raise ValueError("shared BLAST TSV does not conform to the supplied fold")

    reproduction_queries = reproduction_cohort(
        fold["test_acc"], shared_analysis, args.reproduction_queries
    )
    log.info(
        "running BLAST 2.16 reproduction on %d/%d fold queries",
        len(reproduction_queries), len(fold["test_acc"]),
    )
    reproduced_path, run_stats = run_baseline_reproduction(
        annotations, fold["train_acc"], reproduction_queries,
        staging_work, args.threads, args.timeout,
    )
    run_stats["query_cohort"]["shared_no_hits"] = sum(
        query not in shared_analysis.first for query in reproduction_queries
    )
    reproduced_analysis = analyze_blast_tsv(reproduced_path)
    reproduction_fold = {
        "train_acc": fold["train_acc"],
        "val_acc": fold["val_acc"],
        "test_acc": reproduction_queries,
    }
    reproduced_metrics = baseline_report(
        reproduced_analysis, reproduction_fold, annotations
    )
    parity = compare_orders(
        reproduction_queries, shared_analysis, reproduced_analysis
    )
    write_top_hit_diff(
        staging_work / "baseline_top_hit_diff.tsv",
        reproduction_queries, annotations, shared_analysis, reproduced_analysis,
    )

    log.info("building and searching leakage-safe EnzymeX fold")
    enzymex = run_enzymex_benchmark(
        annotations, fold, args.source_reference_dir,
        staging_work, args.threads, args.timeout,
    )

    report = {
        "schema_version": 4,
        "fold": args.fold,
        "inputs": {
            "annotations": {
                "name": args.annotations.name,
                "sha256": file_sha256(args.annotations),
            },
            "folds": {
                "name": args.folds.name,
                "sha256": file_sha256(args.folds),
            },
            "baseline_tsv": {
                "name": args.baseline_tsv.name,
                "sha256": file_sha256(args.baseline_tsv),
            },
        },
        "split": split,
        "shared_baseline": shared,
        "reproduction": {
            "reported_baseline": {
                "tool_version": "BLAST+ 2.5.0",
                "parameters": {
                    "evalue": 1e-5,
                    "comp_based_stats": 2,
                    "max_target_seqs": 100,
                    "outfmt": "6",
                    "threads": 16,
                },
            },
            "run": run_stats,
            "tsv": reproduced_metrics["tsv"],
            "parity": parity,
        },
        "enzymex": enzymex,
        "total_runtime_seconds": round(time.monotonic() - started, 2),
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.output_dir / f"fold_{args.fold}_report.json"
    markdown_path = args.output_dir / f"fold_{args.fold}_report.md"
    json_tmp = json_path.with_suffix(".json.tmp")
    markdown_tmp = markdown_path.with_suffix(".md.tmp")
    json_tmp.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8")
    markdown_tmp.write_text(render_markdown(report), encoding="utf-8")
    _promote_generated_dir(staging_work, final_work, args.work_dir)
    json_tmp.replace(json_path)
    markdown_tmp.replace(markdown_path)
    print(markdown_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
