"""
Карта 'слепоты fitz' по страницам PDF.

Цель: найти страницы, где текстовый слой беден, но на странице ЕСТЬ контент
(изображения / большая площадь рисунков). На таких страницах fitz (а значит и
пайплайн) может терять реальный текст — их нужно проверять визуально (vision/OCR),
а не сравнением fitz-vs-fitz.

Метрики на страницу:
  txt      - длина извлечённого текста
  imgs     - число растровых изображений
  img_cov  - доля площади страницы, покрытая изображениями (0..1)

Флаги:
  SCAN   - txt < 100 и img_cov > 0.5  -> страница-картинка (вероятный скан)
  SPARSE - txt < 200 и есть изображения -> мало текста, есть графика
  BIGFONT- txt < 200, картинок нет, но это не пустая страница (возможна крупная типографика)

Usage:
    venv/Scripts/python.exe scripts/fitz_blindness.py <book_substring>
    venv/Scripts/python.exe scripts/fitz_blindness.py            # сводка по всем
"""
import sys
from pathlib import Path
import fitz

ROOT = Path(__file__).resolve().parent.parent
PDF_DIR = ROOT / "test"

def page_stats(page):
    txt = page.get_text()
    rect = page.rect
    parea = max(rect.width * rect.height, 1)
    imgs = page.get_image_info(xrefs=False)
    img_area = 0.0
    for im in imgs:
        b = im.get('bbox')
        if b:
            w = max(b[2] - b[0], 0); h = max(b[3] - b[1], 0)
            img_area += w * h
    drawings = page.get_drawings()
    return {
        'txt': len(txt.strip()),
        'imgs': len(imgs),
        'img_cov': round(min(img_area / parea, 1.0), 2),
        'draws': len(drawings),
    }

def classify(s):
    if s['txt'] < 100 and s['img_cov'] > 0.5:
        return 'SCAN'
    if s['txt'] < 200 and s['imgs'] > 0:
        return 'SPARSE'
    if s['txt'] < 200 and (s['imgs'] > 0 or s['draws'] > 20):
        return 'BIGFONT?'
    return ''

def audit(pdf_path: Path, detailed=False):
    doc = fitz.open(pdf_path)
    flagged = {'SCAN': [], 'SPARSE': [], 'BIGFONT?': []}
    rows = []
    for i in range(len(doc)):
        s = page_stats(doc[i])
        cls = classify(s)
        rows.append((i, s, cls))
        if cls:
            flagged[cls].append(i)
    doc.close()
    return flagged, rows, len(rows)

def fmt_ranges(nums):
    if not nums:
        return '-'
    nums = sorted(nums)
    out = []; a = b = nums[0]
    for n in nums[1:]:
        if n == b + 1:
            b = n
        else:
            out.append(f"{a}" if a == b else f"{a}-{b}"); a = b = n
    out.append(f"{a}" if a == b else f"{a}-{b}")
    return ','.join(out)

def main():
    substr = sys.argv[1] if len(sys.argv) > 1 else None
    pdfs = sorted(PDF_DIR.glob('*.pdf'))
    if substr:
        pdfs = [p for p in pdfs if substr.lower() in p.name.lower()]
    print(f"{'book':<46}{'pg':>5}{'SCAN':>6}{'SPARSE':>8}{'BIGFONT?':>10}")
    print('-'*75)
    detail = []
    for p in pdfs:
        flagged, rows, n = audit(p)
        print(f"{p.name[:45]:<46}{n:>5}{len(flagged['SCAN']):>6}"
              f"{len(flagged['SPARSE']):>8}{len(flagged['BIGFONT?']):>10}")
        detail.append((p, flagged, rows))

    if substr:
        for p, flagged, rows in detail:
            print(f"\n{'='*75}\n{p.name}")
            for cls in ('SCAN', 'SPARSE', 'BIGFONT?'):
                print(f"  {cls:<9}: {fmt_ranges(flagged[cls])}")
            # топ-страницы с минимальным текстом, но с графикой
            sus = [(i, s) for i, s, c in rows if c]
            sus.sort(key=lambda x: x[1]['txt'])
            print(f"  --- топ-20 подозрительных (мало текста + графика) ---")
            print(f"  {'pg':>4}{'txt':>7}{'imgs':>6}{'img_cov':>9}{'draws':>7}  class")
            for i, s in sus[:20]:
                c = classify(s)
                print(f"  {i:>4}{s['txt']:>7}{s['imgs']:>6}{s['img_cov']:>9}{s['draws']:>7}  {c}")

if __name__ == '__main__':
    main()
