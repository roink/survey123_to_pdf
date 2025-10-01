#!/usr/bin/env python3
"""
Generate per-response PDFs from a Survey123 CSV export.

Features
--------
- A4 pages, clean typography with ReportLab (Platypus).
- Alternates question and answer (Q, then A) for readability.
- Skips unanswered questions automatically.
- Detects repeated question blocks created by duplicate headers (e.g., "Question", "Question.1", ... "Question.4")
  and structures them into per-file sections (or any repeated group).
- Sensible defaults that exclude Survey123 system/meta fields automatically.

Install
-------
pip install reportlab pandas

Usage
-----
python survey123_to_pdf.py input.csv -o out_pdfs
python survey123_to_pdf.py input.csv -o out_pdfs --rows 0,2,5-7
"""

from __future__ import annotations
import argparse
import os
import re
import sys
import math
import html
from typing import Iterable, List, Dict, Tuple, Optional
from io import BytesIO
from pathlib import Path
import pkgutil

try:
    from importlib import resources as importlib_resources
except ImportError:  # pragma: no cover - Python <3.9 fallback
    import importlib_resources  # type: ignore

import pandas as pd

# ReportLab / Platypus imports
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, HRFlowable
)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# ---------- Utilities ----------

def parse_row_ranges(expr: str, max_index: int) -> List[int]:
    """Parse expressions like '0,2,5-7' into row indexes.
    Clamps each index to [0, max_index].
    """
    result = set()
    for part in expr.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            try:
                start, end = int(a), int(b)
            except ValueError:
                continue
            if start > end:
                start, end = end, start
            for i in range(start, end + 1):
                if 0 <= i <= max_index:
                    result.add(i)
        else:
            try:
                i = int(part)
                if 0 <= i <= max_index:
                    result.add(i)
            except ValueError:
                pass
    return sorted(result)


def slugify(text: str, max_len: int = 80) -> str:
    text = str(text or "").strip()
    text = re.sub(r"[^\w\s\-\.]+", "", text, flags=re.UNICODE)
    text = re.sub(r"\s+", "_", text)
    return text[:max_len] or "row"


def is_empty(val) -> bool:
    """True if a cell is effectively empty."""
    if val is None:
        return True
    s = str(val).strip()
    if s == "" or s.lower() in {"nan", "none", "null"}:
        return True
    return False


def base_and_index(col: str) -> Tuple[str, int]:
    """Split a pandas-mangled duplicate column name into (base, index).
    Example: "Question" -> ("Question", 0), "Question.1" -> ("Question", 1)
    """
    m = re.match(r"^(.*?)(?:\.(\d+))?$", col.strip())
    if m:
        base = m.group(1).strip()
        idx = int(m.group(2)) if m.group(2) else 0
        return base, idx
    return col, 0


def first_value_for_base(
    row: pd.Series,
    groups: Dict[str, List[Tuple[int, str]]],
    base: str,
    idx: int = 0
) -> Optional[str]:
    """Return the first non-empty value for the given base header and index."""
    if base in groups:
        for i, full in groups[base]:
            if i == idx:
                val = row.get(full, None)
                if not is_empty(val):
                    return val
                break
    # Fall back to direct lookup (for columns without duplicate suffixes)
    if base in row.index and not is_empty(row.get(base)):
        return row.get(base)
    return None


# Data quality questions should always be collected together at the end.
DATA_QUALITY_QUESTIONS = [
    "Are you aware of special collection methods or theories used in collecting the original data?",
    "What were the goals during the collection of the original data?",
    "Is there an apparent piece of context missing from this data collection that would help to better analyze it?",
    "Was there specific equipment or machinery used for collecting this data?",
    "Are there any critical contextual papers necessary for the interpretation of this data collection?",
    "Are there any other potential causes of errors obvious for this data collection?",
    "Does the absence of data in certain areas reflect a true absence, or could it be due to factors such as collection methods, material degradation, or other biases?",
    "Are there other data that are missing from the data set that would benefit these data?",
    "Are there any specific kinds of technologies or programs that were used to analyze, modify, or manage these data?",
]


FILE_SECTION_SEPARATOR = "Do you have additional files to add?"
FILE_QUESTION_BASES = {
    "Please provide a title that clearly describes your file.",
    "Please provide up to 4 keywords to describe this file.",
    "Please provide a detailed description of the file as a whole.",
    "Please define each field or column in your file (e.g. column in a csv, layer in a NetCDF)",
    "How would you describe the overall nature of the data?",
    "Please provide a comment explaining your choice regarding data nature",
    "Modifications",
    "Please list any citations or references that provide context for the methodologies used in collecting or processing this data.",
    "Please provide details on the model and any experiment-specific configuration:",
    "Please submit here links to any external descriptions, code, or documentation relevant to this file.",
    "Dependencies",
}


def extract_file_sections(row: pd.Series) -> List[List[Tuple[str, str]]]:
    """Return ordered blocks of (question, answer) pairs for file-related repeats."""
    dq_bases = {q.strip() for q in DATA_QUALITY_QUESTIONS}
    sections: List[List[Tuple[str, str]]] = []
    current: List[Tuple[str, str]] = []

    for col in row.index:
        base, _ = base_and_index(col)
        base = base.strip()

        if base in dq_bases:
            if current:
                sections.append(current)
                current = []
            break

        if base == FILE_SECTION_SEPARATOR:
            if current:
                sections.append(current)
                current = []
            continue

        if base in FILE_QUESTION_BASES:
            current.append((base, col))
        else:
            if current:
                sections.append(current)
                current = []

    if current:
        sections.append(current)

    file_sections: List[List[Tuple[str, str]]] = []
    for block in sections:
        qa_block: List[Tuple[str, str]] = []
        for base, col in block:
            val = row.get(col, None)
            if not is_empty(val):
                qa_block.append((base, str(val)))
        if qa_block:
            file_sections.append(qa_block)

    return file_sections


def detect_groups(columns: Iterable[str]) -> Tuple[Dict[str, List[Tuple[int, str]]], int]:
    """Return mapping: base_name -> list[(idx, full_colname)], and max idx seen."""
    groups: Dict[str, List[Tuple[int, str]]] = {}
    max_idx = 0
    for c in columns:
        base, idx = base_and_index(c)
        groups.setdefault(base, []).append((idx, c))
        if idx > max_idx:
            max_idx = idx
    # ensure stable order
    for k in list(groups.keys()):
        groups[k] = sorted(groups[k], key=lambda t: t[0])
    return groups, max_idx


def as_paragraph(text: str, style: ParagraphStyle) -> Paragraph:
    """Escape text for XML/HTML and preserve newlines as <br/>."""
    s = "" if text is None else str(text)
    s = html.escape(s).replace("\n", "<br/>")
    return Paragraph(s, style)


def register_ttf() -> Optional[str]:
    """Register the bundled DejaVuSans font, returning the family name on success."""

    family = "DejaVuSans"
    rel_paths = [
        "dejavu-sans-ttf-2.37/ttf/DejaVuSans.ttf",
        "DejaVuSans.ttf",
    ]

    data: Optional[bytes] = None

    package_candidates = []
    if __package__:
        package_candidates.append(__package__)
    if __spec__ and getattr(__spec__, "name", None):  # type: ignore[name-defined]
        package_candidates.append(__spec__.name)  # type: ignore[attr-defined]
    module_name = Path(__file__).resolve().stem
    package_candidates.append(module_name)

    seen_packages = []
    for pkg in package_candidates:
        if not pkg or pkg in seen_packages:
            continue
        seen_packages.append(pkg)
        for rel in rel_paths:
            try:
                data = pkgutil.get_data(pkg, rel)
            except (FileNotFoundError, ModuleNotFoundError, OSError, ValueError):
                continue
            if data:
                break
        if data:
            break

    if data is None and 'importlib_resources' in globals():
        for pkg in seen_packages:
            try:
                resources_root = importlib_resources.files(pkg)  # type: ignore[attr-defined]
            except (ModuleNotFoundError, AttributeError):
                continue
            for rel in rel_paths:
                try:
                    with resources_root.joinpath(rel).open("rb") as handle:  # type: ignore[attr-defined]
                        data = handle.read()
                    break
                except (FileNotFoundError, IsADirectoryError):
                    continue
            if data:
                break

    if data is None:
        base_dir = Path(__file__).resolve().parent
        for rel in rel_paths:
            candidate = base_dir / rel
            if candidate.exists():
                data = candidate.read_bytes()
                break

    if data is None:
        sys.stderr.write("[warn] Could not load bundled DejaVuSans.ttf; using default ReportLab fonts.\n")
        return None

    try:
        font_buffer = BytesIO(data)
        pdfmetrics.registerFont(TTFont(family, font_buffer))
        return family
    except Exception as exc:
        sys.stderr.write(f"[warn] Failed to register bundled font: {exc}\n")
        return None


# ---------- PDF Rendering ----------

def _footer(canvas, doc, title_text: str):
    canvas.saveState()
    w, h = A4
    page_num = canvas.getPageNumber()
    footer_text = f"{title_text}  —  Page {page_num}"
    canvas.setFont("Helvetica", 9)
    canvas.drawRightString(w - 20*mm, 12*mm, footer_text)
    canvas.restoreState()


def build_pdf_for_row(
    row: pd.Series,
    groups: Dict[str, List[Tuple[int, str]]],
    out_path: str,
    font_family: Optional[str]
):
    """Create a single PDF for the given DataFrame row."""
    # Title: prefer common title-ish fields, else fall back to GlobalID/row index
    candidates = ["Title", "Title of Tier I Data Submitted", "GlobalID"]
    heading = None
    for c in candidates:
        if not c:
            continue
        base, idx = base_and_index(c)
        # find the actual column name for index 0
        colname = None
        if base in groups:
            for i, full in groups[base]:
                if i == 0:
                    colname = full
                    break
        if not colname and c in row.index:
            colname = c
        if colname and not is_empty(row.get(colname, None)):
            value = row[colname]
            heading = str(value) if base == "Title of Tier I Data Submitted" else f"{base}: {value}"
            break

    if not heading:
        heading = f"Submission {row.name}"

    # Prepare document
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    doc = SimpleDocTemplate(
        out_path,
        pagesize=A4,
        leftMargin=20*mm, rightMargin=20*mm,
        topMargin=18*mm, bottomMargin=18*mm,
        title=heading
    )

    # Styles
    styles = getSampleStyleSheet()
    # If custom TTF given, derive styles from it
    if font_family:
        styles["Normal"].fontName = font_family
        styles["Heading1"].fontName = font_family
        styles["Heading2"].fontName = font_family

    Question = ParagraphStyle(
        "Question",
        parent=styles["Normal"],
        fontName=styles["Normal"].fontName,
        fontSize=10.5,
        leading=14,
        spaceBefore=6,
        spaceAfter=2,
        textColor="#333333",
        leftIndent=0,
        # Make questions visually distinct
        underlineWidth=0.5,
    )
    Answer = ParagraphStyle(
        "Answer",
        parent=styles["Normal"],
        fontName=styles["Normal"].fontName,
        fontSize=11,
        leading=15,
        spaceBefore=0,
        spaceAfter=6,
        leftIndent=6,
    )

    h1 = ParagraphStyle(
        "H1",
        parent=styles["Heading1"],
        fontSize=16,
        leading=20,
        spaceBefore=6,
        spaceAfter=8,
    )
    h2 = ParagraphStyle(
        "H2",
        parent=styles["Heading2"],
        fontSize=13,
        leading=16,
        spaceBefore=10,
        spaceAfter=6,
    )

    story: List = []

    # Title
    story.append(as_paragraph(heading, h1))
    story.append(HRFlowable(color="#888888", width="100%", thickness=0.8))
    story.append(Spacer(1, 6))

    # Capture Metadata Owner and Data Pillar (if present)
    meta_items = []
    metadata_owner = first_value_for_base(row, groups, "Metadata Owner")
    data_pillar = first_value_for_base(row, groups, "Data Pillar")
    if not is_empty(metadata_owner):
        meta_items.append(("Metadata Owner", metadata_owner))
    if not is_empty(data_pillar):
        meta_items.append(("Data Pillar", data_pillar))

    for label, value in meta_items:
        story.append(as_paragraph(label, Question))
        story.append(as_paragraph(str(value), Answer))
    if meta_items:
        story.append(Spacer(1, 6))

    # Build list of base names in original order
    base_order = []
    seen = set()
    for col in row.index:
        base, idx = base_and_index(col)
        if base not in seen:
            seen.add(base)
            base_order.append(base)

    # Default exclusions (system/meta)
    dq_bases = {q.strip() for q in DATA_QUALITY_QUESTIONS}
    default_excl = {
        "ObjectID", "GlobalID", "CreationDate", "EditDate",
        "Creator", "Editor", "Owner", "Metadata Owner",
        "Title of Tier I Data Submitted", "Data Pillar",
        "x", "y"
    } | dq_bases | FILE_QUESTION_BASES | {FILE_SECTION_SEPARATOR}
    exclusions = set(default_excl)

    # GENERAL (non-repeated) questions
    general_items = []
    for base in base_order:
        if base in exclusions:
            continue
        cols = groups.get(base, [])
        if len(cols) == 1:  # only index 0 exists
            _, c0 = cols[0]
            val = row.get(c0, None)
            if not is_empty(val):
                general_items.append((base, val))

    # FILE / REPEATED BLOCKS
    file_sections = extract_file_sections(row)

    for idx, block_items in enumerate(file_sections, start=1):
        story.append(Spacer(1, 6))
        story.append(as_paragraph(f"File {idx}", h2))
        story.append(HRFlowable(color="#BBBBBB", width="100%", thickness=0.6))
        story.append(Spacer(1, 2))

        for q, a in block_items:
            story.append(as_paragraph(q, Question))
            story.append(as_paragraph(a, Answer))

    if general_items:
        story.append(Spacer(1, 6))
        story.append(as_paragraph("General", h2))
        story.append(Spacer(1, 2))
        for q, a in general_items:
            story.append(as_paragraph(q, Question))
            story.append(as_paragraph(str(a), Answer))

    # Data Quality section (always at the end)
    dq_items = []
    for question in DATA_QUALITY_QUESTIONS:
        base = question.strip()
        val = first_value_for_base(row, groups, base)
        if is_empty(val):
            answer = "No response provided."
        else:
            answer = str(val)
        dq_items.append((question, answer))

    if dq_items:
        story.append(Spacer(1, 6))
        story.append(as_paragraph("Data Quality", h2))
        story.append(HRFlowable(color="#BBBBBB", width="100%", thickness=0.6))
        story.append(Spacer(1, 2))
        for q, a in dq_items:
            story.append(as_paragraph(q, Question))
            story.append(as_paragraph(a, Answer))

    # Build PDF with footer
    doc.build(story, onFirstPage=lambda c, d: _footer(c, d, heading),
                     onLaterPages=lambda c, d: _footer(c, d, heading))


# ---------- Main ----------

def main():
    ap = argparse.ArgumentParser(description="Generate per-row PDFs from a Survey123 CSV export.")
    ap.add_argument("csv", help="Path to Survey123 CSV export")
    ap.add_argument("-o", "--outdir", default="out_pdfs", help="Output directory (default: out_pdfs)")
    ap.add_argument("--rows", default=None,
                    help="Row indexes to process, e.g. '0,2,5-7'. Default: all rows")

    args = ap.parse_args()

    # Register bundled font for wide Unicode coverage
    font_family = register_ttf()

    # Load CSV; UTF-8 with BOM (utf-8-sig) tends to fit Survey123 exports
    try:
        df = pd.read_csv(args.csv, encoding="utf-8-sig")
    except UnicodeDecodeError:
        df = pd.read_csv(args.csv)  # fallback

    groups, _ = detect_groups(df.columns)

    # Determine row selection
    if args.rows:
        selection = parse_row_ranges(args.rows, len(df) - 1)
    else:
        selection = list(df.index)

    if not selection:
        sys.exit("No rows selected.")

    # Render
    os.makedirs(args.outdir, exist_ok=True)
    used_names = set()
    for i in selection:
        row = df.loc[i]
        # Use a meaningful filename
        title_candidates = ["Title", "Title of Tier I Data Submitted", "GlobalID"]
        title_val = None
        for c in title_candidates:
            if not c:
                continue
            base, idx = base_and_index(c)
            # try exact match, then base 0
            if c in row.index and not is_empty(row[c]):
                title_val = row[c]
                break
            if base in groups:
                for gi, full in groups[base]:
                    if gi == 0 and not is_empty(row.get(full)):
                        title_val = row.get(full)
                        break
            if title_val:
                break

        slug = slugify(title_val if title_val else f"row_{i}")
        base_slug = slug or f"row_{i}"
        candidate = base_slug
        suffix = 1
        while True:
            outfile = os.path.join(args.outdir, f"{candidate}.pdf")
            if candidate not in used_names and not os.path.exists(outfile):
                used_names.add(candidate)
                break
            suffix += 1
            candidate = f"{base_slug}_{suffix}"
        build_pdf_for_row(row, groups, outfile, font_family)
        print(f"✔ Wrote {outfile}")

if __name__ == "__main__":
    main()
