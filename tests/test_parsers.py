"""BLAST and HMMER output parsing.

Fixtures are written in the tools' real column layouts. The HMMER ones exist
mainly to pin the coordinate role-flip between phmmer and hmmscan, which is
the easiest thing in this codebase to get silently wrong.
"""

from __future__ import annotations

import pytest

from app.search.parsers import (
    ParseError, merge_intervals, parse_blast_tabular, parse_hmmscan,
    parse_phmmer, rank_hits,
)

# qseqid sseqid pident length qstart qend sstart send qlen slen evalue bitscore qcovs
BLAST_TSV = "\n".join([
    "Q1\tEXR10\t98.500\t347\t1\t347\t1\t347\t350\t350\t0.0\t700.5\t99",
    "Q1\tEXR11\t45.200\t300\t20\t320\t15\t315\t350\t340\t2e-90\t280.1\t86",
    # Two HSPs against the same subject: coverage sums, score does not.
    "Q1\tEXR12\t35.000\t100\t1\t100\t5\t104\t350\t400\t1e-20\t80.0\t57",
    "Q1\tEXR12\t30.000\t100\t201\t300\t250\t349\t350\t400\t1e-10\t50.0\t57",
    "Q2\tEXR10\t22.000\t40\t10\t49\t100\t139\t500\t350\t0.5\t20.1\t8",
    "",
])

PHMMER_TBL = "\n".join([
    "# target name        accession  query name accession    E-value  score  bias",
    "EXR10 - Q1 - 1.2e-150 500.1 0.0 1.4e-150 499.9 0.0 1.0 1 0 0 1 1 1 1 alcohol dehydrogenase",
    "EXR11 - Q1 - 3.0e-40 140.2 0.1 5.0e-40 139.0 0.1 1.1 1 0 0 1 1 1 1 related protein",
    "EXR10 - Q2 - 0.004 12.0 0.0 0.02 10.0 0.0 1.2 1 0 0 1 1 1 1 alcohol dehydrogenase",
    "",
])

# target tacc tlen query qacc qlen E score bias # of c-E i-E score bias
# hmmfrom hmmto alifrom alito envfrom envto acc description
PHMMER_DOM = "\n".join([
    "EXR10 - 350 Q1 - 340 1.2e-150 500.1 0.0 1 1 2.0e-153 1.4e-150 499.9 0.0 5 330 8 333 6 335 0.98 alcohol dehydrogenase",
    "EXR11 - 340 Q1 - 340 3.0e-40 140.2 0.1 1 1 8.0e-43 5.0e-40 139.0 0.1 20 200 25 205 22 208 0.91 related protein",
    "EXR10 - 350 Q2 - 500 0.004 12.0 0.0 1 1 1.0e-05 0.02 10.0 0.0 100 140 90 130 88 132 0.80 alcohol dehydrogenase",
    "",
])

HMMSCAN_TBL = "\n".join([
    "EXF00001 - Q1 - 5.0e-120 400.0 0.0 6.0e-120 399.0 0.0 1.0 1 0 0 1 1 1 1 family model",
    "",
])

HMMSCAN_DOM = "\n".join([
    "EXF00001 - 300 Q1 - 340 5.0e-120 400.0 0.0 1 1 1.0e-123 6.0e-120 399.0 0.0 10 290 15 300 12 305 0.97 family model",
    "",
])


# ---------------------------------------------------------------- intervals
def test_merge_intervals_handles_overlap_adjacency_and_order():
    assert merge_intervals([]) == 0
    assert merge_intervals([(1, 10)]) == 10
    assert merge_intervals([(1, 10), (5, 15)]) == 15
    assert merge_intervals([(11, 20), (1, 10)]) == 20          # unsorted input
    assert merge_intervals([(1, 10), (30, 40)]) == 21          # disjoint
    assert merge_intervals([(20, 11), (10, 1)]) == 20          # reversed pairs


# ---------------------------------------------------------------- blast
def test_blast_one_row_per_query_subject_pair():
    hits = parse_blast_tabular(BLAST_TSV)
    assert len(hits) == 4
    assert {h.query_id for h in hits} == {"Q1", "Q2"}


def test_blast_best_hsp_wins_and_coverage_sums():
    hits = {(h.query_id, h.hit_id): h for h in parse_blast_tabular(BLAST_TSV)}
    multi = hits[("Q1", "EXR12")]
    # Score/identity come from the best HSP…
    assert multi.bitscore == 80.0
    assert multi.percent_identity == 35.0
    assert multi.alignment_length == 100
    assert multi.domain_count == 2
    # …while subject coverage merges both HSP spans: (5-104) + (250-349) = 200/400.
    assert multi.subject_coverage == pytest.approx(0.5)
    # Query coverage comes from BLAST's own qcovs column.
    assert multi.query_coverage == pytest.approx(0.57)


def test_blast_rejects_wrong_column_count():
    with pytest.raises(ParseError, match="columns"):
        parse_blast_tabular("Q1\tEXR1\t99.0\n")


def test_blast_rejects_non_numeric_field():
    bad = BLAST_TSV.replace("0.0\t700.5", "notanumber\t700.5")
    with pytest.raises(ParseError, match="malformed"):
        parse_blast_tabular(bad)


def test_blast_empty_output_is_no_hits_not_an_error():
    assert parse_blast_tabular("") == []
    assert parse_blast_tabular("# comment only\n") == []


# ---------------------------------------------------------------- phmmer
def test_phmmer_query_coordinates_come_from_the_hmm_columns():
    """phmmer turns the *query* into the profile, so hmm from/to are query
    positions and env from/to are positions on the reference."""
    hits = {(h.query_id, h.hit_id): h for h in parse_phmmer(PHMMER_TBL, PHMMER_DOM)}
    h = hits[("Q1", "EXR10")]
    assert (h.domain_start, h.domain_end) == (5, 330)          # hmm from/to
    assert h.query_coverage == pytest.approx(326 / 340, abs=1e-4)
    assert h.subject_coverage == pytest.approx(330 / 350, abs=1e-4)
    assert h.evalue == 1.2e-150 and h.bitscore == 500.1
    assert h.domain_evalue == 1.4e-150 and h.domain_bitscore == 499.9
    # HMMER reports no identity; the field must stay empty rather than be faked.
    assert h.percent_identity is None
    assert h.alignment_length is None


def test_hmmscan_query_coordinates_come_from_the_envelope_columns():
    """hmmscan makes the profile the target, so the roles are the other way
    round: env from/to are query positions, hmm from/to index the profile."""
    h = parse_hmmscan(HMMSCAN_TBL, HMMSCAN_DOM)[0]
    assert (h.domain_start, h.domain_end) == (15, 300)         # ali from/to on the query
    assert h.query_coverage == pytest.approx(294 / 340, abs=1e-4)   # env span / qlen
    assert h.subject_coverage == pytest.approx(281 / 300, abs=1e-4)  # hmm span / profile len


def test_hmmer_tables_without_a_domain_row_still_produce_a_hit():
    hits = parse_phmmer(PHMMER_TBL, "")
    assert len(hits) == 3
    assert all(h.query_coverage is None for h in hits)


def test_hmmer_rejects_truncated_rows():
    with pytest.raises(ParseError, match="fields"):
        parse_phmmer("EXR1 - Q1 - 1e-5\n", "")


# ---------------------------------------------------------------- ranking
def test_rank_sorts_by_evalue_then_bitscore_and_truncates():
    ranked = rank_hits(parse_blast_tabular(BLAST_TSV), limit=2)
    assert [h.hit_id for h in ranked["Q1"]] == ["EXR10", "EXR11"]
    assert len(ranked["Q1"]) == 2
    assert [h.hit_id for h in ranked["Q2"]] == ["EXR10"]


def test_rank_is_deterministic_for_identical_scores():
    from app.search.parsers import RawHit
    hits = [RawHit("Q1", h, 1e-5, 100.0) for h in ("EXRb", "EXRa", "EXRc")]
    assert [h.hit_id for h in rank_hits(hits, 10)["Q1"]] == ["EXRa", "EXRb", "EXRc"]
