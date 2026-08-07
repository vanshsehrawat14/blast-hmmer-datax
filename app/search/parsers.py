"""Parsers for BLAST tabular and HMMER tblout/domtblout output.

Pure functions over text: no subprocesses, no filesystem assumptions, no
database. That is what makes them the easiest part of this repository to lift
into EnzymeX, and what makes them cheap to test against captured fixtures.

The HMMER role flip is the thing to get right. `phmmer` turns the *submitted*
sequence into a profile and scans the reference database with it, so in its
domain table the `hmm from/to` columns are coordinates on the submitted
sequence and `ali/env from/to` are coordinates on the reference. `hmmscan`
does the opposite: the profile is the target, so `hmm from/to` are positions
in the profile and `ali/env from/to` are on the submitted sequence. Both are
normalized here so that downstream code always reads query coordinates from
the same field.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ---------------------------------------------------------------- BLAST fields
# Requested from blastp with -outfmt "6 <these>". Order is load-bearing.
BLAST_FIELDS = [
    "qseqid", "sseqid", "pident", "length", "qstart", "qend",
    "sstart", "send", "qlen", "slen", "evalue", "bitscore", "qcovs",
]
BLAST_OUTFMT = "6 " + " ".join(BLAST_FIELDS)


class ParseError(ValueError):
    pass


def merge_intervals(intervals: list[tuple[int, int]]) -> int:
    """Total length covered by inclusive 1-based (start, end) intervals."""
    if not intervals:
        return 0
    ordered = sorted((min(a, b), max(a, b)) for a, b in intervals)
    total = 0
    cur_s, cur_e = ordered[0]
    for s, e in ordered[1:]:
        if s <= cur_e + 1:
            cur_e = max(cur_e, e)
        else:
            total += cur_e - cur_s + 1
            cur_s, cur_e = s, e
    return total + cur_e - cur_s + 1


def _fraction(numerator: int, denominator: int) -> float | None:
    if not denominator:
        return None
    return min(1.0, numerator / denominator)


@dataclass
class RawHit:
    """Method-independent intermediate. Metadata is attached later."""

    query_id: str
    hit_id: str
    evalue: float
    bitscore: float
    percent_identity: float | None = None
    alignment_length: int | None = None
    query_coverage: float | None = None
    subject_coverage: float | None = None
    domain_start: int | None = None
    domain_end: int | None = None
    domain_count: int | None = None
    domain_evalue: float | None = None
    domain_bitscore: float | None = None


@dataclass
class _BlastAccumulator:
    qlen: int = 0
    slen: int = 0
    qcovs: float | None = None
    q_spans: list[tuple[int, int]] = field(default_factory=list)
    s_spans: list[tuple[int, int]] = field(default_factory=list)
    hsps: int = 0
    best: dict | None = None


def parse_blast_tabular(text: str) -> list[RawHit]:
    """Fold BLAST's one-row-per-HSP output into one row per (query, subject).

    `evalue`, `bitscore`, `percent_identity` and `alignment_length` describe
    the single best-scoring HSP, which is what BLAST itself reports as the
    hit's score in pairwise output. Coverage is the opposite: it is summed
    over all HSPs, because a hit that aligns in three pieces really does
    cover three pieces. Mixing the two conventions is deliberate and is
    spelled out on the results page.

    Subject coverage is computed here because BLAST+ has no `scovs` field;
    only query coverage (`qcovs`) is supplied by the tool.
    """
    acc: dict[tuple[str, str], _BlastAccumulator] = {}

    for lineno, line in enumerate(text.splitlines(), start=1):
        line = line.rstrip("\n")
        if not line.strip() or line.startswith("#"):
            continue
        f = line.split("\t")
        if len(f) < len(BLAST_FIELDS):
            raise ParseError(
                f"BLAST output line {lineno} has {len(f)} columns, "
                f"expected {len(BLAST_FIELDS)}"
            )
        try:
            row = dict(zip(BLAST_FIELDS, f))
            qseqid, sseqid = row["qseqid"], row["sseqid"]
            qstart, qend = int(row["qstart"]), int(row["qend"])
            sstart, send = int(row["sstart"]), int(row["send"])
            bits, ev = float(row["bitscore"]), float(row["evalue"])
            entry = acc.setdefault((qseqid, sseqid), _BlastAccumulator())
            entry.qlen = int(row["qlen"])
            entry.slen = int(row["slen"])
            entry.qcovs = float(row["qcovs"])
            entry.q_spans.append((qstart, qend))
            entry.s_spans.append((sstart, send))
            entry.hsps += 1
            if entry.best is None or bits > entry.best["bitscore"]:
                entry.best = {
                    "bitscore": bits, "evalue": ev,
                    "pident": float(row["pident"]), "length": int(row["length"]),
                    "qstart": qstart, "qend": qend,
                }
        except (ValueError, KeyError) as exc:
            raise ParseError(f"BLAST output line {lineno} is malformed: {exc}") from exc

    hits: list[RawHit] = []
    for (qseqid, sseqid), entry in acc.items():
        best = entry.best
        assert best is not None
        hits.append(RawHit(
            query_id=qseqid,
            hit_id=sseqid,
            evalue=best["evalue"],
            bitscore=best["bitscore"],
            percent_identity=best["pident"],
            alignment_length=best["length"],
            # Prefer BLAST's own qcovs; fall back to our own merge if the
            # column was somehow absent.
            query_coverage=(entry.qcovs / 100.0) if entry.qcovs is not None
            else _fraction(merge_intervals(entry.q_spans), entry.qlen),
            subject_coverage=_fraction(merge_intervals(entry.s_spans), entry.slen),
            domain_start=best["qstart"],
            domain_end=best["qend"],
            domain_count=entry.hsps,
        ))
    return hits


# ---------------------------------------------------------------- HMMER tables
# Field counts before the free-text description column, from the HMMER 3.4
# user guide ("tabular output formats").
TBLOUT_FIXED = 18
DOMTBLOUT_FIXED = 22


def _split_table(text: str, fixed: int) -> list[list[str]]:
    rows = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if not line.strip() or line.startswith("#"):
            continue
        parts = line.split(maxsplit=fixed)
        if len(parts) < fixed:
            raise ParseError(
                f"HMMER table line {lineno} has {len(parts)} fields, "
                f"expected at least {fixed}"
            )
        rows.append(parts)
    return rows


@dataclass
class _DomainAgg:
    query_spans: list[tuple[int, int]] = field(default_factory=list)
    target_spans: list[tuple[int, int]] = field(default_factory=list)
    qlen: int = 0
    tlen: int = 0
    count: int = 0
    best_domain: dict | None = None


def _parse_domtblout(text: str, *, query_is_hmm: bool) -> dict[tuple[str, str], _DomainAgg]:
    """Aggregate a domain table keyed by (query name, target name).

    `query_is_hmm` is True for phmmer (the submitted sequence became the
    profile) and False for hmmscan (the profile is the target). It decides
    which coordinate pair belongs to the submitted sequence.
    """
    agg: dict[tuple[str, str], _DomainAgg] = {}
    for f in _split_table(text, DOMTBLOUT_FIXED):
        try:
            target, tlen = f[0], int(f[2])
            query, qlen = f[3], int(f[5])
            hmm_from, hmm_to = int(f[15]), int(f[16])
            env_from, env_to = int(f[19]), int(f[20])
            ali_from, ali_to = int(f[17]), int(f[18])
            i_evalue, dom_score = float(f[12]), float(f[13])
        except (ValueError, IndexError) as exc:
            raise ParseError(f"malformed HMMER domain row: {exc}") from exc

        entry = agg.setdefault((query, target), _DomainAgg())
        entry.qlen, entry.tlen, entry.count = qlen, tlen, entry.count + 1
        if query_is_hmm:
            # phmmer: hmm coordinates are on the submitted sequence.
            entry.query_spans.append((hmm_from, hmm_to))
            entry.target_spans.append((env_from, env_to))
            q_start, q_end = hmm_from, hmm_to
        else:
            # hmmscan: envelope coordinates are on the submitted sequence,
            # hmm coordinates index the profile's match states.
            entry.query_spans.append((env_from, env_to))
            entry.target_spans.append((hmm_from, hmm_to))
            q_start, q_end = ali_from, ali_to
        if entry.best_domain is None or dom_score > entry.best_domain["score"]:
            entry.best_domain = {
                "score": dom_score, "ievalue": i_evalue,
                "start": q_start, "end": q_end,
            }
    return agg


def _parse_hmmer(text_tbl: str, text_dom: str, *, query_is_hmm: bool) -> list[RawHit]:
    """Build hits from a per-sequence table plus its domain table.

    For phmmer the tblout row is (target=reference, query=submitted); for
    hmmscan it is (target=profile, query=submitted). In both cases column 3
    is the query, so the orientation of this function does not change — only
    the coordinate mapping in `_parse_domtblout` does.
    """
    domains = _parse_domtblout(text_dom, query_is_hmm=query_is_hmm)

    hits: list[RawHit] = []
    for f in _split_table(text_tbl, TBLOUT_FIXED):
        try:
            target, query = f[0], f[2]
            evalue, bitscore = float(f[4]), float(f[5])
        except (ValueError, IndexError) as exc:
            raise ParseError(f"malformed HMMER sequence row: {exc}") from exc

        d = domains.get((query, target))
        best = d.best_domain if d else None
        hits.append(RawHit(
            query_id=query,
            hit_id=target,
            evalue=evalue,
            bitscore=bitscore,
            # HMMER's tabular output has no identity column, and inventing one
            # from the alignment would not be the same quantity BLAST reports.
            percent_identity=None,
            alignment_length=None,
            query_coverage=_fraction(merge_intervals(d.query_spans), d.qlen) if d else None,
            subject_coverage=_fraction(merge_intervals(d.target_spans), d.tlen) if d else None,
            domain_start=best["start"] if best else None,
            domain_end=best["end"] if best else None,
            domain_count=d.count if d else None,
            domain_evalue=best["ievalue"] if best else None,
            domain_bitscore=best["score"] if best else None,
        ))
    return hits


def parse_phmmer(text_tbl: str, text_dom: str) -> list[RawHit]:
    return _parse_hmmer(text_tbl, text_dom, query_is_hmm=True)


def parse_hmmscan(text_tbl: str, text_dom: str) -> list[RawHit]:
    return _parse_hmmer(text_tbl, text_dom, query_is_hmm=False)


def rank_hits(hits: list[RawHit], limit: int) -> dict[str, list[RawHit]]:
    """Group by query and rank by E-value, then descending bit score.

    Ranking is ours, not the tool's: BLAST's `-max_target_seqs` is a
    search-time cutoff rather than a "best N" filter (Shah et al. 2019,
    Bioinformatics 35:1786), so truncating here is the only way to get a
    defensible top-N list.
    """
    by_query: dict[str, list[RawHit]] = {}
    for h in hits:
        by_query.setdefault(h.query_id, []).append(h)
    for q in by_query:
        by_query[q].sort(key=lambda h: (h.evalue, -h.bitscore, h.hit_id))
        del by_query[q][limit:]
    return by_query
