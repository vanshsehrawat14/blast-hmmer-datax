"""Per-request search parameters.

The lab's instruction was to run neutral defaults but let a user change them.
Both halves matter here: `SearchParams.defaults()` reproduces the configured
server behaviour exactly, and `from_form()` is the only path that lets a
submitted value replace one.

Every field on this object ends up on a command line, so nothing arrives from
a form without being coerced to its type, bounded, and — where the tool takes
a fixed vocabulary — checked against a whitelist. A rejected value raises
`ParamError` with a message meant for the person who typed it.

Two parameters are deliberately not bounded by us:

  * `gapopen`/`gapextend` are only accepted by blastp in combinations that
    depend on the chosen matrix. We bound them to sane integers and let BLAST
    be the authority on the pairing, because a table copied into this file
    would be one more thing to keep in sync with NCBI.
  * `min_query_coverage` filters our own output rather than the search, so it
    cannot change what the tool reports — only what we show.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Mapping

from app.config import Settings

# blastp's substitution matrices, restricted to the ones BLAST+ ships with
# scoring parameters for. PAM30 and PAM70 are for short queries; BLOSUM45 and
# PAM250 for distant homologs; BLOSUM62 is the default and suits most work.
MATRICES: tuple[str, ...] = (
    "BLOSUM62", "BLOSUM45", "BLOSUM50", "BLOSUM80", "BLOSUM90",
    "PAM30", "PAM70", "PAM250",
)

# -comp_based_stats: 0 none, 1 Schaffer 2001, 2 Yu & Altschul 2005 conditioned
# on sequence properties (blastp's default), 3 unconditional.
COMP_BASED_STATS: tuple[str, ...] = ("0", "1", "2", "3")

COMP_BASED_STATS_LABELS: dict[str, str] = {
    "0": "0 — off",
    "1": "1 — composition-based statistics",
    "2": "2 — conditional compositional adjustment (default)",
    "3": "3 — unconditional compositional adjustment",
}

# blastp itself defaults -evalue to 10; going above that reports noise, and a
# non-positive threshold is meaningless.
EVALUE_MAX = 10.0
MAX_HITS_LIMIT = 100
GAPOPEN_RANGE = (5, 25)
GAPEXTEND_RANGE = (1, 5)


class ParamError(ValueError):
    """A submitted parameter this server will not put on a command line."""


@dataclass(frozen=True)
class SearchParams:
    """Resolved parameters for one submission.

    Resolved, not optional: every field carries the value that will be used,
    so the runners never have to fall back to settings and the result can
    record exactly what produced it. `gapopen`/`gapextend` stay None when the
    user did not set them, which means the flags are omitted and blastp uses
    the chosen matrix's own defaults.
    """

    blast_evalue: float
    phmmer_evalue: float
    hmmscan_evalue: float
    max_hits: int
    matrix: str = "BLOSUM62"
    gapopen: int | None = None
    gapextend: int | None = None
    comp_based_stats: str = "2"
    min_query_coverage: float | None = None

    @classmethod
    def defaults(cls, settings: Settings) -> "SearchParams":
        return cls(
            blast_evalue=settings.blast_evalue,
            phmmer_evalue=settings.phmmer_evalue,
            hmmscan_evalue=settings.hmmscan_evalue,
            max_hits=settings.max_hits_per_query,
        )

    @classmethod
    def from_form(cls, form: Mapping[str, str], settings: Settings) -> "SearchParams":
        """Build from submitted strings. Blank or absent means "keep default"."""
        params = cls.defaults(settings)

        evalue = _float(form, "evalue", "E-value threshold")
        if evalue is not None:
            if not 0 < evalue <= EVALUE_MAX:
                raise ParamError(
                    f"The E-value threshold must be greater than 0 and at most "
                    f"{EVALUE_MAX:g}."
                )
            # One threshold for whichever methods run: a user asking for 1e-5
            # means it for the search, not for blastp alone.
            params = replace(params, blast_evalue=evalue, phmmer_evalue=evalue,
                             hmmscan_evalue=evalue)

        max_hits = _int(form, "max_hits", "Hits per query")
        if max_hits is not None:
            if not 1 <= max_hits <= MAX_HITS_LIMIT:
                raise ParamError(
                    f"Hits per query must be between 1 and {MAX_HITS_LIMIT}."
                )
            params = replace(params, max_hits=max_hits)

        matrix = _text(form, "matrix")
        if matrix is not None:
            if matrix.upper() not in MATRICES:
                raise ParamError(
                    "Unsupported scoring matrix. Choose one of: "
                    + ", ".join(MATRICES) + "."
                )
            params = replace(params, matrix=matrix.upper())

        cbs = _text(form, "comp_based_stats")
        if cbs is not None:
            if cbs not in COMP_BASED_STATS:
                raise ParamError(
                    "Composition-based statistics must be 0, 1, 2 or 3."
                )
            params = replace(params, comp_based_stats=cbs)

        gapopen = _int(form, "gapopen", "Gap open cost")
        gapextend = _int(form, "gapextend", "Gap extend cost")
        if (gapopen is None) != (gapextend is None):
            raise ParamError(
                "Set both the gap open and gap extend costs, or neither. "
                "Leaving both blank uses the matrix's own defaults."
            )
        if gapopen is not None:
            if not GAPOPEN_RANGE[0] <= gapopen <= GAPOPEN_RANGE[1]:
                raise ParamError(
                    f"Gap open cost must be between {GAPOPEN_RANGE[0]} and "
                    f"{GAPOPEN_RANGE[1]}."
                )
            if not GAPEXTEND_RANGE[0] <= gapextend <= GAPEXTEND_RANGE[1]:
                raise ParamError(
                    f"Gap extend cost must be between {GAPEXTEND_RANGE[0]} and "
                    f"{GAPEXTEND_RANGE[1]}."
                )
            params = replace(params, gapopen=gapopen, gapextend=gapextend)

        coverage = _float(form, "min_query_coverage", "Minimum query coverage")
        if coverage is not None:
            if not 0 <= coverage <= 100:
                raise ParamError(
                    "Minimum query coverage must be between 0 and 100 percent."
                )
            # 0 means "do not filter", which is the same as leaving it unset.
            params = replace(params,
                             min_query_coverage=(coverage / 100) or None)

        return params

    def as_dict(self) -> dict:
        """What gets recorded on the result, so a hit list can be reproduced."""
        return {
            "evalue": self.blast_evalue,
            "max_hits": self.max_hits,
            "matrix": self.matrix,
            "gapopen": self.gapopen,
            "gapextend": self.gapextend,
            "comp_based_stats": self.comp_based_stats,
            "min_query_coverage": self.min_query_coverage,
        }


def _text(form: Mapping[str, str], key: str) -> str | None:
    value = form.get(key)
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _int(form: Mapping[str, str], key: str, label: str) -> int | None:
    value = _text(form, key)
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        raise ParamError(f"{label} must be a whole number.") from None


def _float(form: Mapping[str, str], key: str, label: str) -> float | None:
    value = _text(form, key)
    if value is None:
        return None
    try:
        parsed = float(value)
    except ValueError:
        raise ParamError(f"{label} must be a number.") from None
    # float() accepts "nan" and "inf"; neither belongs on a command line.
    if parsed != parsed or parsed in (float("inf"), float("-inf")):
        raise ParamError(f"{label} must be a finite number.")
    return parsed
