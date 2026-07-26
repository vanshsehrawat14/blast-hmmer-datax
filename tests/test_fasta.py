"""FASTA parsing, sequence validation and submission handling."""

from __future__ import annotations

import pytest

from app.fasta import (
    FastaError, clean_sequence, looks_like_nucleotide, parse_fasta,
    parse_submission, safe_display, write_fasta,
)
from tests.conftest import ADH1_YEAST

LIMITS = dict(max_sequences=5, max_length=2000)


def clean(seq, **kw):
    return clean_sequence(seq, min_length=kw.get("min_length", 30),
                          max_length=kw.get("max_length", 10000),
                          max_ambiguous_fraction=kw.get("max_ambiguous_fraction", 0.1))


# ---------------------------------------------------------------- parse_fasta
def test_parses_multiple_records():
    text = ">one desc\nMKV\nAAA\n>two\nMKT\n"
    assert parse_fasta(text) == [("one desc", "MKVAAA"), ("two", "MKT")]


def test_tolerates_crlf_blank_lines_and_bom():
    text = "﻿>a\r\n\r\nMKV\r\nAAA\r\n"
    assert parse_fasta(text) == [("a", "MKVAAA")]


def test_missing_header_is_rejected():
    with pytest.raises(FastaError, match="FASTA header"):
        parse_fasta("MKVAAA\nMKT\n")


def test_empty_input_is_rejected():
    with pytest.raises(FastaError):
        parse_fasta("   \n\n")


# ---------------------------------------------------------------- clean_sequence
def test_normalizes_case_whitespace_and_numbering():
    result = clean("  1 mkv aaa\n 61 kkk  ", min_length=3)
    assert result.sequence == "MKVAAAKKK"


def test_null_and_empty_are_distinguished():
    assert clean(None).reason == "null_sequence"
    assert clean("   ").reason == "empty_sequence"


def test_gaps_and_dots_are_stripped():
    assert clean("MKV--AAA..KKK", min_length=3).sequence == "MKVAAAKKK"


def test_terminal_stop_is_stripped_internal_stop_rejected():
    ok = clean("MKVAAAKKK*", min_length=3)
    assert ok.sequence == "MKVAAAKKK"
    assert "stripped_terminal_stop" in ok.notes
    assert clean("MKV*AAA", min_length=3).reason == "internal_stop"


def test_invalid_characters_are_named():
    reason = clean("MKV!AA@A", min_length=3).reason
    assert reason.startswith("invalid_characters:")
    assert "!" in reason and "@" in reason


def test_ambiguity_codes_are_accepted_but_bounded():
    # 5 ambiguous residues in 205 is under the 10% ceiling; 200 in 220 is not.
    mild = ADH1_YEAST[:100] + "XXBZJ" + ADH1_YEAST[100:200]
    assert clean(mild).sequence == mild
    assert clean("X" * 200 + ADH1_YEAST[:20]).reason == "excessive_ambiguity"


def test_length_bounds():
    assert clean("MKVAAA", min_length=30).reason == "too_short"
    assert clean(ADH1_YEAST, max_length=10).reason == "too_long"


def test_nucleotide_text_is_caught_by_composition():
    # Every one of A/C/G/T is also a valid amino acid letter, so this can only
    # be detected compositionally.
    dna = "ATGGCGTAGCTAGCTAGCATCGATCGATCGATCGTAGCTAGCTAGCTAGCATCGATCGAT" * 2
    assert looks_like_nucleotide(dna)
    assert clean(dna).reason == "looks_like_nucleotide"
    assert not looks_like_nucleotide(ADH1_YEAST)


def test_short_nucleotide_like_peptides_are_not_flagged():
    # Below the length floor the heuristic must not fire on a real peptide.
    assert not looks_like_nucleotide("ACGTACGTACGT")


# ---------------------------------------------------------------- safe_display
def test_display_strips_control_characters_and_truncates():
    # NUL is deleted outright; tab becomes a space so it cannot split a column.
    assert safe_display("bad\x00head\ter") == "badhead er"
    assert safe_display("") == "(no description)"
    assert safe_display("x" * 200).endswith("…")


def test_display_does_not_mangle_markup_that_templates_will_escape():
    # Escaping is the template engine's job; this must pass the text through
    # so the escaping test downstream is meaningful.
    assert "<script>" in safe_display("<script>alert(1)</script>")


# ---------------------------------------------------------------- submissions
def test_assigns_internal_ids_and_keeps_original_headers():
    text = f">sp|P00330|ADH1_YEAST desc here\n{ADH1_YEAST}\n>second\n{ADH1_YEAST[:100]}\n"
    records = parse_submission(text, **LIMITS)
    assert [r.query_id for r in records] == ["Q1", "Q2"]
    assert records[0].description.startswith("sp|P00330|ADH1_YEAST")


def test_rejects_too_many_sequences():
    text = "".join(f">s{i}\n{ADH1_YEAST[:60 + i]}\n" for i in range(6))
    with pytest.raises(FastaError, match="at most 5"):
        parse_submission(text, **LIMITS)


def test_rejects_duplicate_identifiers():
    text = f">dup one\n{ADH1_YEAST}\n>dup two\n{ADH1_YEAST[:100]}\n"
    with pytest.raises(FastaError, match="Duplicate sequence identifier"):
        parse_submission(text, **LIMITS)


def test_rejects_duplicate_sequences():
    text = f">a\n{ADH1_YEAST}\n>b\n{ADH1_YEAST}\n"
    with pytest.raises(FastaError, match="same"):
        parse_submission(text, **LIMITS)


def test_rejects_header_with_no_residues():
    with pytest.raises(FastaError, match="no residues"):
        parse_submission(f">a\n{ADH1_YEAST}\n>b\n\n", **LIMITS)


def test_rejects_oversized_sequence():
    with pytest.raises(FastaError, match="residue limit"):
        parse_submission(f">a\n{'M' * 3000}\n", max_sequences=5, max_length=2000)


def test_rejects_invalid_residues_with_a_usable_message():
    with pytest.raises(FastaError, match="not amino acids"):
        parse_submission(f">a\n{ADH1_YEAST[:50]}###\n", **LIMITS)


def test_rejects_nucleotide_submission():
    dna = "ATGGCGTAGCTAGCTAGCATCGATCGATCGATCGTAGCTAGCTAGCTAGCATCGATCGAT" * 2
    with pytest.raises(FastaError, match="nucleotide"):
        parse_submission(f">gene\n{dna}\n", **LIMITS)


def test_written_fasta_contains_only_internal_ids(tmp_path):
    text = f">nasty\theader with spaces\n{ADH1_YEAST}\n"
    records = parse_submission(text, **LIMITS)
    out = tmp_path / "q.fasta"
    write_fasta(records, out)
    body = out.read_text()
    assert body.startswith(">Q1\n")
    assert "nasty" not in body
    # Wrapped at 60 columns, ASCII only.
    assert all(len(line) <= 60 for line in body.splitlines() if not line.startswith(">"))
