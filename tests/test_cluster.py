"""Clustering quality control and profile annotation.

The gates are what stop a scientifically meaningless profile from being built,
so they are tested directly rather than only through the full build.
"""

from __future__ import annotations

import pytest

from app.references.cluster import Cluster, cluster_stats, qc_clusters, read_fasta_lengths
from app.references.hmmer_build import (
    _consensus_ec, alignment_core_columns, family_id, mean_pairwise_identity,
)


def clusters_from(sizes: dict[str, int]) -> list[Cluster]:
    out, i = [], 0
    for rep, n in sizes.items():
        i += 1
        out.append(Cluster(index=i, representative=rep,
                           members=[f"{rep}_m{j}" for j in range(n)]))
    return out


def uniform_lengths(clusters, length=300) -> dict[str, int]:
    return {m: length for c in clusters for m in c.members}


def test_small_clusters_are_rejected(settings):
    settings.profile_min_members = 5
    cs = clusters_from({"A": 4, "B": 5})
    decisions = {d.cluster.representative: d for d in
                 qc_clusters(cs, uniform_lengths(cs), settings)}
    assert decisions["A"].accepted is False
    assert decisions["A"].reason == "too_few_members(4)"
    assert decisions["B"].accepted is True


def test_length_outliers_are_dropped_before_alignment(settings):
    settings.profile_min_members = 3
    cs = clusters_from({"A": 6})
    lengths = uniform_lengths(cs)
    lengths["A_m0"] = 50      # fragment
    lengths["A_m1"] = 2000    # fusion / much longer entry
    decision = qc_clusters(cs, lengths, settings)[0]
    assert decision.accepted
    assert set(decision.kept_members) == {"A_m2", "A_m3", "A_m4", "A_m5"}
    assert decision.median_length == 300


def test_a_cluster_split_between_fragments_and_full_length_is_rejected(settings):
    """Half fragments, half full length: the median lands between the two
    groups, every member is an outlier, and nothing is left to align."""
    settings.profile_min_members = 4
    cs = clusters_from({"A": 6})
    lengths = uniform_lengths(cs)
    for m in list(lengths)[:3]:
        lengths[m] = 40
    decision = qc_clusters(cs, lengths, settings)[0]
    assert not decision.accepted
    assert decision.reason.startswith("too_few_after_length_filter")


def test_large_clusters_are_subsampled_deterministically(settings):
    settings.profile_min_members = 3
    settings.profile_max_members = 10
    cs = clusters_from({"A": 100})
    lengths = uniform_lengths(cs)
    first = qc_clusters(cs, lengths, settings)[0].kept_members
    second = qc_clusters(cs, lengths, settings)[0].kept_members
    assert len(first) == 10
    assert first == second


def test_cluster_without_length_data_is_reported_not_crashed(settings):
    cs = clusters_from({"A": 5})
    decision = qc_clusters(cs, {}, settings)[0]
    assert not decision.accepted and decision.reason == "no_length_data"


def test_stats_summarise_the_gates(settings):
    settings.profile_min_members = 5
    cs = clusters_from({"A": 2, "B": 3, "C": 8})
    stats = cluster_stats(qc_clusters(cs, uniform_lengths(cs), settings))
    assert stats["clusters_total"] == 3
    assert stats["clusters_accepted"] == 1
    assert stats["clusters_skipped_by_reason"] == {"too_few_members": 2}


# ---------------------------------------------------------------- alignment QC
def test_core_columns_counts_only_well_occupied_positions():
    aligned = {
        "a": "MKV--AAA",
        "b": "MKV--AAA",
        "c": "MKVLLAAA",
        "d": "MKV--AAA",
    }
    core, width = alignment_core_columns(aligned)
    assert width == 8
    assert core == 6          # the two columns present in only one sequence drop out


def test_ragged_alignment_is_an_error_not_a_silent_miscount():
    with pytest.raises(ValueError, match="unequal length"):
        alignment_core_columns({"a": "MKV", "b": "MK"})


def test_pairwise_identity_ignores_gapped_positions():
    assert mean_pairwise_identity({"a": "MKVAA", "b": "MKVAA"}) == 1.0
    assert mean_pairwise_identity({"a": "MKVAA", "b": "MKV--"}) == 1.0
    assert mean_pairwise_identity({"a": "MKVAA", "b": "MKVLL"}) == pytest.approx(0.6)


def test_pairwise_identity_is_deterministic_when_sampled():
    big = {f"s{i}": "MKVAA" * 20 for i in range(200)}
    assert mean_pairwise_identity(big) == mean_pairwise_identity(big)


# ---------------------------------------------------------------- EC annotation
def test_consensus_ec_counts_multi_ec_members_under_each_ec():
    ec, purity, dist = _consensus_ec(
        ["a", "b", "c", "d"],
        {"a": "1.1.1.1", "b": "1.1.1.1", "c": "1.1.1.1;4.2.1.1", "d": "4.2.1.1"},
    )
    assert ec == "1.1.1.1"
    assert dist == {"1.1.1.1": 3, "4.2.1.1": 2}
    assert purity == 0.75


def test_unannotated_members_do_not_lower_purity():
    ec, purity, _ = _consensus_ec(
        ["a", "b", "c"], {"a": "1.1.1.1", "b": "1.1.1.1", "c": None})
    assert ec == "1.1.1.1" and purity == 1.0


def test_a_family_with_no_ec_at_all_is_reported_as_unannotated():
    assert _consensus_ec(["a"], {"a": None}) == (None, 0.0, {})


def test_family_ids_are_zero_padded_and_sortable():
    assert family_id(1) == "EXF00001"
    assert sorted([family_id(10), family_id(2)]) == ["EXF00002", "EXF00010"]


# ---------------------------------------------------------------- fasta helpers
def test_lengths_are_read_without_loading_sequences(tmp_path):
    p = tmp_path / "r.fasta"
    p.write_text(">EXR1 EC=1.1.1.1 src=swissprot\nMKVAA\nMKV\n>EXR2\nMK\n")
    assert read_fasta_lengths(p) == {"EXR1": 8, "EXR2": 2}
