"""HTTP routes: validation, rendering, escaping, downloads, health."""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.jobs import job_dir, new_job_id, save
from app.schemas import (
    ErrorCode, Hit, JobResult, MethodResult, MethodStatus, QueryResult,
)
from tests.conftest import ADH1_YEAST


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A real app instance wired to a scratch reference dir and no database."""
    monkeypatch.setenv("ENZYMEX_REFERENCE_DIR", str(tmp_path / "reference"))
    monkeypatch.setenv("ENZYMEX_JOB_DIR", str(tmp_path / "jobs"))
    monkeypatch.setenv("ENZYMEX_DB_CONFIRM_COPY", "false")
    monkeypatch.setenv("ENZYMEX_MAX_QUERY_SEQUENCES", "3")
    monkeypatch.setenv("ENZYMEX_MAX_UPLOAD_BYTES", "5000")
    monkeypatch.setenv("ENZYMEX_LOG_LEVEL", "WARNING")
    get_settings.cache_clear()

    from app.web.app import create_app
    with TestClient(create_app()) as c:
        yield c
    get_settings.cache_clear()


def sample_result(job_id: str) -> JobResult:
    return JobResult(
        job_id=job_id,
        reference_build_id="build123",
        requested_methods=["blastp", "phmmer", "hmmscan"],
        queries=[QueryResult(
            query_id="Q1",
            query_description='<script>alert("xss")</script> & "quoted"',
            query_length=347,
            methods=[
                MethodResult(method="blastp", method_version="blastp: 2.16.0+",
                             status=MethodStatus.OK, runtime_seconds=0.5, hits=[
                                 Hit(rank=1, hit_id="EXR1",
                                     hit_description="<b>alcohol</b> dehydrogenase",
                                     hit_ec="1.1.1.1", hit_source="swissprot",
                                     evalue=0.0, bitscore=700.5, percent_identity=98.5,
                                     alignment_length=347, query_coverage=0.99,
                                     subject_coverage=0.99, domain_start=1,
                                     domain_end=347, domain_count=1)]),
                MethodResult(method="phmmer", status=MethodStatus.NO_HITS,
                             method_version="HMMER 3.4"),
                MethodResult(method="hmmscan", status=MethodStatus.FAILED,
                             error_code=ErrorCode.TIMEOUT,
                             error_message="HMMER (hmmscan) did not finish in time."),
            ],
        )],
        total_runtime_seconds=1.2,
        notes=["Profile HMM results cover only part of the reference set."],
    )


def store(client, result: JobResult):
    s = get_settings()
    s.job_dir.mkdir(parents=True, exist_ok=True)
    job_dir(s, result.job_id, create=True)
    save(s, result)


# ---------------------------------------------------------------- submission page
def test_index_renders_with_no_reference_build(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "No reference build on this server yet" in r.text
    assert "not the official EnzymeX production service" in r.text


def test_about_page_explains_the_columns(client):
    r = client.get("/about")
    assert r.status_code == 200
    for term in ("E-value", "Bit score", "Percent identity", "coverage"):
        assert term in r.text


# ---------------------------------------------------------------- validation
@pytest.mark.parametrize("payload,fragment", [
    ("", "No sequence data"),
    ("MKVLLA\nMKV", "does not start with a FASTA header"),
    (">a\n\n", "no residues"),
    (">a\nMKV###\n", "not amino acids"),
    (f">dup x\n{ADH1_YEAST}\n>dup y\n{ADH1_YEAST[:99]}\n", "Duplicate sequence identifier"),
    (f">a\n{ADH1_YEAST}\n>b\n{ADH1_YEAST}\n", "same sequence"),
])
def test_invalid_submissions_are_rejected_with_a_usable_message(client, payload, fragment):
    r = client.post("/search", data={"sequences": payload}, follow_redirects=False)
    assert r.status_code == 400
    assert fragment in r.text


def test_too_many_sequences_is_rejected(client):
    payload = "".join(f">s{i}\n{ADH1_YEAST[:100 + i]}\n" for i in range(4))
    r = client.post("/search", data={"sequences": payload})
    assert r.status_code == 400
    assert "at most 3" in r.text


def test_oversized_paste_is_rejected(client):
    r = client.post("/search", data={"sequences": ">a\n" + "M" * 20_000})
    assert r.status_code == 413


def test_oversized_upload_is_rejected(client):
    r = client.post("/search", files={"fasta_file": ("big.fasta", b"M" * 20_000)})
    assert r.status_code == 413


def test_non_utf8_upload_is_rejected(client):
    r = client.post("/search", files={"fasta_file": ("bad.fasta", b"\xff\xfe\x00binary")})
    assert r.status_code == 400
    assert "not UTF-8" in r.text


def test_rejected_submission_keeps_the_users_input(client):
    r = client.post("/search", data={"sequences": ">a\nMKV###\n"})
    assert "MKV###" in r.text


# ---------------------------------------------------------------- results page
def test_results_page_renders_every_method_state(client):
    result = sample_result(new_job_id())
    store(client, result)
    r = client.get(f"/jobs/{result.job_id}")
    assert r.status_code == 200
    assert "BLAST (blastp)" in r.text
    assert "No hits" in r.text                    # phmmer
    assert "timeout" in r.text                    # hmmscan failure
    assert "unaffected" in r.text                 # partial-failure wording
    assert "EXR1" in r.text and "1.1.1.1" in r.text
    assert "build123" in r.text
    assert "&lt;1e-180" in r.text                 # BLAST 0.0 rendered honestly


def test_user_supplied_text_is_html_escaped(client):
    result = sample_result(new_job_id())
    store(client, result)
    body = client.get(f"/jobs/{result.job_id}").text
    assert "<script>alert" not in body
    assert "&lt;script&gt;" in body
    assert "<b>alcohol</b>" not in body
    assert "&lt;b&gt;alcohol" in body


def test_results_page_does_not_leak_filesystem_paths(client):
    result = sample_result(new_job_id())
    store(client, result)
    body = client.get(f"/jobs/{result.job_id}").text
    assert "/var/" not in body and "/tmp/" not in body
    assert str(get_settings().job_dir) not in body


@pytest.mark.parametrize("bad_id", ["../../etc/passwd", "nope", "0" * 17])
def test_bad_job_ids_return_404(client, bad_id):
    assert client.get(f"/jobs/{bad_id}").status_code == 404


def test_unknown_job_gets_a_helpful_404(client):
    r = client.get(f"/jobs/{new_job_id()}")
    assert r.status_code == 404
    assert "deleted after" in r.text


# ---------------------------------------------------------------- downloads
def test_json_download_matches_the_stored_result(client):
    result = sample_result(new_job_id())
    store(client, result)
    body = client.get(f"/jobs/{result.job_id}/results.json").json()
    assert body["job_id"] == result.job_id
    assert body["queries"][0]["methods"][0]["hits"][0]["hit_ec"] == "1.1.1.1"


def test_csv_download_uses_the_flat_schema(client):
    from app.schemas import FLAT_SCHEMA
    result = sample_result(new_job_id())
    store(client, result)
    r = client.get(f"/jobs/{result.job_id}/results.csv")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    lines = r.text.strip().split("\n")
    assert lines[0] == ",".join(FLAT_SCHEMA)
    assert len(lines) == 4          # one row per method, hits expanded


def test_downloads_for_a_missing_job_are_404(client):
    jid = new_job_id()
    assert client.get(f"/jobs/{jid}/results.json").status_code == 404
    assert client.get(f"/jobs/{jid}/results.csv").status_code == 404


# ---------------------------------------------------------------- health
def test_health_reports_unavailable_without_a_build_and_hides_the_password(client):
    r = client.get("/health")
    assert r.status_code == 503
    body = r.json()
    assert body["status"] == "unavailable"
    assert body["artifacts"]["references_fasta"] is False
    assert body["copied_database"]["reachable"] is False
    assert "@" in body["copied_database"]["target"]
    assert not re.search(r"password", r.text, re.I)


def test_health_lists_method_availability(client):
    body = client.get("/health").json()
    assert set(body["methods"]) == {"blastp", "phmmer", "hmmscan"}
    assert all("enabled" in v and "ok" in v for v in body["methods"].values())
