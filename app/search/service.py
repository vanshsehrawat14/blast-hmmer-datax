"""Orchestration: run the selected methods and assemble a normalized result.

This module is the intended integration point. It depends on the settings
object, the reference artifacts and the tool wrappers — not on FastAPI, not on
templates, not on any HTTP concept. Dropping it into EnzymeX means calling
`run_search()` and rendering `JobResult` however that application renders
things.

Methods run in sequence and are independent: one failing does not stop the
others, and a per-method status travels with the result so a partial outcome
is visible rather than silently thinned.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time

from app.config import Settings
from app.fasta import SequenceRecord, write_fasta
from app.jobs import cleanup, discard_raw_outputs, job_dir, new_job_id, save
from app.references import blast_build, hmmer_build
from app.references.manifest import read_manifest
from app.references.metadata import MetadataStore, ProfileMeta, ReferenceMeta
from app.schemas import (
    Hit, JobResult, Method, MethodResult, MethodStatus, QueryResult,
)
from app.search.blast import run_blastp
from app.search.hmmer import run_hmmscan, run_phmmer
from app.search.outcome import SearchOutcome
from app.search.params import SearchParams
from app.search.parsers import RawHit

log = logging.getLogger(__name__)

ALL_METHODS: tuple[Method, ...] = ("blastp", "phmmer", "hmmscan")

RUNNERS = {
    "blastp": run_blastp,
    "phmmer": run_phmmer,
    "hmmscan": run_hmmscan,
}


class ServerBusy(RuntimeError):
    """Raised when the concurrency bound is already saturated."""


class _Slots:
    """Bounds concurrent searches for the whole process.

    Synchronous execution is fine for a test server, but only if the number of
    simultaneous blastp/phmmer processes is capped: each one takes threads and
    memory proportional to the reference database, and an unbounded queue turns
    a slow request into an out-of-memory kill.
    """

    def __init__(self) -> None:
        self._sem: threading.BoundedSemaphore | None = None
        self._size = 0
        self._lock = threading.Lock()

    def acquire(self, size: int):
        with self._lock:
            if self._sem is None or self._size != size:
                self._sem = threading.BoundedSemaphore(max(1, size))
                self._size = size
            sem = self._sem
        if not sem.acquire(blocking=False):
            raise ServerBusy(
                "This test server is already running its maximum number of "
                "concurrent searches. Try again in a moment."
            )
        return sem


_slots = _Slots()


def resolve_methods(settings: Settings, requested: list[str] | None) -> list[Method]:
    """Intersect what was asked for with what is enabled, preserving order."""
    enabled = {
        "blastp": settings.enable_blast,
        "phmmer": settings.enable_phmmer,
        "hmmscan": settings.enable_profile_hmm,
    }
    wanted = [m for m in (requested or list(ALL_METHODS)) if m in ALL_METHODS]
    if not wanted:
        wanted = list(ALL_METHODS)
    return [m for m in ALL_METHODS if m in wanted and enabled[m]]


def run_search(settings: Settings, records: list[SequenceRecord],
               requested_methods: list[str] | None = None,
               job_id: str | None = None,
               params: SearchParams | None = None) -> JobResult:
    """Run the search and return the normalized result. Persists to disk."""
    params = params or SearchParams.defaults(settings)
    methods = resolve_methods(settings, requested_methods)
    disabled = [m for m in (requested_methods or list(ALL_METHODS))
                if m in ALL_METHODS and m not in methods]

    settings.job_dir.mkdir(parents=True, exist_ok=True)
    cleanup(settings)

    job_id = job_id or new_job_id()
    d = job_dir(settings, job_id, create=True)
    query_fasta = d / "query.fasta"
    write_fasta(records, query_fasta)

    manifest = read_manifest(settings) or {}
    build_id = manifest.get("reference_build_id", "unbuilt")

    started = time.monotonic()
    sem = _slots.acquire(settings.max_concurrent_jobs)
    try:
        outcomes = {}
        for method in methods:
            log.info("job %s: running %s over %d quer%s", job_id, method,
                     len(records), "y" if len(records) == 1 else "ies")
            outcomes[method] = RUNNERS[method](settings, d, query_fasta, params)
    finally:
        sem.release()
    elapsed = time.monotonic() - started

    result = _assemble(settings, job_id, build_id, records, methods, disabled,
                       outcomes, manifest, elapsed, params)
    save(settings, result)
    if not settings.keep_raw_outputs:
        discard_raw_outputs(settings, job_id)
    return result


def _assemble(settings: Settings, job_id: str, build_id: str,
              records: list[SequenceRecord], methods: list[Method],
              disabled: list[str], outcomes: dict[str, SearchOutcome],
              manifest: dict, elapsed: float, params: SearchParams) -> JobResult:
    ref_ids, family_ids = [], []
    for method, outcome in outcomes.items():
        for hits in outcome.hits_by_query.values():
            target = family_ids if method == "hmmscan" else ref_ids
            target.extend(h.hit_id for h in hits)

    refs: dict[str, ReferenceMeta] = {}
    profiles: dict[str, ProfileMeta] = {}
    if settings.metadata_db.exists():
        try:
            with MetadataStore(settings.metadata_db) as store:
                refs = store.references(ref_ids)
                profiles = store.profiles(family_ids)
        except (OSError, ValueError, sqlite3.Error) as exc:
            # A hit without metadata still carries its score and identifier,
            # which is more useful than failing the whole search.
            log.warning("metadata lookup failed: %s", exc)

    queries = []
    for record in records:
        method_results = []
        for method in methods:
            outcome = outcomes[method]
            if outcome.failed:
                method_results.append(MethodResult(
                    method=method, method_version=outcome.version,
                    status=MethodStatus.FAILED, error_code=outcome.error_code,
                    error_message=outcome.error_message,
                    runtime_seconds=_round(outcome.runtime),
                ))
                continue
            raw = outcome.hits_by_query.get(record.query_id, [])
            method_results.append(MethodResult(
                method=method, method_version=outcome.version,
                status=MethodStatus.OK if raw else MethodStatus.NO_HITS,
                runtime_seconds=_round(outcome.runtime),
                hits=[_to_hit(i, h, method, refs, profiles)
                      for i, h in enumerate(raw, start=1)],
            ))
        for method in disabled:
            method_results.append(MethodResult(
                method=method, status=MethodStatus.DISABLED,
                error_message="This method is disabled in the server configuration.",
            ))
        queries.append(QueryResult(
            query_id=record.query_id,
            query_description=record.description,
            query_length=len(record.sequence),
            methods=method_results,
        ))

    notes = []
    if "hmmscan" in methods and settings.profile_db.exists():
        notes.append(
            "Profile HMM results cover only the reference clusters that passed "
            "quality control during the build, not the whole reference set."
        )
    if params.min_query_coverage:
        notes.append(
            "Hits covering less than "
            f"{params.min_query_coverage * 100:.0f}% of the submitted sequence "
            "were removed before ranking."
        )

    return JobResult(
        job_id=job_id,
        reference_build_id=build_id,
        reference_sequences=(manifest.get("export") or {}).get("exported"),
        profile_count=(manifest.get("hmmer") or {}).get("profiles_built"),
        requested_methods=methods,
        search_parameters=params.as_dict(),
        queries=queries,
        total_runtime_seconds=round(elapsed, 3),
        notes=notes,
    )


def _to_hit(rank: int, raw: RawHit, method: str,
            refs: dict[str, ReferenceMeta], profiles: dict[str, ProfileMeta]) -> Hit:
    hit = Hit(
        rank=rank, hit_id=raw.hit_id, evalue=raw.evalue, bitscore=raw.bitscore,
        percent_identity=raw.percent_identity, alignment_length=raw.alignment_length,
        query_coverage=_round(raw.query_coverage, 4),
        subject_coverage=_round(raw.subject_coverage, 4),
        domain_start=raw.domain_start, domain_end=raw.domain_end,
        domain_count=raw.domain_count, domain_evalue=raw.domain_evalue,
        domain_bitscore=raw.domain_bitscore,
    )
    if method == "hmmscan":
        meta = profiles.get(raw.hit_id)
        if meta:
            hit.hit_description = meta.description
            hit.hit_ec = meta.consensus_ec
            hit.hit_source = "profile HMM"
            hit.family_id = meta.family_id
            hit.family_size = meta.members
            hit.family_ec_purity = meta.ec_purity
        else:
            hit.family_id = raw.hit_id
    else:
        meta = refs.get(raw.hit_id)
        if meta:
            hit.hit_description = meta.description
            hit.hit_ec = meta.ec
            hit.hit_source = meta.source
    return hit


def _round(value: float | None, digits: int = 3) -> float | None:
    return None if value is None else round(value, digits)


def reference_status(settings: Settings) -> dict:
    """What is built and usable. Shared by /health and `refbuild status`."""
    manifest = read_manifest(settings) or {}
    blast_ok, blast_msg = blast_build.available(settings)
    hmmer_ok, hmmer_msg = hmmer_build.available(settings)
    profile_ok, profile_msg = hmmer_build.profiles_available(settings)

    references = None
    profiles = None
    if settings.metadata_db.exists():
        try:
            with MetadataStore(settings.metadata_db) as store:
                references, profiles = store.reference_count(), store.profile_count()
        except (OSError, ValueError, sqlite3.Error):
            # Reported as null counts by /health rather than a 500.
            pass

    return {
        "reference_build_id": manifest.get("reference_build_id"),
        "built_at": manifest.get("built_at"),
        "reference_sequences": references,
        "profiles": profiles,
        "artifacts": {
            "references_fasta": settings.references_fasta.exists(),
            "metadata_db": settings.metadata_db.exists(),
            "blast_db": blast_ok,
            "profile_db": profile_ok,
        },
        "methods": {
            "blastp": {"enabled": settings.enable_blast, "ok": blast_ok, "detail": blast_msg},
            "phmmer": {"enabled": settings.enable_phmmer, "ok": hmmer_ok, "detail": hmmer_msg},
            "hmmscan": {"enabled": settings.enable_profile_hmm,
                        "ok": profile_ok, "detail": profile_msg},
        },
        "tool_versions": manifest.get("tool_versions", {}),
    }
