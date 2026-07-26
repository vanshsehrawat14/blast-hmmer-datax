"""Orchestration: method selection, partial failure, metadata resolution.

The tool runners are replaced with stubs here so these tests describe the
service's own behaviour rather than BLAST's. Real tool execution is covered in
test_e2e.py.
"""

from __future__ import annotations

import sqlite3

import pytest

from app.fasta import SequenceRecord
from app.references.metadata import connect_write
from app.schemas import ErrorCode, MethodStatus
from app.search import service
from app.search.outcome import SearchOutcome
from app.search.parsers import RawHit
from tests.conftest import ADH1_YEAST

RECORDS = [
    SequenceRecord("Q1", "first protein", ADH1_YEAST),
    SequenceRecord("Q2", "second protein", ADH1_YEAST[:200]),
]


@pytest.fixture
def built(settings):
    """Minimal reference artifacts: metadata plus the files the runners check."""
    settings.reference_dir.mkdir(parents=True, exist_ok=True)
    settings.references_fasta.write_text(">EXR1\nMKV\n")
    store = connect_write(settings.metadata_db)
    store.execute(
        "INSERT INTO reference (ref_id, source_pk, description, ec, source, "
        "length, sequence_sha256) VALUES (?,?,?,?,?,?,?)",
        ("EXR1", "1", "alcohol dehydrogenase 1", "1.1.1.1", "swissprot", 347, "x"),
    )
    store.execute(
        "INSERT INTO profile (family_id, members, consensus_ec, ec_purity, "
        "ec_distribution, median_length, match_states, mean_pairwise_identity, "
        "representative_ref_id, description) VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("EXF00001", 42, "1.1.1.1", 0.93, '{"1.1.1.1": 39}', 350, 340, 0.55,
         "EXR1", "42 members"),
    )
    store.commit()
    store.close()
    return settings


def stub(method, hits_by_query=None, **kw):
    def runner(settings, job_dir, query_fasta):
        return SearchOutcome(method=method, version=f"{method} 1.0", runtime=0.1,
                             hits_by_query=hits_by_query or {}, **kw)
    return runner


def test_disabled_methods_are_excluded_and_reported(built, monkeypatch):
    built.enable_profile_hmm = False
    monkeypatch.setitem(service.RUNNERS, "blastp", stub("blastp"))
    monkeypatch.setitem(service.RUNNERS, "phmmer", stub("phmmer"))

    result = service.run_search(built, RECORDS, ["blastp", "hmmscan"])
    statuses = {m.method: m.status for m in result.queries[0].methods}
    assert statuses["blastp"] == MethodStatus.NO_HITS
    assert statuses["hmmscan"] == MethodStatus.DISABLED
    assert "phmmer" not in statuses          # not requested
    assert result.requested_methods == ["blastp"]


def test_no_methods_requested_runs_everything_enabled(built, monkeypatch):
    for m in ("blastp", "phmmer", "hmmscan"):
        monkeypatch.setitem(service.RUNNERS, m, stub(m))
    result = service.run_search(built, RECORDS, [])
    assert result.requested_methods == ["blastp", "phmmer", "hmmscan"]


def test_one_method_failing_does_not_affect_the_others(built, monkeypatch):
    monkeypatch.setitem(service.RUNNERS, "blastp", stub(
        "blastp", {"Q1": [RawHit("Q1", "EXR1", 1e-99, 400.0, percent_identity=98.0)]}))
    monkeypatch.setitem(service.RUNNERS, "phmmer", lambda s, d, q: SearchOutcome.failure(
        "phmmer", ErrorCode.TIMEOUT, "HMMER (phmmer) did not finish within 300 seconds."))
    monkeypatch.setitem(service.RUNNERS, "hmmscan", stub("hmmscan"))

    result = service.run_search(built, RECORDS)
    by_method = {m.method: m for m in result.queries[0].methods}
    assert by_method["blastp"].status == MethodStatus.OK
    assert by_method["phmmer"].status == MethodStatus.FAILED
    assert by_method["phmmer"].error_code == ErrorCode.TIMEOUT
    assert by_method["hmmscan"].status == MethodStatus.NO_HITS


def test_hits_are_attached_to_the_right_query(built, monkeypatch):
    monkeypatch.setitem(service.RUNNERS, "blastp", stub("blastp", {
        "Q1": [RawHit("Q1", "EXR1", 1e-99, 400.0)],
        "Q2": [RawHit("Q2", "EXR1", 0.004, 22.0)],
    }))
    built.enable_phmmer = built.enable_profile_hmm = False
    result = service.run_search(built, RECORDS, ["blastp"])
    q1, q2 = result.queries
    assert q1.query_id == "Q1" and q1.methods[0].hits[0].evalue == 1e-99
    assert q2.query_id == "Q2" and q2.methods[0].hits[0].evalue == 0.004


def test_reference_and_profile_metadata_are_resolved(built, monkeypatch):
    monkeypatch.setitem(service.RUNNERS, "blastp", stub(
        "blastp", {"Q1": [RawHit("Q1", "EXR1", 1e-99, 400.0)]}))
    monkeypatch.setitem(service.RUNNERS, "hmmscan", stub(
        "hmmscan", {"Q1": [RawHit("Q1", "EXF00001", 1e-80, 300.0)]}))
    built.enable_phmmer = False

    result = service.run_search(built, RECORDS, ["blastp", "hmmscan"])
    by_method = {m.method: m for m in result.queries[0].methods}

    blast_hit = by_method["blastp"].hits[0]
    assert blast_hit.hit_ec == "1.1.1.1"
    assert blast_hit.hit_source == "swissprot"
    assert blast_hit.hit_description == "alcohol dehydrogenase 1"

    profile_hit = by_method["hmmscan"].hits[0]
    assert profile_hit.family_id == "EXF00001"
    assert profile_hit.family_size == 42
    assert profile_hit.family_ec_purity == 0.93
    assert profile_hit.hit_source == "profile HMM"


def test_unknown_hit_id_leaves_metadata_null_rather_than_guessing(built, monkeypatch):
    monkeypatch.setitem(service.RUNNERS, "blastp", stub(
        "blastp", {"Q1": [RawHit("Q1", "EXR_MISSING", 1e-5, 50.0)]}))
    built.enable_phmmer = built.enable_profile_hmm = False
    hit = service.run_search(built, RECORDS, ["blastp"]).queries[0].methods[0].hits[0]
    assert hit.hit_ec is None and hit.hit_source is None and hit.hit_description is None


def test_flat_rows_cover_every_query_method_pair(built, monkeypatch):
    monkeypatch.setitem(service.RUNNERS, "blastp", stub(
        "blastp", {"Q1": [RawHit("Q1", "EXR1", 1e-99, 400.0)]}))
    built.enable_phmmer = built.enable_profile_hmm = False
    rows = service.run_search(built, RECORDS, ["blastp"]).flat_rows()
    # Q1 has one hit, Q2 has none but still emits a status row.
    assert len(rows) == 2
    assert {r["query_id"] for r in rows} == {"Q1", "Q2"}
    assert [r["status"] for r in rows] == ["ok", "no_hits"]
    assert rows[1]["hit_id"] is None


def test_concurrency_bound_rejects_extra_work(built, monkeypatch):
    built.max_concurrent_jobs = 1
    held = service._slots.acquire(1)
    try:
        with pytest.raises(service.ServerBusy):
            service.run_search(built, RECORDS, ["blastp"])
    finally:
        held.release()


def test_missing_metadata_database_degrades_instead_of_raising(settings, monkeypatch):
    settings.reference_dir.mkdir(parents=True, exist_ok=True)
    settings.enable_phmmer = settings.enable_profile_hmm = False
    monkeypatch.setitem(service.RUNNERS, "blastp", stub(
        "blastp", {"Q1": [RawHit("Q1", "EXR1", 1e-9, 90.0)]}))
    result = service.run_search(settings, RECORDS, ["blastp"])
    assert result.queries[0].methods[0].hits[0].hit_ec is None


def test_results_are_persisted_and_reloadable(built, monkeypatch):
    monkeypatch.setitem(service.RUNNERS, "blastp", stub("blastp"))
    built.enable_phmmer = built.enable_profile_hmm = False
    result = service.run_search(built, RECORDS, ["blastp"])
    from app.jobs import load
    assert load(built, result.job_id).job_id == result.job_id


def test_raw_outputs_are_discarded_when_configured(built, monkeypatch):
    built.keep_raw_outputs = False
    built.enable_phmmer = built.enable_profile_hmm = False

    def runner_writing_junk(settings, job_dir, query_fasta):
        (job_dir / "blastp_hits.tsv").write_text("raw output")
        return SearchOutcome(method="blastp", version="x", runtime=0.1)

    monkeypatch.setitem(service.RUNNERS, "blastp", runner_writing_junk)
    result = service.run_search(built, RECORDS, ["blastp"])
    from app.jobs import job_dir
    assert [p.name for p in job_dir(built, result.job_id).iterdir()] == ["results.json"]


def test_metadata_store_is_opened_read_only(built):
    from app.references.metadata import MetadataStore
    with MetadataStore(built.metadata_db) as store:
        with pytest.raises(sqlite3.OperationalError):
            store._conn.execute("DELETE FROM reference")
