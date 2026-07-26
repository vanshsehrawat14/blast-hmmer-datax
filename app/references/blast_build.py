"""Build the protein BLAST database from the exported reference FASTA."""

from __future__ import annotations

import logging

from app.config import Settings
from app.search.subprocess_utils import run_tool, tool_version

log = logging.getLogger(__name__)

# The files makeblastdb writes for a protein database. Volume suffixes
# (references.00.phr …) appear only past ~4 GB and are checked separately.
BLAST_DB_SUFFIXES = (".phr", ".pin", ".psq")


def build(settings: Settings, build_id: str) -> dict:
    fasta = settings.references_fasta
    if not fasta.exists():
        raise FileNotFoundError(f"{fasta} not found; run the export step first")

    db_path = settings.blast_db
    db_path.parent.mkdir(parents=True, exist_ok=True)

    args = [
        "-in", str(fasta),
        "-dbtype", "prot",
        "-title", f"EnzymeX copied enzymesdata references (build {build_id})",
        "-out", str(db_path),
        # -parse_seqids is deliberately omitted. It rewrites UniProt-style
        # deflines into "sp|ACC|" form, which then has to be stripped back out
        # of every result row. Our identifiers are already single tokens and
        # nothing here needs blastdbcmd retrieval by id.
    ]
    run = run_tool(
        settings.makeblastdb_bin, args,
        timeout=settings.build_timeout_seconds,
        log_dir=db_path.parent, log_name="makeblastdb",
    )
    if not run.ok:
        raise RuntimeError(
            f"makeblastdb failed (exit {run.returncode}, timeout={run.timed_out}): "
            f"{run.stderr_snippet[:500]}"
        )

    produced = sorted(p.name for p in db_path.parent.glob(db_path.name + "*"))
    missing = [s for s in BLAST_DB_SUFFIXES
               if not any(n.endswith(s) for n in produced)]
    if missing:
        raise RuntimeError(f"makeblastdb produced no {missing} files; got {produced}")

    return {
        "db_path": str(db_path.relative_to(settings.reference_dir)),
        "files": produced,
        "bytes": sum(p.stat().st_size for p in db_path.parent.glob(db_path.name + "*")),
        "makeblastdb_version": tool_version(settings.makeblastdb_bin, ["-version"]),
        "build_seconds": round(run.duration, 2),
    }


def available(settings: Settings) -> tuple[bool, str]:
    """Health probe: both binaries resolvable and the database files present."""
    for binary in (settings.blastp_bin, settings.makeblastdb_bin):
        if settings.which(binary) is None:
            return False, f"executable not found: {binary}"
    db = settings.blast_db
    present = {p.suffix for p in db.parent.glob(db.name + "*")} if db.parent.exists() else set()
    missing = [s for s in BLAST_DB_SUFFIXES if s not in present]
    if missing:
        return False, f"BLAST database incomplete, missing {', '.join(missing)}"
    return True, f"blastp ready ({tool_version(settings.blastp_bin, ['-version']) or 'version unknown'})"
