#!/usr/bin/env python3
"""Build the BLAST/HMMER EnzymeX meeting deck."""

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
    height: float = 4.86,
):
    add_rect(slide, x, 1.55, 3.85, height, fill=WHITE, line=LINE)
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
        chip_y = 1.55 + height - 0.69
        add_chip(slide, chip, chip_x, chip_y if y < chip_y else y, chip_w, fill=light, color=accent)
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


def add_step_row(slide, number: str, title: str, body: str, y: float, accent: str, last: bool):
    circle = slide.shapes.add_shape(MSO_SHAPE.OVAL, Inches(0.82), Inches(y + 0.1), Inches(0.5), Inches(0.5))
    circle.fill.solid()
    circle.fill.fore_color.rgb = rgb(accent)
    circle.line.color.rgb = rgb(accent)
    add_text(slide, number, 0.83, y + 0.18, 0.48, 0.22, size=12, color=WHITE, bold=True, align=PP_ALIGN.CENTER)
    if not last:
        add_line(slide, 1.07, y + 0.62, 1.07, y + 1.14, color=LINE, width=2)
    add_text(slide, title, 1.55, y, 6.9, 0.3, size=15, bold=True, color=accent)
    add_text(slide, body, 1.55, y + 0.36, 6.9, 0.45, size=11.5, color=CHARCOAL)


def build_deck(output: Path, logo_deck: Path | None) -> None:
    prs = Presentation()
    prs.slide_width = Inches(SLIDE_W)
    prs.slide_height = Inches(SLIDE_H)
    prs.core_properties.title = "BLAST and HMMER for EnzymeX"
    prs.core_properties.subject = "Sequence-similarity evidence on the result page"
    prs.core_properties.author = "Vansh Sehrawat"
    prs.core_properties.last_modified_by = "Vansh Sehrawat"
    prs.core_properties.keywords = "EnzymeX, BLAST, HMMER, Swiss-Prot, PDB"
    prs.core_properties.comments = ""
    prs.core_properties.created = datetime(2026, 8, 2, 12, 0, 0)
    prs.core_properties.modified = datetime(2026, 8, 2, 12, 0, 0)
    blank = prs.slide_layouts[6]
    logo = logo_blob(logo_deck)

    # ---------------------------------------------------------------- 1 title
    slide = prs.slides.add_slide(blank)
    slide.background.fill.solid()
    slide.background.fill.fore_color.rgb = rgb(WHITE)
    add_rect(slide, 0, 0, 0.18, SLIDE_H, fill=RED, line=RED, radius=False, line_width=0)
    if logo:
        slide.shapes.add_picture(io.BytesIO(logo), Inches(1.0), Inches(0.75), width=Inches(4.45))
    else:
        add_text(slide, "EnzymeX", 1.0, 0.8, 4.3, 0.8, size=38, bold=True)
    add_chip(slide, "TEST SERVER, NOT DEPLOYED", 9.42, 0.84, 2.85, fill=RED_LIGHT, color=RED)
    add_text(
        slide,
        "BLAST + HMMER for EnzymeX",
        1.0,
        2.4,
        10.9,
        1.0,
        size=42,
        bold=True,
        valign=MSO_ANCHOR.MIDDLE,
    )
    add_text(
        slide,
        "Sequence-similarity evidence beside the model predictions",
        1.03,
        3.55,
        10.8,
        0.5,
        size=19,
        color=CHARCOAL,
    )
    add_chip(slide, "blastp", 1.03, 4.5, 1.12, fill=BLUE_LIGHT, color=BLUE)
    add_chip(slide, "phmmer", 2.3, 4.5, 1.22, fill=GREEN_LIGHT, color=GREEN)
    add_chip(slide, "profile HMMs", 3.68, 4.5, 1.73, fill=PURPLE_LIGHT, color=PURPLE)
    add_text(slide, "Vansh Sehrawat", 1.03, 6.4, 8.5, 0.32, size=15, bold=True)
    add_text(slide, "August 4th 2026", 1.03, 6.76, 8.5, 0.28, size=12, color=MUTED)
    set_notes(
        slide,
        "Two things to say before slide 2. One: this is a standalone test server, nothing has been "
        "deployed to or changed in the live EnzymeX service. Two: this is not a fourth EC predictor. "
        "It is supporting evidence that sits next to what ECPICK, HIT-EC and CLEAN already produce.",
    )

    # ------------------------------------------------------------- 2 the task
    slide = prs.slides.add_slide(blank)
    add_header(slide, "Why this exists", "The task", 2)
    add_rect(slide, 0.72, 1.5, 5.78, 2.62, fill=PANEL, line=LINE)
    add_text(slide, "EnzymeX today", 0.98, 1.75, 5.2, 0.34, size=18, bold=True)
    add_bullets(
        slide,
        [
            "Three models predict EC numbers: ECPICK, HIT-EC, CLEAN.",
            "DIAMOND is the only sequence-similarity tool on the site.",
            "The user sees a prediction, but little independent evidence for it.",
        ],
        0.98,
        2.24,
        5.2,
        1.45,
        size=13.5,
        spacing=7,
    )
    add_rect(slide, 6.83, 1.5, 5.79, 2.62, fill=RED_LIGHT, line=RED_LIGHT)
    add_text(slide, "What was asked", 7.09, 1.75, 5.2, 0.34, size=18, bold=True, color=RED)
    add_bullets(
        slide,
        [
            'Suhyeong\'s July 20 handoff, future work: "BLAST and HMMER should be implemented on it."',
            "Dr. Kang: add them to the result page, build on a separate test server, use a copy of the database.",
            "Later: use Swiss-Prot and PDB together.",
        ],
        7.09,
        2.24,
        5.2,
        1.45,
        size=13.5,
        spacing=7,
        color=INK,
    )
    add_rect(slide, 0.72, 4.38, 11.9, 1.05, fill=WHITE, line=RED, line_width=1.5)
    add_text(
        slide,
        "This is evidence, not prediction.",
        0.98,
        4.59,
        11.4,
        0.34,
        size=19,
        bold=True,
        color=RED,
        align=PP_ALIGN.CENTER,
    )
    add_text(
        slide,
        "BLAST and HMMER answer a different question than the models: which known proteins does this look like, and what do they do?",
        0.98,
        4.99,
        11.4,
        0.3,
        size=13.5,
        color=CHARCOAL,
        align=PP_ALIGN.CENTER,
    )
    add_text(slide, "Scope of this talk", 0.72, 5.72, 5.0, 0.3, size=15, bold=True)
    scope = [
        ("What I built", RED),
        ("How it works", BLUE),
        ("Does it work", GREEN),
        ("What's next", PURPLE),
    ]
    for i, (label, accent) in enumerate(scope):
        x = 0.72 + i * 3.02
        add_rect(slide, x, 6.12, 2.82, 0.62, fill=WHITE, line=LINE)
        add_rect(slide, x, 6.12, 0.09, 0.62, fill=accent, line=accent, radius=False, line_width=0)
        add_text(slide, label, x + 0.25, 6.25, 2.4, 0.3, size=14, bold=True, color=accent)
    add_footer(slide)
    set_notes(
        slide,
        "Frame it as picking up the last item on Suhyeong's future-work list. The site can tell a "
        "biologist what EC number it thinks their protein has, but it cannot show them a known protein "
        "that looks like theirs. That is what BLAST and HMMER add. Say clearly it is not a fourth model.",
    )

    # --------------------------------------------------------- 3 what I built
    slide = prs.slides.add_slide(blank)
    add_header(slide, "A working search layer, end to end", "What I built", 3)
    flow = [
        ("1", "Copied DB", "read-only"),
        ("2", "Offline build", "FASTA + indexes"),
        ("3", "Sequence in", "web submission"),
        ("4", "Three searches", "BLAST + HMMER"),
        ("5", "Result page", "HTML, CSV, JSON"),
    ]
    for i, (number, title, subtitle) in enumerate(flow):
        x = 0.72 + i * 2.42
        add_rect(slide, x, 1.7, 1.94, 1.28, fill=WHITE, line=LINE)
        circle = slide.shapes.add_shape(MSO_SHAPE.OVAL, Inches(x + 0.16), Inches(1.97), Inches(0.5), Inches(0.5))
        circle.fill.solid()
        circle.fill.fore_color.rgb = rgb(RED if i in (0, 4) else CHARCOAL)
        circle.line.color.rgb = rgb(RED if i in (0, 4) else CHARCOAL)
        add_text(slide, number, x + 0.17, 2.05, 0.48, 0.22, size=12, color=WHITE, bold=True, align=PP_ALIGN.CENTER)
        add_text(slide, title, x + 0.72, 1.88, 1.14, 0.3, size=12, bold=True)
        add_text(slide, subtitle, x + 0.72, 2.24, 1.14, 0.4, size=9.5, color=MUTED)
        if i < len(flow) - 1:
            arrow = slide.shapes.add_shape(MSO_SHAPE.RIGHT_ARROW, Inches(x + 2.01), Inches(2.17), Inches(0.32), Inches(0.34))
            arrow.fill.solid()
            arrow.fill.fore_color.rgb = rgb(LINE)
            arrow.line.color.rgb = rgb(LINE)
    cards = [
        (
            "Reference builder",
            "Reads the copied database once. Filters to Swiss-Prot and PDB, drops bad sequences, removes duplicates, and writes the files the searches use.",
            RED,
        ),
        (
            "Search service",
            "Runs blastp, phmmer and hmmscan through one plain function. It knows nothing about the web framework, so EnzymeX can call it directly.",
            BLUE,
        ),
        (
            "Result interface",
            "One table per method with the EC number, source and description attached. Exports to CSV and JSON. Says explicitly when a method found nothing.",
            GREEN,
        ),
    ]
    for i, (title, body, accent) in enumerate(cards):
        x = 0.72 + i * 4.02
        add_rect(slide, x, 3.35, 3.8, 2.6, fill=PANEL, line=LINE)
        add_rect(slide, x, 3.35, 3.8, 0.11, fill=accent, line=accent, radius=False, line_width=0)
        add_text(slide, title, x + 0.25, 3.65, 3.3, 0.36, size=17, bold=True, color=accent)
        add_text(slide, body, x + 0.25, 4.2, 3.3, 1.5, size=12.5, color=CHARCOAL, line_spacing=1.1)
    add_rect(slide, 0.72, 6.15, 11.9, 0.52, fill=RED_LIGHT, line=RED_LIGHT)
    add_text(
        slide,
        "All of it runs today on a standalone server. The live EnzymeX service and repository are untouched.",
        0.94,
        6.29,
        11.46,
        0.26,
        size=12.5,
        color=RED,
        bold=True,
        align=PP_ALIGN.CENTER,
    )
    add_footer(slide)
    set_notes(
        slide,
        "Walk the five boxes left to right, then say the middle card is the important one: the search "
        "code is a library, not a website. EnzymeX can call run_search from its existing scheduled job "
        "without taking any of my web code.",
    )

    # ------------------------------------------------------- 4 the two halves
    slide = prs.slides.add_slide(blank)
    add_header(slide, "Build once, then search many times", "How it works", 4)
    add_rect(slide, 0.72, 1.55, 5.78, 3.7, fill=BLUE_LIGHT, line=BLUE_LIGHT)
    add_chip(slide, "OFFLINE  |  MINUTES  |  RUN RARELY", 0.98, 1.8, 3.4, fill=WHITE, color=BLUE, border=BLUE)
    add_text(slide, "Reference build", 0.98, 2.3, 5.26, 0.4, size=22, bold=True, color=BLUE)
    add_bullets(
        slide,
        [
            "The only step that opens the database.",
            "Exports the sequences to a FASTA file.",
            "Builds the BLAST index and the family profiles.",
            "Writes EC number, source and description to a small local file.",
            "Stamps everything with one build ID.",
        ],
        0.98,
        2.86,
        5.26,
        2.2,
        size=13.5,
        spacing=7,
    )
    add_rect(slide, 6.83, 1.55, 5.79, 3.7, fill=GREEN_LIGHT, line=GREEN_LIGHT)
    add_chip(slide, "ONLINE  |  SECONDS  |  EVERY REQUEST", 7.09, 1.8, 3.55, fill=WHITE, color=GREEN, border=GREEN)
    add_text(slide, "The search", 7.09, 2.3, 5.26, 0.4, size=22, bold=True, color=GREEN)
    add_bullets(
        slide,
        [
            "Validates the submitted sequence.",
            "Runs the three searches against the built files.",
            "Reads the EC and source from that same local file.",
            "Renders the tables.",
            "Never opens a database connection.",
        ],
        7.09,
        2.86,
        5.26,
        2.2,
        size=13.5,
        spacing=7,
    )
    add_text(slide, "Why split it this way", 0.72, 5.5, 5.0, 0.32, size=16, bold=True)
    reasons = [
        ("No database at request time", "A user search cannot touch, slow down or leak the database.", RED),
        ("One build ID on everything", "A hit and its EC label always come from the same snapshot.", BLUE),
        ("The slow work is done once", "Indexing runs on a schedule, not while a user waits.", GREEN),
    ]
    for i, (title, body, accent) in enumerate(reasons):
        x = 0.72 + i * 4.02
        add_rect(slide, x, 5.9, 3.8, 0.95, fill=WHITE, line=LINE)
        add_rect(slide, x, 5.9, 0.09, 0.95, fill=accent, line=accent, radius=False, line_width=0)
        add_text(slide, title, x + 0.25, 6.04, 3.35, 0.28, size=13, bold=True, color=accent)
        add_text(slide, body, x + 0.25, 6.35, 3.35, 0.4, size=10.5, color=CHARCOAL)
    add_footer(slide)
    set_notes(
        slide,
        "This is the one structural decision in the whole project. Everything expensive happens once, "
        "offline. A user request only reads files. That is why a misconfigured credential is literally "
        "unreachable from the website, and why a hit can never be labelled with a different snapshot's "
        "annotation.",
    )

    # ------------------------------------------------- 5 the three searches
    slide = prs.slides.add_slide(blank)
    add_header(slide, "Three searches, three different questions", "How it works", 5)
    add_method_card(
        slide,
        "blastp",
        "Compares the sequence letter by letter against every known protein.",
        [
            "The default. Fast.",
            "Gives identity and coverage.",
            "Best for close relatives.",
        ],
        ["always on"],
        0.72,
        BLUE,
        BLUE_LIGHT,
        height=3.5,
    )
    add_method_card(
        slide,
        "phmmer",
        "Same coverage as BLAST, different statistics. An independent second opinion.",
        [
            "Agrees with BLAST in testing.",
            "Much slower at scale.",
            "Recommend keeping it optional.",
        ],
        ["optional"],
        4.74,
        GREEN,
        GREEN_LIGHT,
        height=3.5,
    )
    add_method_card(
        slide,
        "hmmscan",
        "Compares against family profiles instead of individual proteins.",
        [
            "Catches distant relatives.",
            "Answers with a family, not a hit.",
            "Only covers families I could build.",
        ],
        ["partial coverage"],
        8.76,
        PURPLE,
        PURPLE_LIGHT,
        height=3.5,
    )
    add_rect(slide, 0.72, 5.5, 11.9, 1.05, fill=AMBER_LIGHT, line=AMBER_LIGHT)
    add_text(
        slide,
        "The three results are shown as three separate tables, never merged.\nTheir scores are not the same quantity and must not be ranked against each other.",
        0.94,
        5.75,
        11.46,
        0.6,
        size=14,
        color=AMBER,
        bold=True,
        align=PP_ALIGN.CENTER,
    )
    add_footer(slide)
    set_notes(
        slide,
        "If asked why not merge them into one ranking: an E-value means how many matches this good you "
        "would expect by chance, and that depends on how big the search space is and which statistical "
        "model was used. BLAST and phmmer search all references; hmmscan searches the profile set. "
        "Putting them in one sorted list invites people to compare numbers that are not comparable.",
    )

    # ------------------------------------------------ 6 the design decision
    slide = prs.slides.add_slide(blank)
    add_header(slide, "Family profiles are built by sequence, not by EC number", "The one design decision", 6)
    add_rect(slide, 0.72, 1.55, 5.78, 2.5, fill=RED_LIGHT, line=RED_LIGHT)
    add_chip(slide, "THE OBVIOUS APPROACH", 0.98, 1.8, 2.35, fill=WHITE, color=RED, border=RED)
    add_text(slide, "One profile per EC number", 0.98, 2.3, 5.26, 0.38, size=20, bold=True, color=RED)
    add_text(
        slide,
        "Group every protein that shares an EC number and build one family model from the group.",
        0.98,
        2.8,
        5.26,
        0.6,
        size=13.5,
        color=INK,
        line_spacing=1.1,
    )
    add_text(slide, "This is wrong.", 0.98, 3.52, 5.26, 0.32, size=16, bold=True, color=RED)
    add_rect(slide, 6.83, 1.55, 5.79, 2.5, fill=PURPLE_LIGHT, line=PURPLE_LIGHT)
    add_chip(slide, "WHAT I DID INSTEAD", 6.99 + 0.1, 1.8, 2.2, fill=WHITE, color=PURPLE, border=PURPLE)
    add_text(slide, "Cluster first, label after", 7.09, 2.3, 5.26, 0.38, size=20, bold=True, color=PURPLE)
    add_text(
        slide,
        "Group by actual sequence similarity, then read off which EC labels landed in each group and report how consistent they are.",
        7.09,
        2.8,
        5.26,
        0.7,
        size=13.5,
        color=INK,
        line_spacing=1.1,
    )
    add_text(slide, "Consistency is measured, not assumed.", 7.09, 3.62, 5.26, 0.32, size=13, bold=True, color=PURPLE)

    add_rect(slide, 0.72, 4.25, 11.9, 2.15, fill=PANEL, line=LINE)
    add_text(slide, "Why:  an EC number names a chemical reaction, not a protein family", 0.98, 4.48, 11.4, 0.34, size=17, bold=True)
    add_text(
        slide,
        "Nature solved the same reaction more than once with completely unrelated proteins. Superoxide dismutase, EC 1.15.1.1, exists as a copper/zinc "
        "enzyme and as a manganese/iron enzyme. Same job, nothing alike. Averaging them into one profile produces a model of nothing, and the "
        "dangerous part is that it still outputs confident-looking scores.",
        0.98,
        4.9,
        11.4,
        0.95,
        size=13,
        color=CHARCOAL,
        line_spacing=1.15,
    )
    add_rect(slide, 0.98, 5.8, 11.38, 0.62, fill=GREEN_LIGHT, line=GREEN_LIGHT)
    add_text(
        slide,
        "Check that it works: submitting a human Mn-SOD matches only the manganese/iron\nfamilies and none of the copper/zinc ones, despite the identical EC number.",
        1.16,
        5.9,
        11.02,
        0.46,
        size=12,
        color=GREEN,
        bold=True,
        align=PP_ALIGN.CENTER,
    )
    add_footer(slide)
    set_notes(
        slide,
        "This is the slide to be able to defend. The takeaway is that clustering by EC would have merged "
        "unrelated folds. Concrete evidence from the development build: glutathione transferase split "
        "into 13 separate families, carbonic anhydrase into 4, superoxide dismutase into 5. Each of "
        "those is a single EC number.",
    )

    # ---------------------------------------------------------- 7 validation
    slide = prs.slides.add_slide(blank)
    add_header(slide, "It reproduces the existing BLAST result exactly", "Does it work", 7)
    add_rect(slide, 0.72, 1.5, 11.9, 1.62, fill=BLUE_LIGHT, line=BLUE_LIGHT)
    add_text(slide, "452 / 452", 1.05, 1.72, 3.1, 0.74, size=46, bold=True, color=BLUE)
    add_text(
        slide,
        "queries returned the identical top hit",
        1.08,
        2.5,
        3.4,
        0.4,
        size=12.5,
        color=BLUE,
        bold=True,
    )
    add_text(
        slide,
        "I ran Beomsu's query set through my pipeline with his parameters.",
        4.7,
        1.76,
        7.6,
        0.34,
        size=14.5,
        bold=True,
    )
    add_bullets(
        slide,
        [
            "Every comparable query agreed on the top hit, across two different BLAST versions.",
            "This proves the pipeline is wired correctly. It is a correctness check, not an accuracy score.",
            "Full test suite passes, including checks that references, FASTA and metadata stay in sync.",
        ],
        4.7,
        2.2,
        7.62,
        0.9,
        size=12.5,
        spacing=3,
    )
    add_text(slide, "Two questions people confuse", 0.72, 3.4, 6.0, 0.32, size=16, bold=True)
    add_rect(slide, 0.72, 3.85, 5.78, 1.5, fill=WHITE, line=LINE)
    add_rect(slide, 0.72, 3.85, 0.09, 1.5, fill=GREEN, line=GREEN, radius=False, line_width=0)
    add_text(slide, "Does my BLAST behave like his BLAST?", 1.02, 4.05, 5.3, 0.3, size=14, bold=True, color=GREEN)
    add_text(
        slide,
        "Yes, exactly. Same queries, same parameters, same top hit every time. This is the number above.",
        1.02,
        4.42,
        5.3,
        0.7,
        size=12,
        color=CHARCOAL,
        line_spacing=1.1,
    )
    add_rect(slide, 6.83, 3.85, 5.79, 1.5, fill=WHITE, line=LINE)
    add_rect(slide, 6.83, 3.85, 0.09, 1.5, fill=AMBER, line=AMBER, radius=False, line_width=0)
    add_text(slide, "Does the top hit have the right EC?", 7.13, 4.05, 5.3, 0.3, size=14, bold=True, color=AMBER)
    add_text(
        slide,
        "Measured separately, after removing anything identical to a test sequence so it could not find itself. Narrow slice, so not an EnzymeX accuracy figure.",
        7.13,
        4.42,
        5.3,
        0.85,
        size=12,
        color=CHARCOAL,
        line_spacing=1.1,
    )
    add_rect(slide, 0.72, 5.6, 11.9, 1.05, fill=PANEL, line=LINE)
    add_text(slide, "Sanity checks on the browser workflow", 0.98, 5.78, 5.5, 0.3, size=14, bold=True)
    add_text(
        slide,
        "A known enzyme returns strong, mutually consistent hits from all three methods. An unrelated non-enzyme returns nothing. "
        "\"No hit\" means this reference set has no detectable relative. It does not mean the protein is not an enzyme, and the page says so.",
        0.98,
        6.12,
        11.4,
        0.45,
        size=12,
        color=CHARCOAL,
        line_spacing=1.1,
    )
    add_footer(slide)
    set_notes(
        slide,
        "Lead the meeting with this slide if Dr. Kang asks about validation first. The 452/452 is the "
        "strongest single result: two different BLAST versions, his parameters, identical top hit on "
        "every comparable query. Be careful not to let the second box get quoted as EnzymeX accuracy. "
        "It was measured on a small, close-homolog slice.",
    )

    # ----------------------------------------------------------- 8 real data
    slide = prs.slides.add_slide(blank)
    add_header(slide, "Rebuilt on real Swiss-Prot and PDB data", "Does it work", 8)
    add_metric_card(slide, "272,112", "references built", 0.72, 1.5, 3.6, accent=RED, note="Swiss-Prot + PDB, public proxy dataset")
    add_metric_card(slide, "~4.5 min", "full offline build", 4.5, 1.5, 3.6, accent=CHARCOAL, note="one-time, not per request")
    add_metric_card(slide, "0", "changes to the search code", 8.28, 1.5, 4.34, accent=GREEN, note="only a new loader script")

    add_rect(slide, 0.72, 2.95, 5.78, 3.4, fill=AMBER_LIGHT, line=AMBER_LIGHT)
    add_chip(slide, "WHAT REAL DATA EXPOSED", 0.98, 3.18, 2.4, fill=WHITE, color=AMBER, border=AMBER)
    add_text(slide, "A trap the small test set could not show", 0.98, 3.68, 5.26, 0.38, size=18, bold=True, color=AMBER)
    add_bullets(
        slide,
        [
            "GFP is the jellyfish protein that glows green. It is not an enzyme.",
            "On real data it returns 25 near-perfect BLAST hits.",
            "All of them are PDB structures where GFP was glued onto another protein for imaging.",
            "15 carry an EC number, spanning eight different values. None belong to GFP.",
        ],
        0.98,
        4.18,
        5.26,
        2.0,
        size=12.5,
        color=INK,
        spacing=6,
    )

    add_rect(slide, 6.83, 2.95, 5.79, 3.4, fill=BLUE_LIGHT, line=BLUE_LIGHT)
    add_chip(slide, "THE PAGE ALREADY CATCHES IT", 7.09, 3.18, 2.75, fill=WHITE, color=BLUE, border=BLUE)
    add_text(slide, "Full query, partial subject", 7.09, 3.68, 5.26, 0.38, size=18, bold=True, color=BLUE)
    add_bullets(
        slide,
        [
            "The match covers all of the submitted sequence but only about half of the thing it matched.",
            "That lopsidedness is the signature of matching one half of a fused protein.",
            "BLAST does not report it, so the pipeline computes it and shows it on every row.",
            "Without it, the page would hand the user the wrong protein's EC number with full confidence.",
        ],
        7.09,
        4.18,
        5.26,
        2.0,
        size=12.5,
        color=INK,
        spacing=6,
    )
    add_footer(slide)
    set_notes(
        slide,
        "Tell this as a story, not a statistic. The point is that a clean test set could never have "
        "produced this failure, real data did, and the coverage column I had already built is what "
        "exposes it. Caveat to state out loud: the dataset is a public stand-in for the real EnzymeX "
        "table, so the numbers here do not transfer to production.",
    )

    # --------------------------------------------------------- 9 what's next
    slide = prs.slides.add_slide(blank)
    add_header(slide, "Next steps, and what I need to start them", "What's next", 9)
    steps = [
        ("1", "Get access", "Read-only access to the copied database and to the EnzymeX repository. Everything below is blocked on this.", RED),
        ("2", "Check the real schema", "Confirm column names, a stable key and the actual source labels. My code was written against documentation, not the real table.", CHARCOAL),
        ("3", "Rebuild on the real copy", "Run the same pipeline against genuine EnzymeX data and re-measure. Current figures come from a stand-in dataset.", CHARCOAL),
        ("4", "Wire into the existing job", "Run the searches as extra steps in the scheduler that already runs ECPICK, HIT-EC and CLEAN, and add the tables to the result page.", CHARCOAL),
    ]
    for i, (number, title, body, accent) in enumerate(steps):
        add_step_row(slide, number, title, body, 1.55 + i * 1.22, accent, i == len(steps) - 1)

    add_rect(slide, 9.28, 1.5, 3.34, 5.28, fill=RED_LIGHT, line=RED_LIGHT)
    add_text(slide, "Decisions I need", 9.58, 1.78, 2.8, 0.5, size=18, color=RED, bold=True)
    decisions = [
        "Can I get repository and read-only database access?",
        "DIAMOND and BLAST answer the same question. Show both, or pick one?",
        "Run phmmer for every job, or keep it optional?",
    ]
    for i, decision in enumerate(decisions):
        y = 2.5 + i * 1.24
        add_rect(slide, 9.56, y, 2.78, 1.05, fill=WHITE, line=LINE)
        add_text(slide, str(i + 1), 9.74, y + 0.36, 0.32, 0.24, size=12, color=RED, bold=True, align=PP_ALIGN.CENTER)
        add_text(slide, decision, 10.14, y + 0.12, 2.0, 0.85, size=11, color=INK, bold=True, valign=MSO_ANCHOR.MIDDLE)
    add_rect(slide, 9.56, 6.24, 2.78, 0.4, fill=WHITE, line=WHITE)
    add_text(slide, "Access is the blocker", 9.7, 6.31, 2.5, 0.28, size=11.5, color=RED, bold=True, align=PP_ALIGN.CENTER)
    add_footer(slide)
    set_notes(
        slide,
        "Ask for access early in the meeting, not at the end. Suhyeong already said the repository "
        "permission has to come from Dr. Kang directly. Steps two and three are about a day of work "
        "each once access exists. The two questions below the access one are product decisions that "
        "are not mine to make.",
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
