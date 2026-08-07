"""Build-provenance behaviour of the refbuild subcommands.

Both cases here were found by running the build against the real EnzymeX copy
rather than the fixture, where a staged `export` + `blast` produced artifacts
that were correct but labelled with the previous build's id.
"""

from __future__ import annotations

import argparse
import json

from app.references import blast_build, cli
from app.references.manifest import compute_build_id, sha256_file


def _write_fasta(settings, text: str) -> None:
    settings.references_fasta.parent.mkdir(parents=True, exist_ok=True)
    settings.references_fasta.write_text(text, encoding="utf-8")


def test_blast_labels_the_database_with_the_current_references(settings, monkeypatch):
    """`export` does not rewrite the manifest, so `blast` must not trust it."""
    _write_fasta(settings, ">EXR1\nMSIPETQKGVIFYESHGKLEYKDIPVPKPKANELLINVKYSGVCHTDLHAWHG\n")
    settings.manifest_path.parent.mkdir(parents=True, exist_ok=True)
    settings.manifest_path.write_text(
        json.dumps({"manifest_version": 3, "reference_build_id": "staleaaaaaaa"}),
        encoding="utf-8",
    )

    seen: dict[str, str] = {}

    def fake_build(_settings, build_id):
        seen["build_id"] = build_id
        return {"db_path": "blastdb/references"}

    monkeypatch.setattr(blast_build, "build", fake_build)
    assert cli.cmd_blast(settings, argparse.Namespace()) == 0

    expected = compute_build_id(settings, sha256_file(settings.references_fasta))
    assert seen["build_id"] == expected
    assert seen["build_id"] != "staleaaaaaaa"


def test_blast_falls_back_to_the_manifest_when_nothing_is_exported(settings, monkeypatch):
    settings.manifest_path.parent.mkdir(parents=True, exist_ok=True)
    settings.manifest_path.write_text(
        json.dumps({"manifest_version": 3, "reference_build_id": "recordedaaaa"}),
        encoding="utf-8",
    )

    seen: dict[str, str] = {}
    monkeypatch.setattr(blast_build, "build",
                        lambda _s, build_id: seen.setdefault("build_id", build_id) and {} or {})
    cli.cmd_blast(settings, argparse.Namespace())
    assert seen["build_id"] == "recordedaaaa"


def test_skip_profiles_removes_a_profile_database_from_an_earlier_build(settings, monkeypatch):
    """Recording profiles_built = 0 is not enough; the files still answer."""
    settings.profile_db.parent.mkdir(parents=True, exist_ok=True)
    settings.profile_db.write_text("HMMER3/f stale\n", encoding="utf-8")
    pressed = settings.profile_db.with_suffix(settings.profile_db.suffix + ".h3i")
    pressed.write_bytes(b"stale index")

    _write_fasta(settings, ">EXR1\nMSIPETQKGVIFYESHGKLEY\n")

    monkeypatch.setattr(cli, "export_references",
                        lambda _s: {"exported": 1, "fasta_sha256": "abc"})
    monkeypatch.setattr(blast_build, "build", lambda _s, _b: {"db_path": "blastdb/references"})

    args = argparse.Namespace(skip_profiles=True, keep_work=False)
    assert cli.cmd_all(settings, args) == 0

    assert not settings.profile_db.exists()
    assert not pressed.exists()
    assert not settings.profile_db.parent.exists()
