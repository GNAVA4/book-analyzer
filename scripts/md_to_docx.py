"""Convert REPORT.md to .docx using python-docx.

Handles headings (#/##/###), paragraphs, tables, bullet lists, italic intro
blocks, inline `code`, **bold**, and horizontal rules. Tailored for this
project's report — not a general-purpose markdown->docx converter.
"""
import sys, pathlib, re

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from docx import Document
from docx.shared import Pt, RGBColor, Cm
from docx.enum.text import WD_ALIGN_PARAGRAPH

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "REPORT_DRAFT.md"
OUT = ROOT / "ОТЧЕТ.docx"

doc = Document()

# Page margins
for sec in doc.sections:
    sec.top_margin = Cm(1.8)
    sec.bottom_margin = Cm(1.8)
    sec.left_margin = Cm(2.2)
    sec.right_margin = Cm(2.2)

# Default font
style = doc.styles["Normal"]
style.font.name = "Calibri"
style.font.size = Pt(11)


def _add_runs(paragraph, text):
    """Parse inline **bold**, *italic*, `code`, [text](url) and add runs."""
    pattern = re.compile(r"(\*\*[^*]+\*\*|`[^`]+`|\*[^*\n]+\*|\[[^\]]+\]\([^)]+\))")
    pos = 0
    for m in pattern.finditer(text):
        if m.start() > pos:
            paragraph.add_run(text[pos:m.start()])
        tok = m.group()
        if tok.startswith("**"):
            r = paragraph.add_run(tok[2:-2])
            r.bold = True
        elif tok.startswith("`"):
            r = paragraph.add_run(tok[1:-1])
            r.font.name = "Consolas"
            r.font.size = Pt(10)
        elif tok.startswith("*"):
            r = paragraph.add_run(tok[1:-1])
            r.italic = True
        elif tok.startswith("["):
            label = re.match(r"\[([^\]]+)\]", tok).group(1)
            paragraph.add_run(label)
        pos = m.end()
    if pos < len(text):
        paragraph.add_run(text[pos:])


def add_heading(text, level):
    p = doc.add_heading("", level=min(level, 3))
    _add_runs(p, text)


def add_paragraph(text, italic=False):
    p = doc.add_paragraph()
    if italic:
        text_clean = text.strip("_")
        _add_runs(p, text_clean)
        for r in p.runs:
            r.italic = True
    else:
        _add_runs(p, text)


def add_bullet(text):
    p = doc.add_paragraph(style="List Bullet")
    _add_runs(p, text.lstrip("- ").lstrip("* "))


def add_table(header, rows):
    # Cap column count to header
    ncol = len(header)
    table = doc.add_table(rows=1 + len(rows), cols=ncol)
    table.style = "Light Grid Accent 1"
    table.autofit = True
    hdr = table.rows[0].cells
    for i, h in enumerate(header[:ncol]):
        hdr[i].text = ""
        p = hdr[i].paragraphs[0]
        r = p.add_run(h.strip())
        r.bold = True
        r.font.size = Pt(10)
    for ri, row in enumerate(rows, start=1):
        cells = table.rows[ri].cells
        for ci in range(ncol):
            cells[ci].text = ""
            p = cells[ci].paragraphs[0]
            val = row[ci].strip() if ci < len(row) else ""
            _add_runs(p, val)
            for r in p.runs:
                r.font.size = Pt(10)


def add_hr():
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run("***")
    r.bold = True


# --- Parse REPORT.md -------------------------------------------------

src = SRC.read_text(encoding="utf-8")
lines = src.splitlines()

i = 0
while i < len(lines):
    line = lines[i]

    # Headings
    m = re.match(r"^(#{1,6})\s+(.+)$", line)
    if m:
        level = len(m.group(1))
        add_heading(m.group(2).strip(), level)
        i += 1
        continue

    # Horizontal rule
    if re.match(r"^-{3,}\s*$", line):
        add_hr()
        i += 1
        continue

    # Italic intro block (starts with _, ends with _)
    if line.startswith("_") and line.rstrip().endswith("_") and len(line.strip()) > 2:
        add_paragraph(line.strip(), italic=True)
        i += 1
        continue

    # Table — line starts with | and next line is |---|...
    if line.lstrip().startswith("|") and i + 1 < len(lines) \
       and re.match(r"^\|[\s\-:|]+\|\s*$", lines[i + 1]):
        # Collect rows
        header = [c.strip() for c in line.strip().strip("|").split("|")]
        rows = []
        j = i + 2
        while j < len(lines) and lines[j].lstrip().startswith("|"):
            row = [c.strip() for c in lines[j].strip().strip("|").split("|")]
            rows.append(row)
            j += 1
        add_table(header, rows)
        i = j
        continue

    # Bullet
    if re.match(r"^\s*[-*]\s+", line):
        add_bullet(line)
        i += 1
        continue

    # Empty
    if not line.strip():
        i += 1
        continue

    # Regular paragraph — collect continuation lines until blank
    para = [line]
    j = i + 1
    while j < len(lines) and lines[j].strip() and \
          not re.match(r"^#|^\|", lines[j]) and \
          not re.match(r"^\s*[-*]\s+", lines[j]) and \
          not re.match(r"^-{3,}\s*$", lines[j]):
        para.append(lines[j])
        j += 1
    add_paragraph(" ".join(para))
    i = j

doc.save(OUT)
print(f"Saved: {OUT}")
print(f"Size: {OUT.stat().st_size:,} bytes")
