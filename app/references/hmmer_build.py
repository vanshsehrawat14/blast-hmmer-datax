"""Build the HMMER artifacts: nothing for phmmer, profiles for hmmscan.

phmmer needs no build step at all — it searches the exported FASTA directly,
which is why it is this server's universal HMMER baseline and covers 100% of
the references. Everything below concerns the optional profile layer, which
covers only the subset of the export that survives clustering and QC. That
partial coverage is reported, never papered over.

Per accepted cluster the pipeline is:

    member FASTA -> MAFFT -> alignment QC -> hmmbuild -> profile QC

and the surviving profiles are concatenated and `hmmpress`ed into a single
searchable database.

Search direction is `hmmscan` (profiles are the database, the submitted
sequence is the query), not `hmmsearch`. HMMER's own documentation notes that
hmmsearch is the faster of the two for the same comparison, but its E-values
are computed against the size of the *target* set — which for us would be the
handful of sequences a user happened to paste. The same protein would then
get a different E-value depending on how many other sequences were submitted
alongside it. hmmscan scores against a fixed profile database, so a result is
reproducible across submissions. At this scale the speed difference is not
worth an unstable statistic.
"""

from __future__ import annotations

import json
import logging
import random
import re
import shutil
import statistics
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from app.config import Settings
from app.references.cluster import ClusterDecision, read_fasta
from app.search.subprocess_utils import run_tool, tool_version

log = logging.getLogger(__name__)

FAMILY_PREFIX = "EXF"
_LENG = re.compile(r"^LENG\s+(\d+)", re.MULTILINE)

# Fraction of sequences that must have a residue in a column for it to count
# as a "core" column. A profile whose core is much shorter than its members
# is describing gaps, not a domain.
CORE_COLUMN_OCCUPANCY = 0.5
# Pairwise identity over a full n x n matrix is quadratic in both sequence
# count and length; 40 sequences (780 pairs) is enough to characterise a
# cluster and keeps the build bounded.
IDENTITY_SAMPLE = 40


@dataclass
class BuiltProfile:
    family_id: str
    hmm_path: Path
    members: int
    match_states: int
    median_length: int
    mean_pairwise_identity: float
    consensus_ec: str | None
    ec_purity: float
    ec_distribution: dict[str, int]
    representative: str


def family_id(index: int) -> str:
    return f"{FAMILY_PREFIX}{index:05d}"


def alignment_core_columns(aligned: dict[str, str]) -> tuple[int, int]:
    """(core column count, total column count) for an aligned FASTA."""
    if not aligned:
        return 0, 0
    seqs = list(aligned.values())
    width = len(seqs[0])
    if any(len(s) != width for s in seqs):
        raise ValueError("alignment rows have unequal length")
    threshold = CORE_COLUMN_OCCUPANCY * len(seqs)
    core = 0
    for col in range(width):
        occupied = sum(1 for s in seqs if s[col] not in "-.")
        if occupied >= threshold:
            core += 1
    return core, width


def mean_pairwise_identity(aligned: dict[str, str], seed: int = 0) -> float:
    """Average identity over aligned positions where both sequences have a
    residue. Sampled deterministically for large clusters."""
    ids = sorted(aligned)
    if len(ids) > IDENTITY_SAMPLE:
        rng = random.Random(seed)
        ids = sorted(rng.sample(ids, IDENTITY_SAMPLE))
    if len(ids) < 2:
        return 1.0

    total, pairs = 0.0, 0
    for i in range(len(ids)):
        a = aligned[ids[i]]
        for j in range(i + 1, len(ids)):
            b = aligned[ids[j]]
            same = shared = 0
            for x, y in zip(a, b):
                if x in "-." or y in "-.":
                    continue
                shared += 1
                if x == y:
                    same += 1
            if shared:
                total += same / shared
                pairs += 1
    return round(total / pairs, 4) if pairs else 0.0


def _consensus_ec(members: list[str], ec_by_ref: dict[str, str | None]) -> tuple[str | None, float, dict[str, int]]:
    """Most common EC in the cluster, and how dominant it is.

    A multi-EC reference contributes to each of its EC numbers, so a
    bifunctional enzyme does not create a spurious singleton category.
    Purity is over annotated members only: unannotated references should not
    make a coherent family look impure.
    """
    counts: Counter = Counter()
    annotated = 0
    for m in members:
        ec = ec_by_ref.get(m)
        if not ec:
            continue
        annotated += 1
        for token in ec.split(";"):
            counts[token] += 1
    if not counts:
        return None, 0.0, {}
    top, n = counts.most_common(1)[0]
    return top, round(n / annotated, 4), dict(counts.most_common(10))


def build_profiles(settings: Settings, decisions: list[ClusterDecision],
                   ec_by_ref: dict[str, str | None], work_dir: Path) -> tuple[list[BuiltProfile], list[dict]]:
    """Align, build and QC one profile per accepted cluster."""
    sequences = read_fasta(settings.references_fasta)
    fam_dir = work_dir / "families"
    if fam_dir.exists():
        shutil.rmtree(fam_dir)
    fam_dir.mkdir(parents=True, exist_ok=True)

    built: list[BuiltProfile] = []
    skipped: list[dict] = []
    accepted = [d for d in decisions if d.accepted]
    log.info("building profiles for %d accepted clusters", len(accepted))

    for n, decision in enumerate(accepted, start=1):
        fam = family_id(n)
        members = decision.kept_members
        d = fam_dir / fam
        d.mkdir(parents=True, exist_ok=True)

        members_fa = d / "members.fasta"
        with members_fa.open("w", encoding="ascii", newline="\n") as fh:
            for ref in members:
                seq = sequences.get(ref)
                if not seq:
                    continue
                fh.write(f">{ref}\n")
                for i in range(0, len(seq), 60):
                    fh.write(seq[i:i + 60] + "\n")

        # --anysymbol keeps U/O/B/Z/J as-is instead of MAFFT silently mapping
        # them; --reorder makes output ordering deterministic.
        aln_run = run_tool(
            settings.mafft_bin,
            ["--auto", "--anysymbol", "--reorder", "--quiet",
             "--thread", str(settings.build_threads), str(members_fa)],
            timeout=settings.build_timeout_seconds, log_dir=d, log_name="mafft",
        )
        if not aln_run.ok:
            skipped.append(_skip(decision, fam, "mafft_failed", aln_run.stderr_snippet[:200]))
            continue

        aln_path = d / "aligned.afa"
        aln_run.stdout_path.replace(aln_path)
        aligned = read_fasta(aln_path)
        if len(aligned) < settings.profile_min_members:
            skipped.append(_skip(decision, fam, "alignment_lost_members", f"{len(aligned)}"))
            continue

        core, width = alignment_core_columns(aligned)
        if core < 0.5 * decision.median_length:
            # A gappy alignment means the "cluster" is not alignable end to
            # end, whatever the clustering thresholds said.
            skipped.append(_skip(decision, fam, "alignment_too_gappy",
                                 f"core={core} width={width} median_len={decision.median_length}"))
            continue

        hmm_path = d / f"{fam}.hmm"
        build_run = run_tool(
            settings.hmmbuild_bin,
            ["-n", fam, "--amino", "--cpu", str(settings.build_threads),
             "-o", str(d / "hmmbuild.log"), str(hmm_path), str(aln_path)],
            timeout=settings.build_timeout_seconds, log_dir=d, log_name="hmmbuild",
        )
        if not build_run.ok or not hmm_path.exists():
            skipped.append(_skip(decision, fam, "hmmbuild_failed", build_run.stderr_snippet[:200]))
            continue

        match = _LENG.search(hmm_path.read_text(encoding="ascii", errors="replace")[:4000])
        mlen = int(match.group(1)) if match else 0
        if mlen < settings.profile_min_match_states or mlen < 0.5 * decision.median_length:
            skipped.append(_skip(decision, fam, "profile_too_short",
                                 f"match_states={mlen} median_len={decision.median_length}"))
            continue

        ec, purity, distribution = _consensus_ec(members, ec_by_ref)
        built.append(BuiltProfile(
            family_id=fam, hmm_path=hmm_path, members=len(aligned), match_states=mlen,
            median_length=decision.median_length,
            mean_pairwise_identity=mean_pairwise_identity(aligned),
            consensus_ec=ec, ec_purity=purity, ec_distribution=distribution,
            representative=decision.cluster.representative,
        ))
        if n % 50 == 0:
            log.info("… %d/%d clusters processed, %d profiles built", n, len(accepted), len(built))

    return built, skipped


def _skip(decision: ClusterDecision, fam: str, reason: str, detail: str = "") -> dict:
    return {
        "family_id": fam,
        "representative": decision.cluster.representative,
        "members": decision.cluster.size,
        "reason": reason,
        "detail": detail,
    }


def press_profiles(settings: Settings, profiles: list[BuiltProfile]) -> dict:
    """Concatenate the profiles and index them for hmmscan."""
    out = settings.profile_db
    out.parent.mkdir(parents=True, exist_ok=True)
    for stale in out.parent.glob(out.name + ".h3*"):
        stale.unlink()

    with out.open("wb") as dest:
        for p in profiles:
            dest.write(p.hmm_path.read_bytes())

    if not profiles:
        log.warning("no profiles passed QC; hmmscan will be reported unavailable")
        return {"profiles": 0, "pressed": False}

    run = run_tool(
        settings.hmmpress_bin, ["-f", str(out)],
        timeout=settings.build_timeout_seconds,
        log_dir=out.parent, log_name="hmmpress",
    )
    if not run.ok:
        raise RuntimeError(
            f"hmmpress failed (exit {run.returncode}): {run.stderr_snippet[:400]}"
        )
    return {
        "profiles": len(profiles),
        "pressed": True,
        "bytes": sum(p.stat().st_size for p in out.parent.glob(out.name + "*")),
    }


def write_profile_metadata(store, profiles: list[BuiltProfile]) -> None:
    store.execute("DELETE FROM profile")
    for p in profiles:
        store.execute(
            "INSERT INTO profile (family_id, members, consensus_ec, ec_purity, "
            "ec_distribution, median_length, match_states, mean_pairwise_identity, "
            "representative_ref_id, description) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                p.family_id, p.members, p.consensus_ec, p.ec_purity,
                json.dumps(p.ec_distribution), p.median_length, p.match_states,
                p.mean_pairwise_identity, p.representative,
                f"{p.members} members, {p.match_states} match states, "
                f"mean pairwise identity {p.mean_pairwise_identity:.0%}",
            ),
        )
    store.commit()


def profile_stats(profiles: list[BuiltProfile], skipped: list[dict]) -> dict:
    reasons: Counter = Counter(s["reason"] for s in skipped)
    with_ec = [p for p in profiles if p.consensus_ec]
    return {
        "profiles_built": len(profiles),
        "profiles_skipped_at_build": len(skipped),
        "profiles_skipped_by_reason": dict(sorted(reasons.items())),
        "profiles_with_ec": len(with_ec),
        "distinct_consensus_ec": len({p.consensus_ec for p in with_ec}),
        "members_covered": sum(p.members for p in profiles),
        "mean_pairwise_identity": {
            "min": min((p.mean_pairwise_identity for p in profiles), default=None),
            "median": round(statistics.median([p.mean_pairwise_identity for p in profiles]), 4)
            if profiles else None,
        },
        "match_states": {
            "min": min((p.match_states for p in profiles), default=None),
            "max": max((p.match_states for p in profiles), default=None),
        },
        "mean_ec_purity": round(
            statistics.mean([p.ec_purity for p in with_ec]), 4) if with_ec else None,
    }


def available(settings: Settings) -> tuple[bool, str]:
    for binary in (settings.phmmer_bin, settings.hmmscan_bin):
        if settings.which(binary) is None:
            return False, f"executable not found: {binary}"
    if not settings.references_fasta.exists():
        return False, "reference FASTA missing (phmmer target database)"
    return True, f"HMMER ready ({tool_version(settings.phmmer_bin, ['-h']) or 'version unknown'})"


def profiles_available(settings: Settings) -> tuple[bool, str]:
    db = settings.profile_db
    if not db.exists() or db.stat().st_size == 0:
        return False, "no profile database (profile HMM layer not built)"
    missing = [s for s in (".h3m", ".h3i", ".h3f", ".h3p")
               if not db.with_suffix(db.suffix + s).exists()]
    if missing:
        return False, f"profile database not pressed, missing {', '.join(missing)}"
    return True, "profile HMM database pressed and ready"
