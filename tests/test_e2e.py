"""End-to-end: build real artifacts, run the real tools, check the real page.

Uses the 20 curated EC 1.1.1.1 reference sequences and the held-out positive
and negative queries that the original proof of concept downloaded from
UniProt and committed under `data/raw/`. They are a small, biologically
meaningful, offline fixture: the positives must produce strong evidence and
the negatives must not.

Skipped when BLAST+/HMMER are not installed.
"""

from __future__ import annotations

import shutil

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.fasta import parse_submission
from app.references import blast_build
from app.references.cli import build_hmmer_layer
from app.references.metadata import connect_write
from app.references.manifest import write_manifest
from app.schemas import ErrorCode, MethodStatus
from app.search.blast import run_blastp
from app.search.hmmer import run_hmmscan, run_phmmer
from app.search.service import run_search
from tests.conftest import REPO_ROOT, needs_tools

pytestmark = [pytest.mark.tools, needs_tools]

POC_DATA = REPO_ROOT / "data" / "raw"
REFERENCE_FASTA = POC_DATA / "reference_ec_1_1_1_1.fasta"
POSITIVES = POC_DATA / "queries_positive.fasta"
NEGATIVES = POC_DATA / "queries_negative.fasta"


def _read_records(path):
    """Split a committed FASTA into (header, sequence) pairs."""
    from app.fasta import parse_fasta
    return parse_fasta(path.read_text())


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """A real reference build over the committed EC 1.1.1.1 sequences."""
    if not REFERENCE_FASTA.exists():
        pytest.skip("proof-of-concept reference FASTA not present")

    root = tmp_path_factory.mktemp("e2e")
    from app.config import Settings
    s = Settings(
        _env_file=None,
        db_password="unused",
        reference_dir=root / "reference",
        job_dir=root / "jobs",
        # 20 sequences is a small set: allow smaller families than the
        # production default so the profile layer is exercised at all.
        profile_min_members=3,
        profile_min_match_states=40,
        build_threads=2,
        search_threads=1,
    )
    s.reference_dir.mkdir(parents=True, exist_ok=True)

    # Stand in for the export step: write references with our own identifiers
    # and a metadata database, exactly as export_references would.
    store = connect_write(s.metadata_db)
    with s.references_fasta.open("w", encoding="ascii", newline="\n") as fh:
        for i, (header, seq) in enumerate(_read_records(REFERENCE_FASTA), start=1):
            ref_id = f"EXR{i}"
            fh.write(f">{ref_id} EC=1.1.1.1 src=swissprot\n")
            for j in range(0, len(seq), 60):
                fh.write(seq[j:j + 60] + "\n")
            store.execute(
                "INSERT INTO reference (ref_id, source_pk, description, ec, source, "
                "length, sequence_sha256) VALUES (?,?,?,?,?,?,?)",
                (ref_id, str(i), header[:200], "1.1.1.1", "swissprot", len(seq), f"h{i}"),
            )
    store.commit()
    store.close()

    blast_build.build(s, "e2e")
    if shutil.which(s.mmseqs_bin) and shutil.which(s.mafft_bin):
        build_hmmer_layer(s)
    write_manifest(s, {"export": {"exported": 20, "fasta_sha256": "e2e"}})
    return s


def _submission(path, limit=3):
    records = _read_records(path)[:limit]
    return "".join(f">{h}\n{s}\n" for h, s in records)


# ---------------------------------------------------------------- the pipeline
def test_blast_database_was_built(built):
    ok, detail = blast_build.available(built)
    assert ok, detail


def test_positive_queries_produce_strong_evidence(built):
    records = parse_submission(_submission(POSITIVES), max_sequences=5, max_length=5000)
    result = run_search(built, records)

    for q in result.queries:
        by_method = {m.method: m for m in q.methods}
        for method in ("blastp", "phmmer"):
            m = by_method[method]
            assert m.status == MethodStatus.OK, f"{method} on {q.query_id}: {m.error_message}"
            best = m.hits[0]
            # Held-out EC 1.1.1.1 proteins against an EC 1.1.1.1 reference set:
            # the top hit should be unambiguous, not marginal.
            assert best.evalue < 1e-20
            assert best.hit_ec == "1.1.1.1"
            assert best.hit_source == "swissprot"
            assert best.query_coverage is not None and best.query_coverage > 0.5


def test_negative_controls_produce_weak_or_no_evidence(built):
    records = parse_submission(_submission(NEGATIVES), max_sequences=5, max_length=5000)
    result = run_search(built, records)

    for q in result.queries:
        for m in q.methods:
            assert m.status in (MethodStatus.OK, MethodStatus.NO_HITS)
            # An alpha-amylase or hexokinase must not look like an alcohol
            # dehydrogenase. Anything reported at all must be far weaker than
            # the 1e-20 the positives clear.
            for hit in m.hits:
                assert hit.evalue > 1e-10, f"{m.method} called {hit.hit_id} on a negative"


def test_multiple_sequences_land_under_the_right_query(built):
    text = _submission(POSITIVES, limit=2) + _submission(NEGATIVES, limit=1)
    records = parse_submission(text, max_sequences=5, max_length=5000)
    result = run_search(built, records)

    assert [q.query_id for q in result.queries] == ["Q1", "Q2", "Q3"]
    # Each query's headers are preserved and distinct.
    assert len({q.query_description for q in result.queries}) == 3
    positives = [q for q in result.queries[:2] for m in q.methods if m.hits]
    assert positives, "positive queries produced no hits at all"
    negative = result.queries[2]
    strong = [h for m in negative.methods for h in m.hits if h.evalue < 1e-10]
    assert not strong


def test_profile_layer_when_present(built):
    from app.references.hmmer_build import profiles_available
    ok, detail = profiles_available(built)
    if not ok:
        pytest.skip(f"profile layer not built: {detail}")

    records = parse_submission(_submission(POSITIVES, limit=1),
                               max_sequences=5, max_length=5000)
    result = run_search(built, records, ["hmmscan"])
    m = result.queries[0].methods[0]
    assert m.method == "hmmscan"
    assert m.status == MethodStatus.OK
    hit = m.hits[0]
    assert hit.family_id and hit.family_size and hit.family_size >= 3
    assert hit.evalue < 1e-10
    assert hit.domain_start and hit.domain_end and hit.domain_start < hit.domain_end


# ---------------------------------------------------------------- failure paths
def test_missing_executable_is_reported_not_raised(built, tmp_path):
    s = built.model_copy(update={"blastp_bin": "definitely-not-installed-xyz"})
    outcome = run_blastp(s, tmp_path, built.references_fasta)
    assert outcome.error_code == ErrorCode.EXECUTABLE_MISSING
    assert "not installed" in outcome.error_message


def test_nonzero_exit_is_reported_not_raised(built, tmp_path):
    s = built.model_copy(update={"reference_dir": tmp_path / "nowhere"})
    (tmp_path / "nowhere" / "blastdb").mkdir(parents=True)
    # A .pin that is not a real BLAST index gets past the presence check and
    # makes blastp fail the way a corrupt build would.
    (tmp_path / "nowhere" / "blastdb" / "references.pin").write_bytes(b"not an index")
    outcome = run_blastp(s, tmp_path, built.references_fasta)
    assert outcome.error_code == ErrorCode.NONZERO_EXIT


def test_timeout_is_reported_not_raised(built, tmp_path):
    s = built.model_copy(update={"blast_timeout_seconds": 0})
    outcome = run_blastp(s, tmp_path, built.references_fasta)
    assert outcome.error_code == ErrorCode.TIMEOUT
    assert "did not finish within" in outcome.error_message


def test_absent_reference_artifacts_are_reported(built, tmp_path):
    s = built.model_copy(update={"reference_dir": tmp_path / "empty"})
    assert run_blastp(s, tmp_path, built.references_fasta).error_code == ErrorCode.REFERENCE_MISSING
    assert run_phmmer(s, tmp_path, built.references_fasta).error_code == ErrorCode.REFERENCE_MISSING
    assert run_hmmscan(s, tmp_path, built.references_fasta).error_code == ErrorCode.REFERENCE_MISSING


def test_job_directories_do_not_contaminate_each_other(built):
    records = parse_submission(_submission(POSITIVES, limit=1),
                               max_sequences=5, max_length=5000)
    a = run_search(built, records, ["blastp"])
    b = run_search(built, records, ["blastp"])
    assert a.job_id != b.job_id

    from app.jobs import job_dir
    da, db = job_dir(built, a.job_id), job_dir(built, b.job_id)
    assert da != db
    assert (da / "query.fasta").read_text() == (db / "query.fasta").read_text()
    assert (da / "blastp_hits.tsv").exists() and (db / "blastp_hits.tsv").exists()


# ---------------------------------------------------------------- through HTTP
def test_full_workflow_through_the_web_application(built, monkeypatch):
    monkeypatch.setenv("ENZYMEX_REFERENCE_DIR", str(built.reference_dir))
    monkeypatch.setenv("ENZYMEX_JOB_DIR", str(built.job_dir))
    monkeypatch.setenv("ENZYMEX_DB_CONFIRM_COPY", "false")
    monkeypatch.setenv("ENZYMEX_LOG_LEVEL", "WARNING")
    get_settings.cache_clear()

    from app.web.app import create_app
    with TestClient(create_app()) as client:
        health = client.get("/health").json()
        assert health["status"] in ("ok", "degraded")
        assert health["artifacts"]["blast_db"] is True

        assert client.get("/").status_code == 200

        payload = _submission(POSITIVES, limit=1) + _submission(NEGATIVES, limit=1)
        r = client.post("/search", data={"sequences": payload}, follow_redirects=False)
        assert r.status_code == 303
        job_url = r.headers["location"]

        page = client.get(job_url)
        assert page.status_code == 200
        assert "BLAST (blastp)" in page.text
        assert "HMMER (phmmer)" in page.text
        assert "1.1.1.1" in page.text

        rows = client.get(job_url + "/results.csv").text.strip().split("\n")
        assert len(rows) > 2
        assert rows[0].startswith("job_id,query_id,query_description")

        data = client.get(job_url + "/results.json").json()
        assert len(data["queries"]) == 2
    get_settings.cache_clear()


# ---------------------------------------------------------------- upload path
def test_file_upload_is_accepted(built, monkeypatch):
    monkeypatch.setenv("ENZYMEX_REFERENCE_DIR", str(built.reference_dir))
    monkeypatch.setenv("ENZYMEX_JOB_DIR", str(built.job_dir))
    monkeypatch.setenv("ENZYMEX_LOG_LEVEL", "WARNING")
    get_settings.cache_clear()

    from app.web.app import create_app
    with TestClient(create_app()) as client:
        payload = _submission(POSITIVES, limit=1).encode()
        r = client.post("/search", files={"fasta_file": ("q.fasta", payload)},
                        data={"methods": ["blastp"]}, follow_redirects=False)
        assert r.status_code == 303
        page = client.get(r.headers["location"])
        assert "BLAST (blastp)" in page.text
        # Only the requested method ran.
        assert "HMMER (phmmer)" not in page.text
    get_settings.cache_clear()
