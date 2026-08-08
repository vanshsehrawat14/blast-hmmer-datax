"""Every value in here reaches a command line, so the bounds are the test."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import Settings
from app.search.blast import blastp_args
from app.search.params import MATRICES, ParamError, SearchParams
from app.search.parsers import RawHit, rank_hits


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(reference_dir=tmp_path / "ref", job_dir=tmp_path / "jobs")


def test_defaults_reproduce_the_configured_server(settings: Settings):
    p = SearchParams.defaults(settings)
    assert p.blast_evalue == settings.blast_evalue
    assert p.phmmer_evalue == settings.phmmer_evalue
    assert p.hmmscan_evalue == settings.hmmscan_evalue
    assert p.max_hits == settings.max_hits_per_query
    assert p.matrix == "BLOSUM62"
    assert p.comp_based_stats == "2"
    assert p.gapopen is None and p.gapextend is None
    assert p.min_query_coverage is None


def test_empty_form_changes_nothing(settings: Settings):
    assert SearchParams.from_form({}, settings) == SearchParams.defaults(settings)
    blank = {"evalue": "", "max_hits": "  ", "matrix": "", "gapopen": "",
             "gapextend": "", "comp_based_stats": "", "min_query_coverage": ""}
    assert SearchParams.from_form(blank, settings) == SearchParams.defaults(settings)


def test_one_evalue_applies_to_every_method(settings: Settings):
    p = SearchParams.from_form({"evalue": "1e-10"}, settings)
    assert (p.blast_evalue, p.phmmer_evalue, p.hmmscan_evalue) == (1e-10,) * 3


@pytest.mark.parametrize("value", ["0", "-1", "11", "abc", "nan", "inf", "1e400"])
def test_bad_evalue_is_rejected(settings: Settings, value: str):
    with pytest.raises(ParamError):
        SearchParams.from_form({"evalue": value}, settings)


@pytest.mark.parametrize("value", ["0", "101", "-5", "2.5", "ten"])
def test_bad_hit_count_is_rejected(settings: Settings, value: str):
    with pytest.raises(ParamError):
        SearchParams.from_form({"max_hits": value}, settings)


def test_matrix_is_whitelisted(settings: Settings):
    assert SearchParams.from_form({"matrix": "pam70"}, settings).matrix == "PAM70"
    for bad in ["BLOSUM63", "; rm -rf /", "BLOSUM62 -out /tmp/x", ""]:
        if bad == "":
            continue
        with pytest.raises(ParamError):
            SearchParams.from_form({"matrix": bad}, settings)


def test_comp_based_stats_is_whitelisted(settings: Settings):
    assert SearchParams.from_form({"comp_based_stats": "0"}, settings).comp_based_stats == "0"
    for bad in ["4", "D", "-1", "2; ls"]:
        with pytest.raises(ParamError):
            SearchParams.from_form({"comp_based_stats": bad}, settings)


def test_gap_costs_must_be_set_together(settings: Settings):
    with pytest.raises(ParamError):
        SearchParams.from_form({"gapopen": "11"}, settings)
    with pytest.raises(ParamError):
        SearchParams.from_form({"gapextend": "1"}, settings)
    p = SearchParams.from_form({"gapopen": "11", "gapextend": "1"}, settings)
    assert (p.gapopen, p.gapextend) == (11, 1)


@pytest.mark.parametrize("open_, extend", [("4", "1"), ("26", "1"), ("11", "0"), ("11", "6")])
def test_gap_costs_are_bounded(settings: Settings, open_: str, extend: str):
    with pytest.raises(ParamError):
        SearchParams.from_form({"gapopen": open_, "gapextend": extend}, settings)


def test_coverage_is_a_percentage_and_zero_means_off(settings: Settings):
    assert SearchParams.from_form({"min_query_coverage": "50"}, settings).min_query_coverage == 0.5
    assert SearchParams.from_form({"min_query_coverage": "0"}, settings).min_query_coverage is None
    for bad in ["-1", "101", "half"]:
        with pytest.raises(ParamError):
            SearchParams.from_form({"min_query_coverage": bad}, settings)


# -- what actually reaches blastp -------------------------------------------

def test_default_args_are_unchanged_by_the_panel(settings: Settings, tmp_path: Path):
    args = blastp_args(settings, tmp_path / "q.fasta", tmp_path / "o.tsv")
    assert args[args.index("-matrix") + 1] == "BLOSUM62"
    assert args[args.index("-comp_based_stats") + 1] == "2"
    assert args[args.index("-evalue") + 1] == str(settings.blast_evalue)
    assert "-gapopen" not in args and "-gapextend" not in args


def test_panel_values_reach_the_command_line(settings: Settings, tmp_path: Path):
    p = SearchParams.from_form(
        {"evalue": "1e-8", "matrix": "PAM30", "comp_based_stats": "1",
         "gapopen": "9", "gapextend": "1"}, settings)
    args = blastp_args(settings, tmp_path / "q.fasta", tmp_path / "o.tsv", p)
    assert args[args.index("-evalue") + 1] == "1e-08"
    assert args[args.index("-matrix") + 1] == "PAM30"
    assert args[args.index("-comp_based_stats") + 1] == "1"
    assert args[args.index("-gapopen") + 1] == "9"
    assert args[args.index("-gapextend") + 1] == "1"


def test_every_offered_matrix_survives_the_round_trip(settings: Settings, tmp_path: Path):
    for matrix in MATRICES:
        p = SearchParams.from_form({"matrix": matrix}, settings)
        args = blastp_args(settings, tmp_path / "q.fasta", tmp_path / "o.tsv", p)
        assert args[args.index("-matrix") + 1] == matrix


# -- the coverage filter -----------------------------------------------------

def _hit(hit_id: str, coverage: float | None) -> RawHit:
    return RawHit(query_id="Q1", hit_id=hit_id, evalue=1e-20, bitscore=100.0,
                  query_coverage=coverage)


def test_coverage_filter_drops_short_alignments_but_keeps_unknowns():
    hits = [_hit("short", 0.10), _hit("long", 0.95), _hit("unknown", None)]
    kept = rank_hits(hits, 10, min_query_coverage=0.5)["Q1"]
    assert {h.hit_id for h in kept} == {"long", "unknown"}


def test_no_filter_keeps_everything():
    hits = [_hit("short", 0.10), _hit("long", 0.95)]
    assert len(rank_hits(hits, 10)["Q1"]) == 2


def test_a_query_filtered_to_nothing_disappears_rather_than_reporting_hits():
    kept = rank_hits([_hit("short", 0.10)], 10, min_query_coverage=0.5)
    assert kept == {}
