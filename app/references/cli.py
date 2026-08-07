"""Command line for the offline reference build.

    enzymex-refbuild inspect    # read-only characterisation of the copy
    enzymex-refbuild export     # enzymesdata -> validated FASTA + metadata
    enzymex-refbuild blast      # makeblastdb
    enzymex-refbuild hmmer      # cluster -> MAFFT -> hmmbuild -> hmmpress
    enzymex-refbuild all        # the three above, then the manifest
    enzymex-refbuild status     # what is currently built

This is the only entry point that touches MySQL. Nothing here runs during a
user request, and the web application refuses to rebuild anything at request
time by design: a rebuild reads the whole table, forks MAFFT thousands of
times, and would let one submission stall every other.
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
import time
from pathlib import Path

from app.config import Settings, get_settings
from app.references import blast_build, cluster, db as dbmod, hmmer_build
from app.references.export import export_references
from app.references.manifest import (
    compute_build_id, read_manifest, sha256_file, write_manifest,
)
from app.references.metadata import connect_write

log = logging.getLogger("refbuild")


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def cmd_inspect(settings: Settings, args) -> int:
    conn = dbmod.connect(settings)
    try:
        schema = dbmod.inspect_schema(conn, settings.db_table)
        stats = dbmod.profile_table(conn, schema)
    finally:
        conn.close()
    print(json.dumps(stats, indent=2, default=str))
    if args.out:
        Path(args.out).write_text(json.dumps(stats, indent=2, default=str) + "\n",
                                  encoding="utf-8")
    return 0


def cmd_export(settings: Settings, args) -> int:
    started = time.monotonic()
    stats = export_references(settings)
    stats["export_seconds"] = round(time.monotonic() - started, 2)
    print(json.dumps(stats, indent=2, default=str))
    return 0


def cmd_blast(settings: Settings, args) -> int:
    # The id must identify the reference set on disk, not whatever the last
    # manifest happens to record. `export` does not rewrite the manifest, so
    # reading the id from there stamped a freshly exported database with the
    # *previous* build's id — and makeblastdb bakes it into `-title`, where it
    # then outlives the manifest that produced it.
    manifest = read_manifest(settings) or {}
    if settings.references_fasta.exists():
        build_id = compute_build_id(settings, sha256_file(settings.references_fasta))
    else:
        build_id = manifest.get("reference_build_id", "unversioned")

    stored = manifest.get("reference_build_id")
    if stored and stored != build_id:
        log.warning(
            "references have changed since the last manifest (%s -> %s); the "
            "BLAST database will carry the new id, but run `all` to bring the "
            "manifest and the profile layer back in step",
            stored, build_id,
        )

    stats = blast_build.build(settings, build_id)
    stats["reference_build_id"] = build_id
    print(json.dumps(stats, indent=2, default=str))
    return 0


def build_hmmer_layer(settings: Settings, keep_work: bool = False) -> dict:
    """Cluster the export, build and QC profiles, press the database.

    Returns the full statistics dict so the manifest records the same numbers
    the command prints, rather than a summary reconstructed afterwards.
    """
    started = time.monotonic()
    work = settings.reference_dir / "work"
    work.mkdir(parents=True, exist_ok=True)

    lengths = cluster.read_fasta_lengths(settings.references_fasta)
    log.info("clustering %d references at %.0f%% identity / %.0f%% coverage",
             len(lengths), settings.cluster_min_seq_id * 100,
             settings.cluster_coverage * 100)
    clusters = cluster.run_mmseqs_cluster(settings, work)
    decisions = cluster.qc_clusters(clusters, lengths, settings)
    cstats = cluster.cluster_stats(decisions)
    log.info("clusters: %d total, %d accepted", cstats["clusters_total"],
             cstats["clusters_accepted"])

    store = connect_write(settings.metadata_db)
    ec_by_ref = {r[0]: r[1] for r in store.execute("SELECT ref_id, ec FROM reference")}

    profiles, build_skipped = hmmer_build.build_profiles(settings, decisions, ec_by_ref, work)
    press = hmmer_build.press_profiles(settings, profiles)
    hmmer_build.write_profile_metadata(store, profiles)
    store.close()

    # Every rejected cluster, with the gate that rejected it.
    skip_path = settings.reference_dir / "skipped_clusters.tsv"
    with skip_path.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write("stage\tidentifier\tmembers\treason\tdetail\n")
        for d in decisions:
            if not d.accepted:
                fh.write(f"cluster\t{d.cluster.representative}\t{d.cluster.size}\t"
                         f"{d.reason}\t\n")
        for s in build_skipped:
            fh.write(f"build\t{s['family_id']}\t{s['members']}\t{s['reason']}\t"
                     f"{s['detail']}\n")

    stats = {
        **cstats,
        **hmmer_build.profile_stats(profiles, build_skipped),
        **press,
        "reference_sequences": len(lengths),
        "profile_member_coverage": round(
            sum(p.members for p in profiles) / len(lengths), 4) if lengths else 0.0,
        "hmmer_seconds": round(time.monotonic() - started, 2),
    }
    if not keep_work:
        shutil.rmtree(work / "families", ignore_errors=True)
    return stats


def cmd_hmmer(settings: Settings, args) -> int:
    if not settings.references_fasta.exists():
        log.error("reference FASTA missing; run `export` first")
        return 1
    print(json.dumps(build_hmmer_layer(settings, args.keep_work), indent=2, default=str))
    return 0


def cmd_all(settings: Settings, args) -> int:
    sections: dict = {}
    started = time.monotonic()

    log.info("step 1/4: export references from %s", settings.dsn_summary())
    t0 = time.monotonic()
    sections["export"] = export_references(settings)
    sections["export"]["export_seconds"] = round(time.monotonic() - t0, 2)

    # A provisional manifest so makeblastdb's -title can carry the build id.
    build_id = write_manifest(settings, sections)["reference_build_id"]

    log.info("step 2/4: build BLAST database (build %s)", build_id)
    sections["blast"] = blast_build.build(settings, build_id)

    if args.skip_profiles:
        log.info("step 3/4: skipped (--skip-profiles); phmmer still covers every reference")
        sections["hmmer"] = {"profiles_built": 0, "skipped": "--skip-profiles"}
        # Recording profiles_built = 0 is not enough: any profile database
        # left from an earlier build is still on disk and still passes the
        # readiness check, so hmmscan would answer with families clustered
        # from a different reference set and family metadata that no longer
        # resolves. Skipping the layer has to mean the layer is absent.
        profile_dir = settings.profile_db.parent
        if profile_dir.exists():
            log.info("removing the previous profile database at %s", profile_dir)
            shutil.rmtree(profile_dir)
            sections["hmmer"]["removed_stale_profiles"] = True
    else:
        log.info("step 3/4: cluster and build profile HMMs")
        sections["hmmer"] = build_hmmer_layer(settings, args.keep_work)

    log.info("step 4/4: write manifest")
    sections["total_build_seconds"] = round(time.monotonic() - started, 2)
    manifest = write_manifest(settings, sections)
    print(json.dumps(manifest, indent=2, default=str))
    log.info("build %s complete in %.1fs", manifest["reference_build_id"],
             sections["total_build_seconds"])
    return 0


def cmd_status(settings: Settings, args) -> int:
    from app.search.service import reference_status
    print(json.dumps(reference_status(settings), indent=2, default=str))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="enzymex-refbuild", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--log-level", default=None)
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("inspect", help="characterise the copied enzymesdata table")
    s.add_argument("--out", help="also write the JSON report here")
    s.set_defaults(func=cmd_inspect)

    s = sub.add_parser("export", help="export validated references")
    s.set_defaults(func=cmd_export)

    s = sub.add_parser("blast", help="build the BLAST protein database")
    s.set_defaults(func=cmd_blast)

    s = sub.add_parser("hmmer", help="cluster references and build profile HMMs")
    s.add_argument("--keep-work", action="store_true",
                   help="keep per-family alignments for inspection")
    s.set_defaults(func=cmd_hmmer)

    s = sub.add_parser("all", help="export + blast + hmmer + manifest")
    s.add_argument("--skip-profiles", action="store_true",
                   help="build BLAST and phmmer only; no profile HMM layer")
    s.add_argument("--keep-work", action="store_true")
    s.set_defaults(func=cmd_all)

    s = sub.add_parser("status", help="report what is currently built")
    s.set_defaults(func=cmd_status)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = get_settings()
    _setup_logging(args.log_level or settings.log_level)
    try:
        return args.func(settings, args)
    except dbmod.DatabaseNotConfirmed as exc:
        log.error("%s", exc)
        return 2
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        log.error("%s: %s", type(exc).__name__, exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
