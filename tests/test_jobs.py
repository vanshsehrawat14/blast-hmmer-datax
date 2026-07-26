"""Job identifiers, directory isolation, persistence and cleanup."""

from __future__ import annotations

import os
import time

import pytest

from app.jobs import (
    InvalidJobId, cleanup, discard_raw_outputs, job_dir, load, new_job_id, save,
)
from app.schemas import JobResult, MethodResult, MethodStatus, QueryResult


def make_result(job_id: str) -> JobResult:
    return JobResult(
        job_id=job_id, reference_build_id="abc123", requested_methods=["blastp"],
        queries=[QueryResult(query_id="Q1", query_description="test", query_length=10,
                             methods=[MethodResult(method="blastp",
                                                   status=MethodStatus.NO_HITS)])],
    )


def test_new_job_ids_are_random_hex():
    ids = {new_job_id() for _ in range(50)}
    assert len(ids) == 50
    assert all(len(i) == 16 and all(c in "0123456789abcdef" for c in i) for i in ids)


@pytest.mark.parametrize("bad", [
    "../etc", "..", "/etc/passwd", "g" * 16, "0123456789abcde", "0123456789ABCDEF",
    "0123456789abcdef0", "", "./0123456789abcdef", "0123456789abcde/",
])
def test_path_traversal_and_malformed_ids_are_refused(settings, bad):
    with pytest.raises(InvalidJobId):
        job_dir(settings, bad)


def test_each_job_gets_its_own_directory(settings):
    a, b = new_job_id(), new_job_id()
    settings.job_dir.mkdir(parents=True, exist_ok=True)
    da = job_dir(settings, a, create=True)
    db = job_dir(settings, b, create=True)
    assert da != db and da.is_dir() and db.is_dir()
    # Creating the same job twice must fail rather than reuse a directory
    # another request may still be writing into.
    with pytest.raises(FileExistsError):
        job_dir(settings, a, create=True)


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_job_directory_is_private(settings):
    settings.job_dir.mkdir(parents=True, exist_ok=True)
    d = job_dir(settings, new_job_id(), create=True)
    assert d.stat().st_mode & 0o777 == 0o700


def test_round_trip_persistence(settings):
    settings.job_dir.mkdir(parents=True, exist_ok=True)
    jid = new_job_id()
    job_dir(settings, jid, create=True)
    save(settings, make_result(jid))
    loaded = load(settings, jid)
    assert loaded is not None
    assert loaded.job_id == jid
    assert loaded.queries[0].methods[0].status == MethodStatus.NO_HITS


def test_missing_and_corrupt_jobs_load_as_none(settings):
    settings.job_dir.mkdir(parents=True, exist_ok=True)
    assert load(settings, new_job_id()) is None
    assert load(settings, "not-a-job-id") is None

    jid = new_job_id()
    d = job_dir(settings, jid, create=True)
    (d / "results.json").write_text("{ this is not json")
    assert load(settings, jid) is None


def test_discard_raw_outputs_keeps_only_results(settings):
    settings.job_dir.mkdir(parents=True, exist_ok=True)
    jid = new_job_id()
    d = job_dir(settings, jid, create=True)
    save(settings, make_result(jid))
    (d / "blastp_hits.tsv").write_text("junk")
    (d / "sub").mkdir()
    discard_raw_outputs(settings, jid)
    assert [p.name for p in d.iterdir()] == ["results.json"]


def test_cleanup_removes_only_expired_jobs(settings):
    settings.job_dir.mkdir(parents=True, exist_ok=True)
    fresh, stale = new_job_id(), new_job_id()
    job_dir(settings, fresh, create=True)
    old = job_dir(settings, stale, create=True)
    past = time.time() - settings.job_retention_hours * 3600 - 60
    os.utime(old, (past, past))

    # A stray directory that is not a job id must be left alone.
    (settings.job_dir / "not-a-job").mkdir()

    assert cleanup(settings) == 1
    assert job_dir(settings, fresh).exists()
    assert not old.exists()
    assert (settings.job_dir / "not-a-job").exists()
