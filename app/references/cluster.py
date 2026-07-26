"""Group the exported references into homologous sequence families.

Why not group by EC number, which is the obvious thing to try:

An EC number names a chemical reaction, not a protein family. Proteins that
catalyse the same reaction are routinely non-homologous — analogous enzymes
that arrived at the same chemistry independently are common enough to have
their own literature (Galperin, Walker & Koonin, Genome Research 8:779, 1998).
A profile HMM is a position-specific model of one alignable family; feeding it
an alignment of unrelated sequences produces a profile whose match states
describe nothing real, and the E-values it reports are then meaningless rather
than merely weak. So EC is not used to define the groups.

Instead the whole validated export is clustered on sequence similarity, and
each resulting cluster is annotated with whatever EC numbers its members
carry. That ordering has three useful consequences:

  * one EC can legitimately produce several profiles, which is the correct
    outcome for a reaction served by several unrelated folds;
  * a cluster's EC composition becomes a reported statistic (`ec_purity`)
    rather than an assumption;
  * references with no EC annotation at all still contribute to a family.

MMseqs2 is used for the clustering itself: it is the conventional tool for
this at protein-database scale and its cascaded clustering handles the
"millions of sequences" case that a copied TrEMBL-derived table can reach
(Steinegger & Söding, Nature Biotechnology 35:1026, 2017).
"""

from __future__ import annotations

import logging
import shutil
import statistics
from dataclasses import dataclass, field
from pathlib import Path

from app.config import Settings
from app.search.subprocess_utils import run_tool

log = logging.getLogger(__name__)


@dataclass
class Cluster:
    index: int
    representative: str
    members: list[str] = field(default_factory=list)

    @property
    def size(self) -> int:
        return len(self.members)


@dataclass
class ClusterDecision:
    cluster: Cluster
    accepted: bool
    reason: str = ""
    kept_members: list[str] = field(default_factory=list)
    median_length: int = 0


def read_fasta_lengths(path: Path) -> dict[str, int]:
    """Sequence lengths keyed by identifier, read in one pass."""
    lengths: dict[str, int] = {}
    current: str | None = None
    total = 0
    with path.open("r", encoding="ascii") as fh:
        for line in fh:
            if line.startswith(">"):
                if current is not None:
                    lengths[current] = total
                current, total = line[1:].split(None, 1)[0], 0
            else:
                total += len(line.strip())
    if current is not None:
        lengths[current] = total
    return lengths


def read_fasta(path: Path) -> dict[str, str]:
    seqs: dict[str, str] = {}
    current: str | None = None
    chunks: list[str] = []
    with path.open("r", encoding="ascii") as fh:
        for line in fh:
            if line.startswith(">"):
                if current is not None:
                    seqs[current] = "".join(chunks)
                current, chunks = line[1:].split(None, 1)[0], []
            else:
                chunks.append(line.strip())
    if current is not None:
        seqs[current] = "".join(chunks)
    return seqs


def run_mmseqs_cluster(settings: Settings, work_dir: Path) -> list[Cluster]:
    """Cascaded clustering of the exported references.

    `--cov-mode 0` requires the coverage threshold to hold on *both* sequences,
    so a short fragment cannot join a family by matching one domain of a large
    multidomain protein. That is the property that makes a single MSA of the
    resulting cluster defensible.
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    tmp = work_dir / "mmseqs_tmp"
    if tmp.exists():
        shutil.rmtree(tmp)
    prefix = work_dir / "clusters"

    args = [
        "easy-cluster",
        str(settings.references_fasta),
        str(prefix),
        str(tmp),
        "--min-seq-id", f"{settings.cluster_min_seq_id}",
        "-c", f"{settings.cluster_coverage}",
        "--cov-mode", "0",
        "--cluster-mode", "0",   # greedy set cover: every member is >= threshold to its representative
        "--threads", str(settings.build_threads),
        "-v", "1",
    ]
    run = run_tool(
        settings.mmseqs_bin, args,
        timeout=settings.build_timeout_seconds,
        log_dir=work_dir, log_name="mmseqs_cluster",
    )
    shutil.rmtree(tmp, ignore_errors=True)
    if not run.ok:
        raise RuntimeError(
            f"mmseqs easy-cluster failed (exit {run.returncode}, "
            f"timeout={run.timed_out}): {run.stderr_snippet[:500]}"
        )

    tsv = prefix.with_name(prefix.name + "_cluster.tsv")
    if not tsv.exists():
        raise RuntimeError(f"mmseqs produced no cluster table at {tsv.name}")

    grouped: dict[str, list[str]] = {}
    with tsv.open("r", encoding="ascii") as fh:
        for line in fh:
            parts = line.rstrip("\n").split("\t")
            if len(parts) != 2:
                continue
            grouped.setdefault(parts[0], []).append(parts[1])

    # Deterministic order regardless of how mmseqs happened to emit them:
    # largest first, ties broken by representative id.
    ordered = sorted(grouped.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    return [Cluster(index=i, representative=rep, members=sorted(members))
            for i, (rep, members) in enumerate(ordered, start=1)]


def qc_clusters(clusters: list[Cluster], lengths: dict[str, int],
                settings: Settings) -> list[ClusterDecision]:
    """Decide which clusters are worth building a profile from.

    Gates, in order:

      1. size — below `profile_min_members` a profile adds little that phmmer
         does not already provide, since there are too few sequences to
         estimate position-specific emissions;
      2. fragment trim — members shorter than 70% or longer than 140% of the
         cluster median are dropped, so a partial entry cannot drag the
         alignment into a gap-dominated mess;
      3. size again, after the trim;
      4. subsample — clusters above `profile_max_members` are thinned evenly.
         This is a runtime bound on MAFFT, not a scientific claim; hmmbuild
         applies Henikoff position-based weighting, so a redundant cluster
         does not gain accuracy from its 501st member.

    Alignment- and profile-level gates run later in hmmer_build, once MAFFT
    and hmmbuild have actually produced something to measure.
    """
    decisions: list[ClusterDecision] = []
    for cluster in clusters:
        member_lengths = [lengths[m] for m in cluster.members if m in lengths]
        if not member_lengths:
            decisions.append(ClusterDecision(cluster, False, "no_length_data"))
            continue
        median = int(statistics.median(member_lengths))

        if cluster.size < settings.profile_min_members:
            decisions.append(ClusterDecision(
                cluster, False, f"too_few_members({cluster.size})", median_length=median))
            continue

        lo, hi = 0.7 * median, 1.4 * median
        kept = [m for m in cluster.members if m in lengths and lo <= lengths[m] <= hi]
        if len(kept) < settings.profile_min_members:
            decisions.append(ClusterDecision(
                cluster, False,
                f"too_few_after_length_filter({len(kept)}/{cluster.size})",
                median_length=median))
            continue

        if len(kept) > settings.profile_max_members:
            step = len(kept) / settings.profile_max_members
            kept = [kept[int(i * step)] for i in range(settings.profile_max_members)]

        decisions.append(ClusterDecision(cluster, True, "", kept, median))
    return decisions


def cluster_stats(decisions: list[ClusterDecision]) -> dict:
    accepted = [d for d in decisions if d.accepted]
    skipped = [d for d in decisions if not d.accepted]
    reasons: dict[str, int] = {}
    for d in skipped:
        reasons[d.reason.split("(")[0]] = reasons.get(d.reason.split("(")[0], 0) + 1
    sizes = [d.cluster.size for d in accepted]
    return {
        "clusters_total": len(decisions),
        "clusters_accepted": len(accepted),
        "clusters_skipped": len(skipped),
        "clusters_skipped_by_reason": dict(sorted(reasons.items())),
        "accepted_member_count": sum(len(d.kept_members) for d in accepted),
        "accepted_cluster_size": {
            "min": min(sizes) if sizes else None,
            "max": max(sizes) if sizes else None,
            "median": int(statistics.median(sizes)) if sizes else None,
        },
    }
