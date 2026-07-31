"""The build manifest: what was built, from what, with which tools.

Every result page and every exported row carries a `reference_build_id`. It is
derived from the checksum of the exported FASTA plus the settings that change
what gets searched, so:

  * rebuilding from an unchanged copy of the database reproduces the same id;
  * changing a filter threshold, or the copied data itself, changes it;
  * a result can never be silently attributed to the wrong reference set.

The manifest deliberately records the database *identity* (host, name, table,
row counts) but never the credentials used to read it.
"""

from __future__ import annotations

import hashlib
import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path

from app.config import Settings
from app.references.export import sha256_file
from app.search.subprocess_utils import tool_version

MANIFEST_VERSION = 3

# Settings that change which sequences end up searchable. A change to any of
# them must produce a new build id; changing a request-time timeout must not.
BUILD_INPUT_KEYS = (
    "db_name", "db_table", "reference_sources", "min_sequence_length",
    "max_reference_length", "max_ambiguous_fraction", "export_limit",
    "cluster_min_seq_id", "cluster_coverage", "profile_min_members",
    "profile_max_members", "profile_min_match_states",
)


def compute_build_id(settings: Settings, fasta_sha256: str) -> str:
    payload = _build_inputs(settings)
    payload["fasta_sha256"] = fasta_sha256
    blob = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:12]


def tool_versions(settings: Settings) -> dict[str, str | None]:
    return {
        "makeblastdb": tool_version(settings.makeblastdb_bin, ["-version"]),
        "blastp": tool_version(settings.blastp_bin, ["-version"]),
        "phmmer": tool_version(settings.phmmer_bin, ["-h"]),
        "hmmbuild": tool_version(settings.hmmbuild_bin, ["-h"]),
        "hmmscan": tool_version(settings.hmmscan_bin, ["-h"]),
        "mafft": tool_version(settings.mafft_bin, ["--version"]),
        "mmseqs": tool_version(settings.mmseqs_bin, ["version"]),
        "python": sys.version.split()[0],
    }


def artifact_checksums(settings: Settings) -> dict[str, str]:
    """Checksums of the artifacts a search actually reads.

    BLAST index files are excluded: makeblastdb embeds a creation timestamp,
    so their checksums differ between two builds of identical data and would
    make the manifest look non-reproducible when it is not.
    """
    out: dict[str, str] = {}
    for path in (settings.references_fasta, settings.metadata_db, settings.profile_db):
        if path.exists():
            out[path.name] = sha256_file(path)
    return out


def write_manifest(settings: Settings, sections: dict) -> dict:
    fasta_sha = sections.get("export", {}).get("fasta_sha256") or (
        sha256_file(settings.references_fasta) if settings.references_fasta.exists() else ""
    )
    build_id = compute_build_id(settings, fasta_sha)

    manifest = {
        "manifest_version": MANIFEST_VERSION,
        "reference_build_id": build_id,
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "built_on": {
            "platform": platform.platform(),
            "python": sys.version.split()[0],
        },
        "source": {
            "host": settings.db_host,
            "port": settings.db_port,
            "database": settings.db_name,
            "table": settings.db_table,
            "read_only": True,
        },
        "configuration": _build_inputs(settings),
        "tool_versions": tool_versions(settings),
        "artifact_sha256": artifact_checksums(settings),
        **sections,
    }
    settings.manifest_path.parent.mkdir(parents=True, exist_ok=True)
    settings.manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=False, default=str) + "\n",
        encoding="utf-8",
    )
    return manifest


def read_manifest(settings: Settings) -> dict | None:
    path = settings.manifest_path
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _plain(value):
    return str(value) if isinstance(value, Path) else value


def _build_inputs(settings: Settings) -> dict:
    values = {k: _plain(getattr(settings, k)) for k in BUILD_INPUT_KEYS}
    values["reference_sources"] = list(settings.selected_reference_sources)
    return values
