"""
Рендер страниц PDF в PNG для визуальной проверки (vision ground-truth).

Usage:
    venv/Scripts/python.exe scripts/render_pages.py <book_substring> <pages> [dpi]
    pages: "11-16" или "11,14,20" (0-based физические индексы)
    Сохраняет в .audit_pages/<book>_p<NN>.png
"""
import sys
from pathlib import Path
import fitz

ROOT = Path(__file__).resolve().parent.parent
PDF_DIR = ROOT / "test"
OUT = ROOT / ".audit_pages"

def parse_pages(spec):
    out = []
    for part in spec.split(','):
        if '-' in part:
            a, b = part.split('-'); out += list(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    return out

def main():
    substr, spec = sys.argv[1], sys.argv[2]
    dpi = int(sys.argv[3]) if len(sys.argv) > 3 else 130
    pdfs = [p for p in PDF_DIR.glob('*.pdf') if substr.lower() in p.name.lower()]
    if not pdfs:
        print("not found"); return
    pdf = pdfs[0]
    OUT.mkdir(exist_ok=True)
    doc = fitz.open(pdf)
    tag = ''.join(c for c in pdf.stem[:16] if c.isalnum())
    for i in parse_pages(spec):
        if 0 <= i < len(doc):
            pix = doc[i].get_pixmap(dpi=dpi)
            f = OUT / f"{tag}_p{i:03d}.png"
            pix.save(f)
            print(f"{f}  ({pix.width}x{pix.height}, txt={len(doc[i].get_text().strip())})")
    doc.close()

if __name__ == '__main__':
    main()
