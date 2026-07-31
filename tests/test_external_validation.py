from __future__ import annotations

import io
import json
import pickle
from pathlib import Path

import pytest

from app.config import Settings
from app.references.cluster import read_fasta
from app.references.metadata import connect_write
from app.search.blast import blastp_args
from scripts.validate_external import (
    Annotation,
    RestrictedUnpickler,
    analyze_blast_tsv,
    benchmark_settings,
    compare_orders,
    evaluate_hits,
    in_annotation_order,
    load_fold,
    prepare_filtered_reference,
    render_markdown,
    reproduction_cohort,
    sequence_sha256,
    split_summary,
    write_fasta,
)


def annotation(sequence: str, *ecs: str) -> Annotation:
    return Annotation(sequence, tuple(ecs), sequence_sha256(sequence))


def blast_row(query: str, subject: str, identity: float, length: int,
              evalue: float, bitscore: float) -> str:
    return (
        f"{query}\t{subject}\t{identity}\t{length}\t0\t0\t1\t{length}\t"
        f"1\t{length}\t{evalue}\t{bitscore}\n"
    )


def test_write_fasta_keeps_requested_order_and_wraps(tmp_path: Path):
    annotations = {
        "A": annotation("A" * 61, "1.1.1.1"),
        "B": annotation("BCDE", "2.2.2.2"),
    }
    path = tmp_path / "queries.fasta"
    assert write_fasta(path, ["B", "A"], annotations) == 2
    assert path.read_text() == f">B\nBCDE\n>A\n{'A' * 60}\nA\n"


def test_annotation_order_matches_notebook_dataframe_filter():
    annotations = {
        "A": annotation("AAAA", "1.1.1.1"),
        "B": annotation("BBBB", "1.1.1.1"),
        "C": annotation("CCCC", "1.1.1.1"),
    }
    assert in_annotation_order(annotations, ["C", "A"]) == ["A", "C"]


def test_load_fold_rejects_accession_overlap(tmp_path: Path):
    annotations = {
        "A": annotation("AAAA", "1.1.1.1"),
        "B": annotation("BBBB", "1.1.1.1"),
        "C": annotation("CCCC", "1.1.1.1"),
    }
    path = tmp_path / "fold.pkl"
    path.write_bytes(pickle.dumps({
        "fold_0": {
            "train_acc": ["A", "B"],
            "val_acc": ["C"],
            "test_acc": ["B"],
        }
    }))
    with pytest.raises(ValueError, match="accession leakage"):
        load_fold(path, 0, annotations)


def test_split_summary_detects_same_sequence_under_different_accessions():
    annotations = {
        "TRAIN": annotation("ACDE", "1.1.1.1"),
        "VAL": annotation("VVVV", "1.1.1.1"),
        "TEST": annotation("ACDE", "1.1.1.1"),
    }
    fold = {
        "train_acc": ["TRAIN"],
        "val_acc": ["VAL"],
        "test_acc": ["TEST"],
    }
    assert split_summary(fold, annotations)["sequence_overlap"]["train_test"] == 1


def test_restricted_unpickler_blocks_globals():
    payload = pickle.dumps(eval)
    with pytest.raises(pickle.UnpicklingError, match="blocked global"):
        RestrictedUnpickler(io.BytesIO(payload)).load()


def test_streaming_blast_analysis_matches_production_ranking(tmp_path: Path):
    path = tmp_path / "hits.tsv"
    path.write_text(
        blast_row("Q1", "S1", 50, 100, 1e-30, 120)
        + blast_row("Q1", "S1", 60, 20, 1e-8, 30)
        + blast_row("Q1", "S2", 95, 15, 1e-5, 25)
        + blast_row("Q2", "S3", 70, 80, 1e-10, 80),
        encoding="utf-8",
    )
    result = analyze_blast_tsv(path)
    assert result.rows == 4
    assert result.duplicate_hsps == 1
    assert result.first["Q1"].subject == "S1"
    assert result.ranked["Q1"].subject == "S1"
    assert result.ranked["Q1"].bitscore == 120
    assert result.max_identity["Q1"].subject == "S2"
    assert result.raw_order["Q1"] == ["S1", "S2"]


def test_evaluation_keeps_no_hits_in_denominator():
    truth = {
        "Q1": annotation("AAAA", "1.1.1.1"),
        "Q2": annotation("BBBB", "2.2.2.2"),
    }
    metrics = evaluate_hits(
        ["Q1", "Q2"],
        {"Q1": "S1"},
        truth,
        {"S1": ("1.1.1.1",)},
    )
    assert metrics["predictions"] == 1
    assert metrics["no_hits"] == 1
    assert metrics["exact_ec_set"] == 1
    assert metrics["exact_ec_set_rate"] == 0.5
    assert metrics["ec_token_micro_recall"] == 0.5


def test_compare_orders_counts_queries_missing_from_both(tmp_path: Path):
    left_path = tmp_path / "left.tsv"
    right_path = tmp_path / "right.tsv"
    left_path.write_text(blast_row("Q1", "S1", 50, 100, 1e-20, 100))
    right_path.write_text(blast_row("Q1", "S2", 50, 100, 1e-20, 100))
    result = compare_orders(
        ["Q1", "Q2"],
        analyze_blast_tsv(left_path),
        analyze_blast_tsv(right_path),
    )
    assert result["queries_with_hits_in_both"] == 1
    assert result["raw_top1_same"] == 0
    assert result["raw_top1_score_same"] == 1
    assert result["raw_top1_tied_score_id_changes"] == 1
    assert result["no_hits_in_both"] == 1


def test_reproduction_cohort_keeps_every_no_hit(tmp_path: Path):
    path = tmp_path / "hits.tsv"
    path.write_text(
        blast_row("Q1", "S1", 50, 100, 1e-20, 100)
        + blast_row("Q3", "S3", 50, 100, 1e-20, 100)
    )
    cohort = reproduction_cohort(
        ["Q1", "Q2", "Q3", "Q4"], analyze_blast_tsv(path), 3
    )
    assert {"Q2", "Q4"} <= set(cohort)
    assert len(cohort) == 3


def test_filtered_reference_removes_hash_matches(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "references.fasta").write_text(
        ">EXR1\nACDE\n>EXR2\nVVVV\n", encoding="ascii"
    )
    store = connect_write(source / "metadata.sqlite3")
    store.executemany(
        "INSERT INTO reference "
        "(ref_id, source_pk, description, ec, source, length, sequence_sha256, "
        "motif, active_site, binding_site, interpretation) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        [
            ("EXR1", "1", "one", "1.1.1.1", "swissprot", 4,
             sequence_sha256("ACDE"), None, None, None, None),
            ("EXR2", "2", "two", "2.2.2.2", "swissprot", 4,
             sequence_sha256("VVVV"), None, None, None, None),
        ],
    )
    store.commit()
    store.close()

    work = tmp_path / "work"
    work.mkdir()
    destination = work / "filtered"
    stats = prepare_filtered_reference(
        source, destination, {sequence_sha256("ACDE")}, work
    )
    assert stats["removed_reference_records_matching_test_hashes"] == 1
    assert read_fasta(destination / "references.fasta") == {"EXR2": "VVVV"}


def test_blastp_args_are_shared_with_request_path(tmp_path: Path):
    settings = Settings(
        _env_file=None,
        reference_dir=tmp_path / "reference",
        job_dir=tmp_path / "jobs",
        blast_evalue=1e-5,
        blast_max_target_seqs=100,
        search_threads=16,
    )
    args = blastp_args(settings, tmp_path / "query.fasta", tmp_path / "hits.tsv")
    assert args[args.index("-evalue") + 1] == "1e-05"
    assert args[args.index("-max_target_seqs") + 1] == "100"
    assert args[args.index("-comp_based_stats") + 1] == "2"
    assert args[args.index("-num_threads") + 1] == "16"


def test_benchmark_settings_pin_search_and_profile_parameters(tmp_path: Path):
    settings = benchmark_settings(
        tmp_path / "reference", tmp_path / "jobs", threads=8, timeout=900
    )
    assert settings.blast_evalue == 1e-3
    assert settings.blast_max_target_seqs == 500
    assert settings.phmmer_evalue == settings.hmmscan_evalue == 1e-3
    assert settings.cluster_min_seq_id == 0.35
    assert settings.cluster_coverage == 0.8
    assert settings.profile_min_members == 5
    assert settings.profile_max_members == 500


def test_committed_report_matches_renderer():
    root = Path(__file__).parents[1]
    report_path = root / "results" / "validation" / "fold_0_report.json"
    markdown_path = report_path.with_suffix(".md")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["schema_version"] == 4
    assert render_markdown(report) == markdown_path.read_text(encoding="utf-8")
