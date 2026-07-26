"""FASTA parsing, validation and sequence normalization.

Used on both sides of the system: to clean rows coming out of `enzymesdata`
and to validate untrusted user submissions. Keeping one implementation means
the reference database and the queries agree on what a protein sequence is.

Every submitted sequence is renamed to an internal `Q<n>` before it reaches a
subprocess. User headers can contain tabs, quotes, control characters and
non-ASCII text, all of which would either corrupt the column structure of
BLAST/HMMER tabular output or be silently truncated at the first whitespace.
The original header is carried alongside for display only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# IUPAC protein alphabet. B/Z/J are ambiguity codes, X is "any", U is
# selenocysteine and O is pyrrolysine; BLAST+ and HMMER both accept all of
# them, so rejecting them would discard real Swiss-Prot entries.
STANDARD_AA = set("ACDEFGHIKLMNPQRSTVWY")
AMBIGUOUS_AA = set("BZJXUO")
VALID_AA = STANDARD_AA | AMBIGUOUS_AA

# A/C/G/T/U/N are all valid amino acid letters, so a nucleotide sequence
# passes an alphabet check cleanly and then produces nonsense alignments.
# Composition is the only signal available: real proteins essentially never
# consist almost entirely of these six residues over any useful length. The
# same heuristic is what BLAST uses to warn about mistyped -dbtype.
NUCLEOTIDE_LETTERS = set("ACGTUN")
NUCLEOTIDE_MIN_LENGTH = 50
NUCLEOTIDE_FRACTION = 0.90

_WHITESPACE_OR_DIGITS = re.compile(r"[\s\d]+")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class FastaError(ValueError):
    """Raised for input the user must fix. The message is shown verbatim."""


@dataclass
class SequenceRecord:
    query_id: str          # internal, safe
    description: str       # original header text, for display only
    sequence: str


@dataclass
class CleanResult:
    sequence: str | None
    reason: str | None = None
    ambiguous_fraction: float = 0.0
    notes: list[str] = field(default_factory=list)


def clean_sequence(
    raw: str | None,
    *,
    min_length: int,
    max_length: int,
    max_ambiguous_fraction: float,
) -> CleanResult:
    """Normalize one sequence and decide whether it is usable.

    Returns the cleaned sequence, or None plus a machine-readable reason. The
    reasons are the vocabulary used by the export's skip report.
    """
    if raw is None:
        return CleanResult(None, "null_sequence")

    # Strip anything a database column might have picked up: wrapped FASTA
    # newlines, residue numbering from a pasted alignment, stray control bytes.
    seq = _CONTROL.sub("", raw)
    seq = _WHITESPACE_OR_DIGITS.sub("", seq).upper()
    seq = seq.replace("-", "").replace(".", "")

    notes: list[str] = []
    if seq.endswith("*"):
        seq = seq.rstrip("*")
        notes.append("stripped_terminal_stop")
    if "*" in seq:
        return CleanResult(None, "internal_stop")

    if not seq:
        return CleanResult(None, "empty_sequence")

    bad = sorted(set(seq) - VALID_AA)
    if bad:
        return CleanResult(None, f"invalid_characters:{''.join(bad[:5])}")

    if len(seq) < min_length:
        return CleanResult(None, "too_short")
    if len(seq) > max_length:
        return CleanResult(None, "too_long")

    if looks_like_nucleotide(seq):
        return CleanResult(None, "looks_like_nucleotide")

    amb = sum(1 for c in seq if c in AMBIGUOUS_AA) / len(seq)
    if amb > max_ambiguous_fraction:
        return CleanResult(None, "excessive_ambiguity", amb)

    return CleanResult(seq, None, amb, notes)


def looks_like_nucleotide(seq: str) -> bool:
    if len(seq) < NUCLEOTIDE_MIN_LENGTH:
        return False
    hits = sum(1 for c in seq if c in NUCLEOTIDE_LETTERS)
    return hits / len(seq) >= NUCLEOTIDE_FRACTION


def parse_fasta(text: str) -> list[tuple[str, str]]:
    """Split FASTA text into (header, raw_sequence) pairs.

    Tolerates CRLF, blank lines and a leading BOM. Does not validate residues;
    that is `clean_sequence`'s job. Raises FastaError only for structural
    problems the user can see and fix.
    """
    text = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    if not text.strip():
        raise FastaError("No sequence data was submitted.")

    lines = text.split("\n")
    first = next((ln for ln in lines if ln.strip()), "")
    if not first.startswith(">"):
        raise FastaError(
            "Input does not start with a FASTA header. Every sequence needs a "
            "line beginning with '>', for example: >my_protein"
        )

    records: list[tuple[str, list[str]]] = []
    for line in lines:
        if not line.strip():
            continue
        if line.startswith(">"):
            records.append((line[1:].strip(), []))
        else:
            records[-1][1].append(line.strip())

    return [(hdr, "".join(chunks)) for hdr, chunks in records]


def safe_display(header: str, *, limit: int = 120) -> str:
    """Make a user header safe to store and short enough to render.

    HTML escaping is left to the template engine; this only removes control
    characters and caps the length. An empty header becomes a placeholder so
    the results page never shows a blank row label.
    """
    cleaned = _CONTROL.sub("", header).replace("\t", " ").strip()
    if not cleaned:
        return "(no description)"
    return cleaned[:limit] + ("…" if len(cleaned) > limit else "")


def parse_submission(
    text: str,
    *,
    max_sequences: int,
    max_length: int,
    min_length: int = 1,
) -> list[SequenceRecord]:
    """Validate a user submission and assign internal identifiers.

    Raises FastaError with a message intended for the submission page. Both
    duplicate headers and duplicate sequences are reported rather than
    silently merged: for a handful of user sequences, telling them is more
    useful than guessing what they meant.
    """
    raw_records = parse_fasta(text)

    if not raw_records:
        raise FastaError("No sequences found in the submitted FASTA.")
    if len(raw_records) > max_sequences:
        raise FastaError(
            f"{len(raw_records)} sequences submitted; this test server accepts "
            f"at most {max_sequences} per job."
        )

    seen_headers: dict[str, int] = {}
    out: list[SequenceRecord] = []
    for i, (header, raw_seq) in enumerate(raw_records, start=1):
        label = safe_display(header)
        # Tools truncate a defline at the first whitespace, so two headers
        # that differ only after a space are indistinguishable downstream.
        key = label.split(" ")[0].lower()
        if key in seen_headers:
            raise FastaError(
                f"Duplicate sequence identifier '{key}' (sequences "
                f"{seen_headers[key]} and {i}). Identifiers must be unique."
            )
        seen_headers[key] = i

        if not raw_seq.strip():
            raise FastaError(f"Sequence {i} ('{label}') has a header but no residues.")

        cleaned = clean_sequence(
            raw_seq,
            min_length=min_length,
            max_length=max_length,
            max_ambiguous_fraction=1.0,  # queries are the user's problem to judge
        )
        if cleaned.sequence is None:
            reason = cleaned.reason or "invalid"
            if reason.startswith("invalid_characters"):
                chars = reason.split(":", 1)[1]
                raise FastaError(
                    f"Sequence {i} ('{label}') contains characters that are not "
                    f"amino acids: {chars}. This server accepts protein "
                    f"sequences only — nucleotide input will fail here."
                )
            if reason == "too_long":
                raise FastaError(
                    f"Sequence {i} ('{label}') is longer than the {max_length} "
                    f"residue limit for this test server."
                )
            if reason == "internal_stop":
                raise FastaError(
                    f"Sequence {i} ('{label}') contains an internal stop codon "
                    f"('*'). Submit the mature protein sequence."
                )
            if reason == "looks_like_nucleotide":
                raise FastaError(
                    f"Sequence {i} ('{label}') looks like a nucleotide sequence: "
                    f"almost every residue is A, C, G, T, U or N. Those are also "
                    f"valid amino acid letters, so translate the sequence before "
                    f"submitting it — searching it as a protein gives nonsense."
                )
            raise FastaError(f"Sequence {i} ('{label}') is not a usable protein sequence ({reason}).")

        out.append(SequenceRecord(f"Q{i}", label, cleaned.sequence))

    by_seq: dict[str, str] = {}
    for rec in out:
        if rec.sequence in by_seq:
            raise FastaError(
                f"'{rec.description}' and '{by_seq[rec.sequence]}' are the same "
                f"sequence. Submit each distinct protein once."
            )
        by_seq[rec.sequence] = rec.description

    return out


def write_fasta(records: list[SequenceRecord], path) -> None:
    """Write internal identifiers only — user text never reaches a tool."""
    with open(path, "w", encoding="ascii", newline="\n") as fh:
        for rec in records:
            fh.write(f">{rec.query_id}\n")
            for i in range(0, len(rec.sequence), 60):
                fh.write(rec.sequence[i:i + 60] + "\n")
