"""Job identity, isolated working directories, persistence and cleanup.

Every submission gets its own directory named after a server-generated random
identifier. The user never supplies any part of a path, and a job id coming
back in a URL is checked against a strict pattern before it is joined to
anything, so `../` and absolute paths cannot escape the job root.

Results are persisted as a single JSON document so a results page can be
reloaded and shared. There is no job database: the copied MySQL is read-only
and adding a second, writable store for what is fundamentally a temp file
would be more machinery than a test server needs. Everything a queue-backed
version would persist is already in this file.
"""

from __future__ import annotations

import logging
import re
import secrets
import shutil
import time
from pathlib import Path

from app.config import Settings
from app.schemas import JobResult

log = logging.getLogger(__name__)

JOB_ID_RE = re.compile(r"^[0-9a-f]{16}$")
RESULTS_FILE = "results.json"


class InvalidJobId(ValueError):
    pass


def new_job_id() -> str:
    return secrets.token_hex(8)


def job_dir(settings: Settings, job_id: str, *, create: bool = False) -> Path:
    if not JOB_ID_RE.match(job_id):
        raise InvalidJobId(job_id)
    root = settings.job_dir.resolve()
    path = (root / job_id).resolve()
    # Defence in depth: even with the pattern check, never return a path that
    # is not inside the job root.
    if path.parent != root:
        raise InvalidJobId(job_id)
    if create:
        path.mkdir(parents=True, exist_ok=False)
        path.chmod(0o700)
    return path


def save(settings: Settings, result: JobResult) -> Path:
    path = job_dir(settings, result.job_id) / RESULTS_FILE
    path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    return path


def load(settings: Settings, job_id: str) -> JobResult | None:
    try:
        path = job_dir(settings, job_id) / RESULTS_FILE
    except InvalidJobId:
        return None
    if not path.exists():
        return None
    try:
        return JobResult.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("job %s has unreadable results: %s", job_id, exc)
        return None


def discard_raw_outputs(settings: Settings, job_id: str) -> None:
    """Remove tool output, keeping only the normalized results.

    Used when `keep_raw_outputs` is off. The raw files are the only debugging
    evidence there is, so the default keeps them and cleanup is by age.
    """
    d = job_dir(settings, job_id)
    for path in d.iterdir():
        if path.name != RESULTS_FILE:
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink(missing_ok=True)


def cleanup(settings: Settings) -> int:
    """Delete job directories older than the retention window.

    Called on job creation rather than from a timer: it is a cheap directory
    scan, and tying it to submissions means an idle server does no work.
    """
    root = settings.job_dir
    if not root.exists():
        return 0
    cutoff = time.time() - settings.job_retention_hours * 3600
    removed = 0
    for path in root.iterdir():
        if not path.is_dir() or not JOB_ID_RE.match(path.name):
            continue
        try:
            if path.stat().st_mtime < cutoff:
                shutil.rmtree(path, ignore_errors=True)
                removed += 1
        except OSError:
            continue
    if removed:
        log.info("cleanup removed %d expired job directories", removed)
    return removed
