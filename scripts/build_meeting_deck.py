#!/usr/bin/env python3
"""Build the short BLAST/HMMER EnzymeX meeting deck."""

from __future__ import annotations

import argparse
import io
import zipfile
from datetime import datetime
from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, MSO_AUTO_SIZE, PP_ALIGN
from pptx.util import Inches, Pt


SLIDE_W = 13.333
SLIDE_H = 7.5

FONT = "Malgun Gothic"
MONO = "Consolas"

WHITE = "FFFFFF"
INK = "18212B"
CHARCOAL = "52565B"
MUTED = "667085"
LINE = "D9DEE7"
PANEL = "F5F7FA"
RED = "B71A1A"
RED_LIGHT = "FBECEC"
BLUE = "176B87"
BLUE_LIGHT = "EAF4F7"
GREEN = "167C5A"
GREEN_LIGHT = "EAF6F1"
PURPLE = "6554A4"
PURPLE_LIGHT = "F1EEFA"
AMBER = "A76500"
AMBER_LIGHT = "FFF5DF"


def rgb(hex_color: str) -> RGBColor:
    return RGBColor.from_string(hex_color)


def add_text(
    slide,
    text: str,
    x: float,
    y: float,
    w: float,
    h: float,
    *,
    size: float = 18,
    color: str = INK,
    bold: bool = False,
    font: str = FONT,
    align: PP_ALIGN = PP_ALIGN.LEFT,
    valign: MSO_ANCHOR = MSO_ANCHOR.TOP,
    margin: float = 0.0,
    line_spacing: float | None = None,
):
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = box.text_frame
    tf.clear()
    tf.word_wrap = True
    tf.auto_size = MSO_AUTO_SIZE.NONE
    tf.margin_left = tf.margin_right = Inches(margin)
    tf.margin_top = tf.margin_bottom = Inches(margin)
    tf.vertical_anchor = valign
    p = tf.paragraphs[0]
    p.text = text
    p.alignment = align
    if line_spacing is not None:
        p.line_spacing = line_spacing
    for run in p.runs:
        run.font.name = font
        run.font.size = Pt(size)
        run.font.bold = bold
        run.font.color.rgb = rgb(color)
    return box


def add_bullets(
    slide,
    items: list[str],
    x: float,
    y: float,
    w: float,
    h: float,
    *,
    size: float = 16,
    color: str = INK,
    spacing: float = 7,
):
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = box.text_frame
    tf.clear()
    tf.word_wrap = True
    tf.auto_size = MSO_AUTO_SIZE.NONE
    tf.margin_left = tf.margin_right = Inches(0.02)
    tf.margin_top = tf.margin_bottom = Inches(0.02)
    for index, item in enumerate(items):
        p = tf.paragraphs[0] if index == 0 else tf.add_paragraph()
        p.text = f"•  {item}"
        p.space_after = Pt(spacing)
        p.line_spacing = 1.05
        for run in p.runs:
            run.font.name = FONT
            run.font.size = Pt(size)
            run.font.color.rgb = rgb(color)
    return box


def add_rect(
    slide,
    x: float,
    y: float,
    w: float,
    h: float,
    *,
    fill: str = WHITE,
    line: str = LINE,
    radius: bool = True,
    line_width: float = 1,
):
    kind = MSO_SHAPE.ROUNDED_RECTANGLE if radius else MSO_SHAPE.RECTANGLE
    shape = slide.shapes.add_shape(kind, Inches(x), Inches(y), Inches(w), Inches(h))
    shape.fill.solid()
    shape.fill.fore_color.rgb = rgb(fill)
    shape.line.color.rgb = rgb(line)
    shape.line.width = Pt(line_width)
    return shape


def add_line(
    slide,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    *,
    color: str = LINE,
    width: float = 1.5,
):
    shape = slide.shapes.add_connector(
        MSO_CONNECTOR.STRAIGHT,
        Inches(x1),
        Inches(y1),
        Inches(x2),
        Inches(y2),
    )
    shape.line.color.rgb = rgb(color)
    shape.line.width = Pt(width)
    return shape


def add_header(slide, title: str, kicker: str, slide_number: int) -> None:
    add_rect(slide, 0, 0, SLIDE_W, 0.08, fill=RED, line=RED, radius=False, line_width=0)
    add_text(slide, kicker.upper(), 0.72, 0.28, 4.5, 0.28, size=10.5, color=RED, bold=True)
    add_text(slide, title, 0.7, 0.62, 11.9, 0.62, size=28, bold=True)
    add_text(
        slide,
        str(slide_number),
        12.25,
        0.34,
        0.35,
        0.25,
        size=10,
        color=MUTED,
        align=PP_ALIGN.RIGHT,
    )


def add_footer(slide) -> None:
    add_line(slide, 0.72, 7.15, 12.62, 7.15, color=LINE, width=0.8)
    add_text(
        slide,
        "Standalone validation. Nothing has been deployed to the official EnzymeX service.",
        0.72,
        7.2,
        11.9,
        0.18,
        size=8.5,
        color=MUTED,
        align=PP_ALIGN.RIGHT,
    )


def set_notes(slide, notes: str) -> None:
    slide.notes_slide.notes_text_frame.text = notes


def add_chip(
    slide,
    text: str,
    x: float,
    y: float,
    w: float,
    *,
    fill: str,
    color: str,
    border: str | None = None,
):
    add_rect(slide, x, y, w, 0.34, fill=fill, line=border or fill, radius=True, line_width=0.7)
    add_text(
        slide,
        text,
        x + 0.04,
        y + 0.01,
        w - 0.08,
        0.28,
        size=10.5,
        color=color,
        bold=True,
        align=PP_ALIGN.CENTER,
        valign=MSO_ANCHOR.MIDDLE,
    )


def add_metric_card(
    slide,
    value: str,
    label: str,
    x: float,
    y: float,
    w: float,
    *,
    accent: str = RED,
    note: str | None = None,
):
    add_rect(slide, x, y, w, 1.14, fill=WHITE, line=LINE)
    add_rect(slide, x, y, 0.09, 1.14, fill=accent, line=accent, radius=False, line_width=0)
    add_text(slide, value, x + 0.23, y + 0.14, w - 0.35, 0.42, size=24, color=accent, bold=True)
    add_text(slide, label, x + 0.23, y + 0.58, w - 0.35, 0.28, size=11.5, color=INK, bold=True)
    if note:
        add_text(slide, note, x + 0.23, y + 0.87, w - 0.35, 0.18, size=8.5, color=MUTED)


def add_method_card(
    slide,
    title: str,
    subtitle: str,
    lines: list[str],
    chips: list[str],
    x: float,
    accent: str,
    light: str,
    title_size: float = 21,
):
    add_rect(slide, x, 1.55, 3.85, 4.86, fill=WHITE, line=LINE)
    add_rect(slide, x, 1.55, 3.85, 0.12, fill=accent, line=accent, radius=False, line_width=0)
    add_text(slide, title, x + 0.25, 1.86, 3.35, 0.38, size=title_size, color=accent, bold=True)
    add_text(slide, subtitle, x + 0.25, 2.25, 3.35, 0.5, size=11.5, color=MUTED)
    y = 2.91
    for line in lines:
        add_text(slide, line, x + 0.25, y, 3.35, 0.34, size=12.5, color=INK, font=MONO if line.startswith("-") else FONT)
        y += 0.43
    chip_x = x + 0.25
    for chip in chips:
        chip_w = max(0.77, len(chip) * 0.077 + 0.28)
        if chip_x + chip_w > x + 3.62:
            chip_x = x + 0.25
            y += 0.43
        add_chip(slide, chip, chip_x, 5.72 if y < 5.72 else y, chip_w, fill=light, color=accent)
        chip_x += chip_w + 0.1


def add_table(
    slide,
    data: list[list[str]],
    x: float,
    y: float,
    w: float,
    h: float,
    col_widths: list[float],
    *,
    header_fill: str = CHARCOAL,
    font_size: float = 11.5,
):
    shape = slide.shapes.add_table(
        len(data), len(data[0]), Inches(x), Inches(y), Inches(w), Inches(h)
    )
    table = shape.table
    for index, width in enumerate(col_widths):
        table.columns[index].width = Inches(width)
    for row_index, row in enumerate(data):
        for col_index, value in enumerate(row):
            cell = table.cell(row_index, col_index)
            cell.text = value
            cell.margin_left = cell.margin_right = Inches(0.08)
            cell.margin_top = cell.margin_bottom = Inches(0.04)
            cell.vertical_anchor = MSO_ANCHOR.MIDDLE
            cell.text_frame.auto_size = MSO_AUTO_SIZE.NONE
            cell.fill.solid()
            cell.fill.fore_color.rgb = rgb(header_fill if row_index == 0 else (WHITE if row_index % 2 else PANEL))
            cell.border = None
            for p in cell.text_frame.paragraphs:
                p.alignment = PP_ALIGN.LEFT
                for run in p.runs:
                    run.font.name = FONT
                    run.font.size = Pt(font_size if row_index else font_size - 0.5)
                    run.font.bold = row_index == 0
                    run.font.color.rgb = rgb(WHITE if row_index == 0 else INK)
    return table


def logo_blob(path: Path | None) -> bytes | None:
    if path is None or not path.exists():
        return None
    with zipfile.ZipFile(path) as archive:
        preferred = "ppt/media/image1.png"
        if preferred in archive.namelist():
            return archive.read(preferred)
    return None


def build_deck(output: Path, logo_deck: Path | None) -> None:
    prs = Presentation()
    prs.slide_width = Inches(SLIDE_W)
    prs.slide_height = Inches(SLIDE_H)
    prs.core_properties.title = "BLAST and HMMER evidence for EnzymeX"
    prs.core_properties.subject = "Swiss-Prot and PDB standalone validation"
    prs.core_properties.author = "Vansh Sehrawat"
    prs.core_properties.last_modified_by = "Vansh Sehrawat"
    prs.core_properties.keywords = "EnzymeX, BLAST, HMMER, Swiss-Prot, PDB"
    prs.core_properties.comments = ""
    prs.core_properties.created = datetime(2026, 7, 31, 12, 0, 0)
    prs.core_properties.modified = datetime(2026, 7, 31, 12, 0, 0)
    blank = prs.slide_layouts[6]
    logo = logo_blob(logo_deck)

    # Slide 1
    slide = prs.slides.add_slide(blank)
    slide.background.fill.solid()
    slide.background.fill.fore_color.rgb = rgb(WHITE)
    add_rect(slide, 0, 0, 0.18, SLIDE_H, fill=RED, line=RED, radius=False, line_width=0)
    if logo:
        slide.shapes.add_picture(io.BytesIO(logo), Inches(1.0), Inches(0.72), width=Inches(4.45))
    else:
        add_text(slide, "EnzymeX", 1.0, 0.78, 4.3, 0.8, size=38, bold=True)
    add_chip(slide, "STANDALONE VALIDATION", 9.92, 0.8, 2.35, fill=RED_LIGHT, color=RED)
    add_text(slide, "BLAST + HMMER evidence\nfor EnzymeX", 1.0, 2.25, 10.9, 1.45, size=38, bold=True, valign=MSO_ANCHOR.MIDDLE)
    add_text(
        slide,
        "Swiss-Prot + PDB source-policy fixture build, search output, and external fold validation",
        1.03,
        3.92,
        10.8,
        0.62,
        size=18,
        color=CHARCOAL,
    )
    add_chip(slide, "blastp", 1.03, 4.9, 1.12, fill=BLUE_LIGHT, color=BLUE)
    add_chip(slide, "phmmer", 2.3, 4.9, 1.22, fill=GREEN_LIGHT, color=GREEN)
    add_chip(slide, "profile HMMs", 3.68, 4.9, 1.73, fill=PURPLE_LIGHT, color=PURPLE)
    add_text(slide, "Vansh Sehrawat  |  EnzymeX meeting  |  August 2026", 1.03, 6.53, 8.5, 0.28, size=12, color=MUTED)
    add_text(slide, "Build 32abd580b689", 9.8, 6.53, 2.47, 0.28, size=11, color=MUTED, align=PP_ALIGN.RIGHT, font=MONO)
    set_notes(
        slide,
        "This is an independent prototype. Nothing has been deployed to or changed in the official EnzymeX codebase. "
        "The goal was to validate the reference-building, search, metadata, and output flow before production integration.",
    )

    # Slide 2
    slide = prs.slides.add_slide(blank)
    add_header(slide, "The search layer is working end to end", "What was built", 2)
    add_rect(slide, 0.72, 1.42, 11.9, 0.58, fill=RED_LIGHT, line=RED_LIGHT)
    add_text(
        slide,
        "Current boundary: a standalone test server. The official EnzymeX codebase and live service are untouched.",
        0.95,
        1.56,
        11.4,
        0.24,
        size=12.5,
        color=RED,
        bold=True,
        align=PP_ALIGN.CENTER,
    )
    flow = [
        ("1", "Copied DB", "read-only"),
        ("2", "Offline build", "FASTA + indexes"),
        ("3", "FASTA input", "web submission"),
        ("4", "3 searches", "BLAST + HMMER"),
        ("5", "Results", "HTML + CSV + JSON"),
    ]
    for i, (number, title, subtitle) in enumerate(flow):
        x = 0.72 + i * 2.42
        add_rect(slide, x, 2.35, 1.94, 1.22, fill=WHITE, line=LINE)
        circle = slide.shapes.add_shape(MSO_SHAPE.OVAL, Inches(x + 0.16), Inches(2.59), Inches(0.5), Inches(0.5))
        circle.fill.solid()
        circle.fill.fore_color.rgb = rgb(RED if i in (0, 4) else CHARCOAL)
        circle.line.color.rgb = rgb(RED if i in (0, 4) else CHARCOAL)
        add_text(slide, number, x + 0.17, 2.67, 0.48, 0.22, size=12, color=WHITE, bold=True, align=PP_ALIGN.CENTER)
        add_text(slide, title, x + 0.72, 2.5, 1.12, 0.3, size=11.5, bold=True)
        add_text(slide, subtitle, x + 0.72, 2.86, 1.12, 0.38, size=9.2, color=MUTED)
        if i < len(flow) - 1:
            arrow = slide.shapes.add_shape(MSO_SHAPE.RIGHT_ARROW, Inches(x + 2.01), Inches(2.78), Inches(0.32), Inches(0.34))
            arrow.fill.solid()
            arrow.fill.fore_color.rgb = rgb(LINE)
            arrow.line.color.rgb = rgb(LINE)
    cards = [
        ("Reference builder", "QC, source filtering, exact-sequence deduplication, metadata, and a versioned manifest."),
        ("Search service", "blastp, phmmer, and hmmscan run through one framework-independent service boundary."),
        ("Result interface", "Separate evidence tables, explicit method status, and downloadable CSV and JSON output."),
    ]
    for i, (title, body) in enumerate(cards):
        x = 0.72 + i * 4.02
        add_rect(slide, x, 4.15, 3.8, 2.36, fill=PANEL, line=LINE)
        add_text(slide, title, x + 0.25, 4.42, 3.25, 0.36, size=17, bold=True, color=RED if i == 0 else INK)
        add_text(slide, body, x + 0.25, 4.96, 3.27, 1.08, size=13, color=CHARCOAL, line_spacing=1.05)
    add_footer(slide)
    set_notes(
        slide,
        "The search core is independent of the FastAPI test interface. EnzymeX can call the same run_search service from its existing Pyramid scheduled-job pipeline. "
        "A user search reads only the built artifacts, so there is no database connection at request time.",
    )

    # Slide 3
    slide = prs.slides.add_slide(blank)
    add_header(slide, "One selected-source fixture export feeds every method", "Reference build", 3)
    stages = [
        ("2,677", "copied rows", CHARCOAL),
        ("1,694", "valid selected rows", BLUE),
        ("120", "exact duplicates", AMBER),
        ("1,574", "canonical refs", RED),
    ]
    for i, (value, label, accent) in enumerate(stages):
        x = 0.72 + i * 2.1
        add_rect(slide, x, 1.52, 1.72, 1.18, fill=WHITE, line=LINE)
        add_text(slide, value, x + 0.12, 1.7, 1.48, 0.42, size=23, color=accent, bold=True, align=PP_ALIGN.CENTER)
        add_text(slide, label, x + 0.12, 2.18, 1.48, 0.26, size=10.5, color=MUTED, bold=True, align=PP_ALIGN.CENTER)
        if i < 3:
            arrow = slide.shapes.add_shape(MSO_SHAPE.RIGHT_ARROW, Inches(x + 1.78), Inches(1.94), Inches(0.25), Inches(0.32))
            arrow.fill.solid()
            arrow.fill.fore_color.rgb = rgb(LINE)
            arrow.line.color.rgb = rgb(LINE)
    add_text(slide, "-930 unselected  •  -53 QC failures", 1.97, 2.82, 3.9, 0.28, size=10.5, color=MUTED, align=PP_ALIGN.CENTER)
    add_text(slide, "merged, not discarded from provenance", 6.78, 2.82, 2.0, 0.36, size=9.5, color=AMBER, align=PP_ALIGN.CENTER)

    add_text(slide, "Canonical fixture source labels", 0.72, 3.35, 4.0, 0.32, size=16, bold=True)
    bar_x, bar_y, bar_w = 0.72, 3.85, 7.98
    swiss_w = bar_w * 1387 / 1574
    add_rect(slide, bar_x, bar_y, swiss_w, 0.54, fill=BLUE, line=BLUE, radius=False, line_width=0)
    add_rect(slide, bar_x + swiss_w, bar_y, bar_w - swiss_w, 0.54, fill=RED, line=RED, radius=False, line_width=0)
    add_text(slide, "Swiss-Prot  1,387  (88.1%)", bar_x + 0.15, bar_y + 0.1, swiss_w - 0.3, 0.24, size=11.5, color=WHITE, bold=True)
    add_text(slide, "PDB 187", bar_x + swiss_w + 0.05, bar_y + 0.1, bar_w - swiss_w - 0.1, 0.24, size=10.5, color=WHITE, bold=True, align=PP_ALIGN.CENTER)

    add_metric_card(slide, "47", "profile HMMs", 0.72, 4.85, 2.2, accent=PURPLE, note="201 clusters evaluated")
    add_metric_card(slide, "1,352", "references in profiles", 3.12, 4.85, 2.45, accent=PURPLE, note="85.9% profile coverage")
    add_metric_card(slide, "0.9994", "mean EC purity", 5.77, 4.85, 2.45, accent=PURPLE, note="12 consensus EC labels")

    add_rect(slide, 9.02, 1.52, 3.6, 5.45, fill=PANEL, line=LINE)
    add_text(slide, "Cross-source duplicates", 9.3, 1.82, 3.05, 0.56, size=16, bold=True, color=RED)
    add_bullets(
        slide,
        [
            "All three methods use the same selected export.",
            "Swiss-Prot is the canonical row for an exact cross-source duplicate.",
            "The duplicate PDB row stays traceable as provenance.",
            "Fields from separate rows are never merged.",
        ],
        9.3,
        2.56,
        3.0,
        2.7,
        size=13,
        spacing=8,
    )
    add_rect(slide, 9.3, 5.63, 3.05, 0.98, fill=AMBER_LIGHT, line=AMBER_LIGHT)
    add_text(
        slide,
        "Fixture only\nPDB labels are synthetic.\nReal PDB rows still need validation.",
        9.49,
        5.78,
        2.67,
        0.66,
        size=10.5,
        color=AMBER,
        bold=True,
    )
    add_footer(slide)
    set_notes(
        slide,
        "The accounting closes exactly: 2,677 rows minus 930 unselected sources, 53 QC failures, and 120 selected-source exact duplicates leaves 1,574 canonical references. "
        "Eight PDB-first duplicate groups were promoted to Swiss-Prot canonical metadata. The fixture's PDB labels are synthetic, so this proves the source-policy code but not genuine PDB ingestion.",
    )

    # Slide 4
    slide = prs.slides.add_slide(blank)
    add_header(slide, "Three searches, with method-specific evidence", "Procedure and parameters", 4)
    add_method_card(
        slide,
        "BLAST (blastp)",
        "Pairwise sequence similarity against the shared reference export.",
        [
            "BLAST+ 2.16.0",
            "-evalue 1e-3",
            "-comp_based_stats 2",
            "-max_target_seqs 500",
            "rerank locally -> top 25",
        ],
        ["identity", "coverage", "bit score"],
        0.72,
        BLUE,
        BLUE_LIGHT,
    )
    add_method_card(
        slide,
        "HMMER (phmmer)",
        "Query-derived profile against every sequence in the same FASTA.",
        [
            "HMMER 3.4",
            "-E 1e-3",
            "1,574 references",
            "whole-sequence + domain score",
            "rerank locally -> top 25",
        ],
        ["all references", "domains"],
        4.74,
        GREEN,
        GREEN_LIGHT,
    )
    add_method_card(
        slide,
        "Profile HMMs (hmmscan)",
        "Submitted sequence against family profiles built from the same export.",
        [
            "HMMER 3.4",
            "-E 1e-3",
            "47 profile HMMs",
            "build: 35% id / 80% bidir. cov.",
            "build: minimum 5 members",
        ],
        ["family", "EC purity", "domains"],
        8.76,
        PURPLE,
        PURPLE_LIGHT,
        19,
    )
    add_rect(slide, 0.72, 6.62, 11.9, 0.39, fill=PANEL, line=PANEL)
    add_text(
        slide,
        "No MySQL connection during a search  |  E-values are ranked within each method and are not compared across methods",
        0.93,
        6.7,
        11.45,
        0.2,
        size=10.5,
        color=CHARCOAL,
        bold=True,
        align=PP_ALIGN.CENTER,
    )
    add_footer(slide)
    set_notes(
        slide,
        "blastp and phmmer cover the whole exported reference set. hmmscan covers only families that pass profile quality control. "
        "Profiles are built from sequence clusters rather than EC numbers because one EC can include unrelated folds. The application defaults shown here differ from the exact BLAST reproduction, which used Beomsu's E-value of 1e-5.",
    )

    # Slide 5
    slide = prs.slides.add_slide(blank)
    add_header(slide, "Output is explicit, separate, and exportable", "Output format", 5)
    raw_cards = [
        (
            "BLAST raw output",
            "outfmt 6 TSV, 13 fields\nqseqid sseqid pident length qstart qend sstart send qlen slen evalue bitscore qcovs",
            BLUE,
            BLUE_LIGHT,
        ),
        (
            "HMMER raw output",
            "phmmer + hmmscan\n--tblout for sequence hits\n--domtblout for domains",
            GREEN,
            GREEN_LIGHT,
        ),
        (
            "Normalized application output",
            "rank + metadata + method status\nseparate HTML tables  |  CSV  |  JSON",
            RED,
            RED_LIGHT,
        ),
    ]
    for i, (title, body, accent, light) in enumerate(raw_cards):
        x = 0.72 + i * 4.05
        add_rect(slide, x, 1.43, 3.81, 1.2, fill=light, line=light)
        add_text(slide, title, x + 0.2, 1.62, 3.4, 0.25, size=12.5, color=accent, bold=True)
        add_text(slide, body, x + 0.2, 1.95, 3.4, 0.58, size=8.8 if i == 0 else 10.2, color=INK, font=MONO if i < 2 else FONT)
    add_text(slide, "Example top row from each separate method table", 0.72, 2.77, 6.0, 0.26, size=12, color=CHARCOAL, bold=True)
    add_text(slide, "Job 16564dd9e08fb902  |  Build 32abd580b689  |  0.925 s", 7.0, 2.77, 5.62, 0.26, size=9.8, color=MUTED, align=PP_ALIGN.RIGHT, font=MONO)
    output_rows = [
        ["Method", "Top hit", "Annotation", "Method-specific evidence", "Status"],
        ["blastp", "EXR2306", "1.15.1.1\nSwiss-Prot", "99.1% identity | 100% query coverage\nE 7.8e-169 | 459.0 bits", "ok"],
        ["phmmer", "EXR2306", "1.15.1.1\nSwiss-Prot", "100% query coverage\nE 5.2e-157 | 517.2 bits", "ok"],
        ["hmmscan", "EXF00003\n90 members", "1.15.1.1\n100% EC purity", "89.6% query | 99.0% profile coverage\nE 1.3e-93 | 303.6 bits", "ok"],
    ]
    add_table(slide, output_rows, 0.72, 3.11, 11.9, 1.98, [1.35, 1.55, 1.65, 5.85, 1.5], font_size=11)
    add_rect(slide, 0.72, 5.21, 5.72, 1.55, fill=BLUE_LIGHT, line=BLUE_LIGHT)
    add_text(slide, "HTML tables", 0.98, 5.42, 2.1, 0.3, size=16, color=BLUE, bold=True)
    add_bullets(
        slide,
        [
            "One table per method, never side by side.",
            "Explicit ok, no_hits, disabled, and failed states.",
            "Percent identity appears only for BLAST.",
        ],
        0.98,
        5.83,
        5.18,
        0.82,
        size=11.5,
        spacing=2,
    )
    add_rect(slide, 6.69, 5.21, 5.93, 1.55, fill=GREEN_LIGHT, line=GREEN_LIGHT)
    add_text(slide, "CSV and JSON", 6.96, 5.42, 2.3, 0.3, size=16, color=GREEN, bold=True)
    add_text(
        slide,
        "Context\njob, query, method/version, build, status",
        6.97,
        5.85,
        2.44,
        0.68,
        size=10.5,
        color=INK,
        bold=True,
    )
    add_text(
        slide,
        "Evidence\nrank, reference/family, EC, source, scores, coverage, domains",
        9.5,
        5.85,
        2.82,
        0.75,
        size=10.5,
        color=INK,
        bold=True,
    )
    add_footer(slide)
    set_notes(
        slide,
        "BLAST writes a custom 13-column outfmt 6 TSV. phmmer and hmmscan each write tblout for sequence-level hits and domtblout for domain coordinates and scores. The parsers normalize those raw files and attach EC/source metadata from the matching reference build. "
        "The web header records the job ID, reference build ID, query count, total runtime, and export links. CSV is one row per hit, while JSON keeps the nested job, query, method, and hit structure. "
        "No-hit and failed methods still emit a status row. HMMER does not report the same percent identity quantity as BLAST, so that field is left empty rather than invented.",
    )

    # Slide 6
    slide = prs.slides.add_slide(blank)
    add_header(slide, "Validation reproduced the shared BLAST result", "Tests and comparison", 6)
    add_metric_card(slide, "183", "tests passed", 0.72, 1.48, 2.45, accent=GREEN, note="with copied-DB access")
    add_metric_card(slide, "0", "provenance mismatches", 3.38, 1.48, 2.72, accent=GREEN, note="references, FASTA, duplicates")
    add_metric_card(slide, "452 / 452", "same raw BLAST top hit", 6.31, 1.48, 3.0, accent=BLUE, note="2.16 vs supplied 2.5 output")
    add_metric_card(slide, "450 / 452", "same top-25 subject set", 9.52, 1.48, 3.1, accent=BLUE, note="99.56% of comparable hits")
    add_rect(slide, 0.72, 2.94, 11.9, 0.54, fill=BLUE_LIGHT, line=BLUE_LIGHT)
    add_text(
        slide,
        "Beomsu fold: 500 queries, 48 shared no-hits  |  BLAST 2.5: E 1e-5, comp stats 2, 100 targets, outfmt 6, 16 threads",
        0.92,
        3.08,
        11.5,
        0.22,
        size=10.5,
        color=BLUE,
        bold=True,
        align=PP_ALIGN.CENTER,
    )
    benchmark = [
        ["Method", "Evaluated cohort", "Top-hit EC overlap", "Local WSL2 run"],
        ["blastp", "349 reference-covered queries", "306 / 349  (87.68%)", "7.81 s"],
        ["phmmer", "349 reference-covered queries", "303 / 349  (86.82%)", "130.36 s"],
        ["blastp + phmmer", "279 profile-common queries", "279 / 279  (100.00%)", "same runs as above"],
        ["hmmscan", "279 profile-common queries", "272 / 279  (97.49%)", "42.58 s"],
    ]
    add_table(slide, benchmark, 0.72, 3.62, 11.9, 2.05, [1.8, 4.0, 3.55, 2.55], header_fill=CHARCOAL, font_size=12)
    add_rect(slide, 0.72, 5.8, 11.9, 1.12, fill=AMBER_LIGHT, line=AMBER_LIGHT)
    add_text(slide, "How to read this", 0.96, 5.99, 1.46, 0.28, size=12, color=AMBER, bold=True)
    add_text(
        slide,
        "Compare methods only inside one cohort. On the shared 279-query cohort blastp and phmmer both reach 100% and hmmscan 97.49%, so the wider 87.68% row is a harder cohort, not a weaker method. "
        "Only 349 of 19,567 fold queries were evaluated and all were truth-selected, so this is a close-homolog slice rather than broad EC-prediction accuracy. Timings are local.",
        2.42,
        5.94,
        9.88,
        0.66,
        size=10.5,
        color=AMBER,
        bold=True,
    )
    add_footer(slide)
    set_notes(
        slide,
        "The strongest cross-version result is exact raw top-hit agreement for every comparable query in the 500-query reproduction cohort. Exact top score also matched for 447 of 452. "
        "The smaller EC table is useful for checking behavior on labels that the development reference actually covers, but it must not be presented as overall EnzymeX accuracy. It supports BLAST as the default sequence evidence and profile HMMs as optional family evidence; it does not justify running slower phmmer for every production job. "
        "Read the last two rows together. hmmscan is not the most accurate method: on the identical 279-query cohort the sequence methods are perfect and hmmscan misses seven. The profile-common slice is the easiest part of the selected cohort, so its higher percentage reflects the cohort, not the method. "
        "Outside that slice BLAST produced hits for 35 of 70 queries and its top hit shared an EC for 27, which is where the 87.68% comes from.",
    )

    # Slide 7
    slide = prs.slides.add_slide(blank)
    add_header(slide, "Positive and unrelated sequences behave as expected", "Browser workflow", 7)
    add_rect(slide, 0.72, 1.48, 5.78, 5.36, fill=GREEN_LIGHT, line=GREEN_LIGHT)
    add_chip(slide, "POSITIVE CONTROL", 0.98, 1.75, 1.76, fill=WHITE, color=GREEN, border=GREEN)
    add_text(slide, "Human mitochondrial Mn-SOD", 0.98, 2.25, 5.08, 0.38, size=20, color=GREEN, bold=True)
    add_text(slide, "Held-out query  |  EC 1.15.1.1  |  222 aa", 0.98, 2.68, 5.08, 0.28, size=11.5, color=MUTED)
    add_bullets(
        slide,
        [
            "BLAST: 25 hits; top EXR2306, 99.1% identity, full coverage.",
            "phmmer: 25 hits; same top reference and same top four ordering.",
            "hmmscan: two Mn/Fe SOD family profiles, both 100% EC purity.",
            "No unrelated Cu/Zn SOD profile was returned.",
        ],
        0.98,
        3.2,
        5.05,
        1.9,
        size=12.5,
        spacing=6,
    )
    add_text(slide, "0.925 s total", 0.98, 5.45, 2.15, 0.4, size=21, color=GREEN, bold=True)
    add_text(slide, "blastp 0.222  |  phmmer 0.278  |  hmmscan 0.174", 0.98, 5.93, 4.9, 0.25, size=10.5, color=MUTED, font=MONO)
    add_rect(slide, 0.98, 6.28, 5.05, 0.36, fill=WHITE, line=WHITE)
    add_text(slide, "Strong, mutually consistent sequence-family evidence", 1.12, 6.33, 4.76, 0.26, size=10.5, color=GREEN, bold=True, align=PP_ALIGN.CENTER)

    add_rect(slide, 6.83, 1.48, 5.79, 5.36, fill=PANEL, line=LINE)
    add_chip(slide, "UNRELATED CONTROL", 7.09, 1.75, 1.88, fill=WHITE, color=CHARCOAL, border=LINE)
    add_text(slide, "Green fluorescent protein", 7.09, 2.25, 5.06, 0.38, size=20, color=CHARCOAL, bold=True)
    add_text(slide, "Unrelated non-enzyme query  |  238 aa", 7.09, 2.68, 5.08, 0.28, size=11.5, color=MUTED)
    no_hits = [("blastp", BLUE), ("phmmer", GREEN), ("hmmscan", PURPLE)]
    for i, (method, accent) in enumerate(no_hits):
        y = 3.26 + i * 0.74
        add_rect(slide, 7.09, y, 5.05, 0.53, fill=WHITE, line=LINE)
        add_text(slide, method, 7.32, y + 0.1, 1.2, 0.3, size=11.5, color=accent, bold=True, valign=MSO_ANCHOR.MIDDLE)
        add_text(slide, "no hits", 10.55, y + 0.1, 1.25, 0.3, size=11.5, color=CHARCOAL, bold=True, align=PP_ALIGN.RIGHT, valign=MSO_ANCHOR.MIDDLE)
    add_text(slide, "0.850 s total", 7.09, 5.72, 2.15, 0.4, size=21, color=CHARCOAL, bold=True)
    add_rect(slide, 7.09, 6.28, 5.05, 0.36, fill=WHITE, line=WHITE)
    add_text(slide, "No hit means no detectable relative in this reference set", 7.19, 6.33, 4.84, 0.26, size=10.3, color=CHARCOAL, bold=True, align=PP_ALIGN.CENTER)
    add_footer(slide)
    set_notes(
        slide,
        "The positive sequence is a human mitochondrial manganese superoxide dismutase. The methods agree on close relatives and the profile layer selects the correct Mn/Fe families without selecting unrelated Cu/Zn proteins that share the same EC. "
        "GFP returns no hits from all three methods. That is not a claim that a no-hit protein is not an enzyme; it only says this reference set has no detectable relative under the current thresholds.",
    )

    # Slide 8
    slide = prs.slides.add_slide(blank)
    add_header(slide, "Rebuilt on real Swiss-Prot and PDB data", "Real-data build", 8)
    real_cards = [
        ("272,112", "references built", "from 273,500 public rows", RED),
        ("4 m 29 s", "full offline build", "export 252 s + makeblastdb 13 s", CHARCOAL),
        ("1.8 s", "blastp, one query", "phmmer 7.1 s in the same job", BLUE),
        ("0", "changes to the search code", "a loader script only", GREEN),
    ]
    for i, (value, label, note, accent) in enumerate(real_cards):
        add_metric_card(slide, value, label, 0.72 + i * 3.015, 1.48, 2.855, accent=accent, note=note)
    add_text(
        slide,
        "Swiss-Prot 231,577  |  PDB 40,535  |  235,821 carry an EC  |  1,388 rows failed QC  |  build 56b491bee73d, profiles not built on this generation",
        0.72,
        2.78,
        11.9,
        0.26,
        size=11,
        color=MUTED,
        align=PP_ALIGN.CENTER,
    )

    add_rect(slide, 0.72, 3.15, 5.78, 3.05, fill=AMBER_LIGHT, line=AMBER_LIGHT)
    add_chip(slide, "WHAT REAL DATA EXPOSED", 0.98, 3.36, 2.32, fill=WHITE, color=AMBER, border=AMBER)
    add_text(slide, "GFP is no longer a clean negative", 0.98, 3.86, 5.26, 0.36, size=18, color=AMBER, bold=True)
    add_bullets(
        slide,
        [
            "On the fixture, GFP returned no hits from all three methods.",
            "On 272,112 real references: 25 blastp hits, top E 8.4e-175 at 97.5% identity.",
            "Every hit is a PDB GFP-fusion construct, GFP joined to an unrelated protein.",
            "Those 25 hits carry eight different EC annotations. None of them belong to GFP.",
        ],
        0.98,
        4.36,
        5.26,
        1.7,
        size=11.5,
        color=INK,
        spacing=5,
    )

    add_rect(slide, 6.83, 3.15, 5.79, 3.05, fill=BLUE_LIGHT, line=BLUE_LIGHT)
    add_chip(slide, "THE OUTPUT ALREADY FLAGS IT", 7.09, 3.36, 2.72, fill=WHITE, color=BLUE, border=BLUE)
    add_text(slide, "Full query, partial subject", 7.09, 3.86, 5.26, 0.36, size=18, color=BLUE, bold=True)
    add_bullets(
        slide,
        [
            "Top hit 6HR1: query coverage 1.00, subject coverage 0.55.",
            "All 25 hits: query 0.97 to 1.00, subject 0.27 to 0.84.",
            "That gap is the fusion signature, and it is visible without opening PDB.",
            "Subject coverage is computed from merged HSP intervals; BLAST+ has no scovs field.",
        ],
        7.09,
        4.36,
        5.26,
        1.7,
        size=11.5,
        color=INK,
        spacing=5,
    )
    add_rect(slide, 0.72, 6.35, 11.9, 0.55, fill=PANEL, line=LINE)
    add_text(
        slide,
        "Only the fixture generation has profile HMMs, so every hmmscan number in this deck comes from build 32abd580b689. Building profiles on the real generation is the next step.",
        0.94,
        6.5,
        11.46,
        0.26,
        size=11,
        color=CHARCOAL,
        bold=True,
        align=PP_ALIGN.CENTER,
    )
    add_footer(slide)
    set_notes(
        slide,
        "The public SwissProt_PDB_2022 set from the datax-lab HIT-EC repository is 273,500 rows, loaded into a separate database so the fixture build stays intact. "
        "It is a proxy for the real EnzymeX copy, not the copy itself, but it is genuine PDB provenance rather than synthetic labels. Nothing in the search code changed; the only new file is a loader script. "
        "GFP is the useful result. PDB is full of fusion constructs where GFP is joined to another protein for imaging, so a GFP query matches the GFP half at near-perfect identity and inherits the partner's EC number. "
        "The query covers the full alignment while the subject does not, and that asymmetry is the tell. This failure mode could not appear in the fixture, which contains no real PDB entries. "
        "Also note the positive control is now a self-match: P04179 is present in this set at 100% identity, so the held-out positive demo still needs the fixture build.",
    )

    # Slide 9
    slide = prs.slides.add_slide(blank)
    add_header(slide, "The remaining work starts with real EnzymeX access", "Production integration", 9)
    steps = [
        ("1", "Inspect the real copy", "Read-only DB and repository access; schema, stable key, engine, source counts, UniprotID, and PDB identifiers."),
        ("2", "Rebuild on genuine data", "Start with a capped export, then build a full generation from all eligible Swiss-Prot/PDB rows in the real copy."),
        ("3", "Connect the scheduled job", "Adapt run_search to Pyramid, job/job_result/ref_data, temporary directories, and the existing result page."),
        ("4", "Reconcile DIAMOND", "Define whether BLAST supplements or replaces the current similarity path and whether phmmer is optional."),
        ("5", "Deploy versioned artifacts", "Build into a new generation and atomically switch the active reference after validation."),
    ]
    for i, (number, title, body) in enumerate(steps):
        y = 1.48 + i * 1.06
        circle = slide.shapes.add_shape(MSO_SHAPE.OVAL, Inches(0.82), Inches(y + 0.12), Inches(0.52), Inches(0.52))
        circle.fill.solid()
        circle.fill.fore_color.rgb = rgb(RED if i == 0 else CHARCOAL)
        circle.line.color.rgb = rgb(RED if i == 0 else CHARCOAL)
        add_text(slide, number, 0.83, y + 0.2, 0.5, 0.22, size=12, color=WHITE, bold=True, align=PP_ALIGN.CENTER)
        if i < len(steps) - 1:
            add_line(slide, 1.08, y + 0.66, 1.08, y + 1.18, color=LINE, width=2)
        add_text(slide, title, 1.55, y + 0.01, 7.12, 0.28, size=14.5, bold=True, color=RED if i == 0 else INK)
        add_text(slide, body, 1.55, y + 0.38, 7.12, 0.43, size=10.5, color=CHARCOAL)

    add_rect(slide, 9.28, 1.48, 3.34, 4.92, fill=RED_LIGHT, line=RED_LIGHT)
    add_text(slide, "Meeting decisions", 9.58, 1.8, 2.78, 0.52, size=17, color=RED, bold=True)
    decisions = [
        "Read-only access to the DB copy and EnzymeX repo?",
        "BLAST beside DIAMOND, or replace its evidence role?",
        "Run phmmer by default, or keep it optional?",
    ]
    for i, decision in enumerate(decisions):
        y = 2.58 + i * 1.02
        add_rect(slide, 9.56, y, 2.78, 0.82, fill=WHITE, line=LINE)
        add_text(slide, str(i + 1), 9.73, y + 0.23, 0.32, 0.22, size=11, color=RED, bold=True, align=PP_ALIGN.CENTER)
        add_text(slide, decision, 10.16, y + 0.1, 1.96, 0.62, size=10.5, color=INK, bold=True, valign=MSO_ANCHOR.MIDDLE)
    add_rect(slide, 9.56, 5.76, 2.78, 0.38, fill=WHITE, line=WHITE)
    add_text(slide, "Blocked on access + schema", 9.7, 5.82, 2.5, 0.27, size=10.5, color=RED, bold=True, align=PP_ALIGN.CENTER)
    add_chip(slide, "FIXTURE: VALIDATED", 0.82, 6.72, 1.94, fill=GREEN_LIGHT, color=GREEN)
    add_chip(slide, "ENZYMEX: ACCESS NEEDED", 2.94, 6.72, 2.55, fill=AMBER_LIGHT, color=AMBER)
    add_footer(slide)
    set_notes(
        slide,
        "The remaining uncertainty is the actual EnzymeX schema and genuine PDB provenance, not the standalone search implementation. "
        "The first meeting outcome should be read-only access. The other decisions are product choices: how BLAST relates to DIAMOND and whether slower phmmer should run by default. Production deployment also needs versioned reference generations with an atomic active-generation switch.",
    )

    output.parent.mkdir(parents=True, exist_ok=True)
    prs.save(output)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="PPTX file to write")
    parser.add_argument(
        "--logo-deck",
        type=Path,
        help="Existing EnzymeX PPTX containing ppt/media/image1.png",
    )
    args = parser.parse_args()
    build_deck(args.output.resolve(), args.logo_deck.resolve() if args.logo_deck else None)
    print(args.output.resolve())


if __name__ == "__main__":
    main()
