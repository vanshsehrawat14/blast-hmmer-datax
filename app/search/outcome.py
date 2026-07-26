"""Shared return type for the three search runners."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.schemas import ErrorCode
from app.search.parsers import RawHit


@dataclass
class SearchOutcome:
    """What one method produced for a whole submission.

    A method runs once per job, not once per sequence, so results arrive
    grouped by internal query id. `error_message` is written for the user:
    it never contains a path, a command line or a tool's stderr.
    """

    method: str
    version: str | None = None
    runtime: float | None = None
    hits_by_query: dict[str, list[RawHit]] = field(default_factory=dict)
    error_code: ErrorCode | None = None
    error_message: str | None = None

    @property
    def failed(self) -> bool:
        return self.error_code is not None

    @classmethod
    def failure(cls, method: str, code: ErrorCode, message: str,
                version: str | None = None, runtime: float | None = None) -> "SearchOutcome":
        return cls(method=method, version=version, runtime=runtime,
                   error_code=code, error_message=message)
