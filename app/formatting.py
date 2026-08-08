"""Display formatting shared by both front ends.

The test server renders with FastAPI's Jinja environment and EnzymeX renders
with pyramid_jinja2. These are the rules that decide how a number reads, and
they belong in one place: a hit shown on the EnzymeX page and the same hit in
the CSV should not disagree about what "0.0" means.
"""

from __future__ import annotations


def evalue(value: float | None) -> str:
    """BLAST prints 0.0 when the E-value underflows; say so rather than
    rendering a zero that reads like a missing number."""
    if value is None:
        return "—"
    if value == 0:
        return "<1e-180"
    return f"{value:.1e}" if value < 0.01 else f"{value:.3g}"


def fraction(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.0f}%"


def percent1(value: float | None) -> str:
    return "—" if value is None else f"{value:.1f}%"


def number(value, digits: int = 1) -> str:
    return "—" if value is None else f"{round(value, digits):g}"
