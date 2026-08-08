"""The normalized, method-agnostic result model.

BLAST, phmmer and hmmscan report overlapping but genuinely different
quantities. Rather than forcing them into identical columns, every method
fills the subset of this model it can support honestly and leaves the rest
None. Nothing here is ever back-filled with a guess.

Two conventions hold across all methods and are the reason the rest of the
codebase can stay simple:

  * coordinates in `domain_start`/`domain_end` are always on the *submitted*
    sequence, never on the reference or the profile;
  * `rank` is assigned by us after sorting, not taken from tool output.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field

Method = Literal["blastp", "phmmer", "hmmscan"]

METHOD_LABELS: dict[str, str] = {
    "blastp": "BLAST (blastp)",
    "phmmer": "HMMER (phmmer)",
    "hmmscan": "HMMER (profile HMMs, hmmscan)",
}


class MethodStatus(str, Enum):
    OK = "ok"
    NO_HITS = "no_hits"
    DISABLED = "disabled"
    FAILED = "failed"


class ErrorCode(str, Enum):
    EXECUTABLE_MISSING = "executable_missing"
    REFERENCE_MISSING = "reference_missing"
    TIMEOUT = "timeout"
    NONZERO_EXIT = "nonzero_exit"
    OUTPUT_UNPARSEABLE = "output_unparseable"
    INTERNAL = "internal"


class Hit(BaseModel):
    """One reference (or profile) matched to one submitted sequence."""

    rank: int
    hit_id: str
    hit_description: str | None = None
    hit_ec: str | None = None
    hit_source: str | None = None

    evalue: float
    bitscore: float

    # blastp only: HMMER's tabular output does not report percent identity,
    # and computing it from the alignment would mean a different quantity
    # under the same column name.
    percent_identity: float | None = None
    alignment_length: int | None = None

    # Fractions in [0, 1]. Query coverage is the share of the submitted
    # sequence aligned; subject coverage the share of the reference (for
    # hmmscan, of the profile's match states).
    query_coverage: float | None = None
    subject_coverage: float | None = None

    domain_start: int | None = None
    domain_end: int | None = None
    domain_count: int | None = None
    # Best single domain: HMMER's independent E-value and domain bit score.
    domain_evalue: float | None = None
    domain_bitscore: float | None = None

    # Profile hits only.
    family_id: str | None = None
    family_size: int | None = None
    family_ec_purity: float | None = None


class MethodResult(BaseModel):
    """Outcome of one method for one submitted sequence."""

    method: Method
    method_version: str | None = None
    status: MethodStatus = MethodStatus.OK
    error_code: ErrorCode | None = None
    # User-facing text. Never contains a filesystem path or a command line.
    error_message: str | None = None
    runtime_seconds: float | None = None
    hits: list[Hit] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status in (MethodStatus.OK, MethodStatus.NO_HITS)


class QueryResult(BaseModel):
    query_id: str            # internal, safe: Q1, Q2, ...
    query_description: str   # the user's original FASTA header, unmodified
    query_length: int
    methods: list[MethodResult] = Field(default_factory=list)


class JobResult(BaseModel):
    job_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    reference_build_id: str
    reference_sequences: int | None = None
    profile_count: int | None = None
    requested_methods: list[Method] = Field(default_factory=list)
    # What the search actually ran with, so a hit list can be reproduced from
    # the record alone rather than from whatever the server defaults are today.
    search_parameters: dict = Field(default_factory=dict)
    queries: list[QueryResult] = Field(default_factory=list)
    total_runtime_seconds: float | None = None
    notes: list[str] = Field(default_factory=list)

    def flat_rows(self) -> list[dict]:
        """Flatten to the one-row-per-hit export schema.

        A method that produced no hits, was disabled or failed still emits a
        single row carrying its status, so a downstream consumer can tell
        "searched, found nothing" apart from "never ran".
        """
        rows: list[dict] = []
        for q in self.queries:
            for m in q.methods:
                base = {
                    "job_id": self.job_id,
                    "query_id": q.query_id,
                    "query_description": q.query_description,
                    "query_length": q.query_length,
                    "method": m.method,
                    "method_version": m.method_version,
                    "reference_build_id": self.reference_build_id,
                    "status": m.status.value,
                    "error_code": m.error_code.value if m.error_code else None,
                    "error_message": m.error_message,
                }
                if not m.hits:
                    rows.append({**base, **{k: None for k in _HIT_FIELDS}})
                    continue
                for h in m.hits:
                    rows.append({**base, **{k: getattr(h, k) for k in _HIT_FIELDS}})
        return rows


_HIT_FIELDS = (
    "rank", "hit_id", "hit_description", "hit_ec", "hit_source",
    "evalue", "bitscore", "percent_identity", "alignment_length",
    "query_coverage", "subject_coverage", "domain_start", "domain_end",
    "domain_count", "domain_evalue", "domain_bitscore",
    "family_id", "family_size", "family_ec_purity",
)

FLAT_SCHEMA = (
    "job_id", "query_id", "query_description", "query_length",
    "method", "method_version", "reference_build_id",
    "status", "error_code", "error_message",
) + _HIT_FIELDS
