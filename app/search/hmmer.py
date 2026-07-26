"""Run the two HMMER searches.

`phmmer` is the baseline: it compares each submitted sequence against every
exported reference, so its coverage of the copied database is complete. HMMER
builds a single-sequence profile from the query internally, which means it
still uses the full probabilistic scoring model — but with no position-specific
information beyond what a substitution matrix provides. In practice its
rankings track blastp closely; the value of running both is that they are
independent implementations of different statistics over the same data.

`hmmscan` is the profile layer, and it only covers the subset of references
that clustered into families good enough to model (see references/cluster.py).
The results page says so explicitly rather than letting an absent profile hit
read as evidence of absence.
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.config import Settings
from app.schemas import ErrorCode
from app.search.outcome import SearchOutcome
from app.search.parsers import ParseError, parse_hmmscan, parse_phmmer, rank_hits
from app.search.subprocess_utils import ToolNotFound, run_tool, tool_version

log = logging.getLogger(__name__)


def run_phmmer(settings: Settings, job_dir: Path, query_fasta: Path) -> SearchOutcome:
    target = settings.references_fasta
    if not target.exists():
        return SearchOutcome.failure(
            "phmmer", ErrorCode.REFERENCE_MISSING,
            "The reference sequence database has not been built on this server.",
        )

    tbl = job_dir / "phmmer_tblout.txt"
    dom = job_dir / "phmmer_domtblout.txt"
    args = [
        "--tblout", str(tbl),
        "--domtblout", str(dom),
        "-o", str(job_dir / "phmmer_full.txt"),
        "-E", str(settings.phmmer_evalue),
        "--cpu", str(settings.search_threads),
        str(query_fasta), str(target),
    ]
    return _run(settings, job_dir, "phmmer", settings.phmmer_bin, args, tbl, dom,
                parse=parse_phmmer, label="HMMER (phmmer)")


def run_hmmscan(settings: Settings, job_dir: Path, query_fasta: Path) -> SearchOutcome:
    db = settings.profile_db
    pressed = db.exists() and db.with_suffix(db.suffix + ".h3i").exists()
    if not pressed:
        return SearchOutcome.failure(
            "hmmscan", ErrorCode.REFERENCE_MISSING,
            "No profile HMM database is available on this server. "
            "BLAST and phmmer cover the full reference set regardless.",
        )

    tbl = job_dir / "hmmscan_tblout.txt"
    dom = job_dir / "hmmscan_domtblout.txt"
    args = [
        "--tblout", str(tbl),
        "--domtblout", str(dom),
        "-o", str(job_dir / "hmmscan_full.txt"),
        "-E", str(settings.hmmscan_evalue),
        "--cpu", str(settings.search_threads),
        str(db), str(query_fasta),
    ]
    return _run(settings, job_dir, "hmmscan", settings.hmmscan_bin, args, tbl, dom,
                parse=parse_hmmscan, label="HMMER (hmmscan)")


def _run(settings: Settings, job_dir: Path, method: str, binary: str,
         args: list[str], tbl: Path, dom: Path, *, parse, label: str) -> SearchOutcome:
    try:
        run = run_tool(
            binary, args,
            timeout=settings.hmmer_timeout_seconds,
            log_dir=job_dir, log_name=method,
        )
    except ToolNotFound:
        return SearchOutcome.failure(
            method, ErrorCode.EXECUTABLE_MISSING,
            "HMMER is not installed on this server.",
        )

    version = tool_version(binary, ["-h"])
    if run.timed_out:
        return SearchOutcome.failure(
            method, ErrorCode.TIMEOUT,
            f"{label} did not finish within {settings.hmmer_timeout_seconds} seconds.",
            version=version, runtime=run.duration,
        )
    if run.returncode != 0:
        return SearchOutcome.failure(
            method, ErrorCode.NONZERO_EXIT,
            f"{label} exited with an error. The raw output has been kept for diagnosis.",
            version=version, runtime=run.duration,
        )

    try:
        text_tbl = tbl.read_text(encoding="utf-8", errors="replace") if tbl.exists() else ""
        text_dom = dom.read_text(encoding="utf-8", errors="replace") if dom.exists() else ""
        hits = parse(text_tbl, text_dom)
    except (ParseError, OSError) as exc:
        log.warning("%s output unparseable: %s", method, exc)
        return SearchOutcome.failure(
            method, ErrorCode.OUTPUT_UNPARSEABLE,
            f"{label} produced output this server could not read.",
            version=version, runtime=run.duration,
        )

    return SearchOutcome(
        method=method, version=version, runtime=run.duration,
        hits_by_query=rank_hits(hits, settings.max_hits_per_query),
    )
