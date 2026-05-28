"""
Аудит соответствия XML-контента реальному PDF.

Для каждой книги:
  - извлекает текст PDF постранично (fitz)
  - парсит секции XML (title, page, confidence, strategy, content)
  - вычисляет смещение между логическими страницами ToC и физическими страницами PDF
  - для каждой секции сравнивает объём контента XML с реальным объёмом текста PDF
    на соответствующем диапазоне страниц -> ratio
  - флагует: пустые, обрезанные (ratio<<1), раздутые/слипшиеся (ratio>>1),
    утечку границы (контент начинается с чужого заголовка), дубли контента

Usage:
    venv/Scripts/python.exe scripts/audit_content.py <book_substring>      # одна книга детально
    venv/Scripts/python.exe scripts/audit_content.py                       # сводка по всем
"""
import sys, re, json, statistics, hashlib
from pathlib import Path
import xml.etree.ElementTree as ET
import fitz

ROOT = Path(__file__).resolve().parent.parent
PDF_DIR = ROOT / "test"
XML_DIR = ROOT / "test_v2"

CJK_RE = re.compile(r'[一-鿿㐀-䶿]')

def norm(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r'[^\wЀ-ӿ]+', ' ', s)
    return re.sub(r'\s+', ' ', s).strip()

def title_key(title: str) -> str:
    """Нормализованный ключ заголовка без ведущей нумерации для поиска в теле."""
    t = re.sub(r'^\s*(глава|часть|chapter|part)?\s*\d+[.\)]?\s*', '', title, flags=re.I)
    return norm(t)

def load_pdf_pages(pdf_path: Path):
    doc = fitz.open(pdf_path)
    pages = [doc[i].get_text() for i in range(len(doc))]
    doc.close()
    return pages

def parse_xml(xml_path: Path):
    root = ET.parse(xml_path).getroot()
    secs = []
    for s in root.findall('.//section'):
        c = s.find('content')
        content = (c.text if c is not None else s.text) or ''
        secs.append({
            'title': s.get('title', ''),
            'page': s.get('page', ''),
            'conf': float(s.get('confidence', '0') or 0),
            'strat': s.get('match_strategy', '') or '',
            'content': content,
        })
    return secs

def to_int(p):
    try:
        return int(str(p).strip())
    except Exception:
        return None

def estimate_offset(secs, pages_norm):
    """Смещение pdf_page_index - toc_page по якорным секциям, найденным в теле."""
    deltas = []
    n = len(pages_norm)
    for s in secs:
        tp = to_int(s['page'])
        if tp is None:
            continue
        key = title_key(s['title'])
        if len(key) < 8:
            continue
        # ищем страницу, где встречается заголовок, рядом с ожидаемой
        candidates = [i for i in range(n) if key in pages_norm[i]]
        if not candidates:
            continue
        # берём кандидата, ближайшего к tp (логическая ~ физическая в первом приближении)
        best = min(candidates, key=lambda i: abs(i - tp))
        deltas.append(best - tp)
    if not deltas:
        return 0, 0
    off = int(round(statistics.median(deltas)))
    # доля якорей, согласных с медианой (±1)
    agree = sum(1 for d in deltas if abs(d - off) <= 1) / len(deltas)
    return off, round(agree, 2)

def expected_len(secs, idx, pages, offset, total_pages):
    """Ожидаемый объём текста PDF для секции idx по диапазону страниц."""
    tp = to_int(secs[idx]['page'])
    if tp is None:
        return None
    start = tp + offset
    # следующая секция с известной страницей
    nxt = None
    for j in range(idx + 1, len(secs)):
        np_ = to_int(secs[j]['page'])
        if np_ is not None and np_ >= tp:
            nxt = np_ + offset
            break
    if nxt is None:
        nxt = total_pages
    start = max(0, min(start, total_pages))
    nxt = max(start, min(nxt, total_pages))
    return sum(len(pages[i]) for i in range(start, nxt))

def audit_book(pdf_path: Path, xml_path: Path, detailed=False):
    pages = load_pdf_pages(pdf_path)
    pages_norm = [norm(p) for p in pages]
    total_pdf = sum(len(p) for p in pages)
    secs = parse_xml(xml_path)
    offset, agree = estimate_offset(secs, pages_norm)

    title_norms = {norm(s['title']) for s in secs if s['title']}
    seen_hashes = {}
    total_xml = 0
    flags = []  # (severity, title, msg)
    rows = []

    for i, s in enumerate(secs):
        content = s['content']
        clen = len(content.strip())
        total_xml += clen
        exp = expected_len(secs, i, pages, offset, len(pages))
        ratio = (clen / exp) if exp and exp > 50 else None

        issues = []
        # пустые
        if clen < 50:
            issues.append('EMPTY')
        # обрезано
        elif ratio is not None and ratio < 0.30 and exp - clen > 1500:
            issues.append(f'TRUNCATED({ratio:.2f},exp~{exp})')
        # раздуто / слиплось
        elif ratio is not None and ratio > 3.0 and clen - (exp or 0) > 3000:
            issues.append(f'BLOATED({ratio:.2f},exp~{exp})')
        # утечка границы: первая непустая строка == чужой заголовок
        first_line = norm(next((l for l in content.splitlines() if l.strip()), ''))
        if first_line and first_line in title_norms and first_line != norm(s['title']):
            issues.append(f'LEAK(starts="{first_line[:40]}")')
        # CJK
        cjk = len(CJK_RE.findall(content))
        if cjk:
            issues.append(f'CJK:{cjk}')
        # дубль контента
        if clen > 200:
            h = hashlib.md5(norm(content)[:400].encode()).hexdigest()
            if h in seen_hashes:
                issues.append(f'DUP(of "{seen_hashes[h][:35]}")')
            else:
                seen_hashes[h] = s['title']

        rows.append({'i': i, 'title': s['title'], 'page': s['page'], 'conf': s['conf'],
                     'strat': s['strat'], 'clen': clen, 'exp': exp, 'ratio': ratio,
                     'issues': issues})
        if issues:
            sev = 2 if any(x.startswith(('EMPTY', 'LEAK', 'DUP', 'TRUNC')) for x in issues) else 1
            flags.append((sev, s['title'], issues))

    coverage = total_xml / total_pdf if total_pdf else 0
    summary = {
        'book': pdf_path.name, 'pdf_pages': len(pages), 'sections': len(secs),
        'offset': offset, 'offset_agree': agree,
        'total_pdf': total_pdf, 'total_xml': total_xml, 'coverage': round(coverage, 3),
        'n_empty': sum(1 for r in rows if 'EMPTY' in str(r['issues'])),
        'n_trunc': sum(1 for r in rows if 'TRUNCATED' in str(r['issues'])),
        'n_bloat': sum(1 for r in rows if 'BLOATED' in str(r['issues'])),
        'n_leak': sum(1 for r in rows if 'LEAK' in str(r['issues'])),
        'n_dup': sum(1 for r in rows if 'DUP' in str(r['issues'])),
        'n_cjk': sum(1 for r in rows if 'CJK' in str(r['issues'])),
    }
    return summary, rows

def match_pairs(substr=None):
    pairs = []
    for xml in sorted(XML_DIR.glob('*.xml')):
        stem = xml.stem.replace('_report', '')
        pdf = PDF_DIR / (stem + '.pdf')
        if not pdf.exists():
            # попытка по началу имени
            cand = list(PDF_DIR.glob(stem[:20] + '*.pdf'))
            pdf = cand[0] if cand else None
        if pdf and (not substr or substr.lower() in xml.name.lower()):
            pairs.append((pdf, xml))
    return pairs

def main():
    substr = sys.argv[1] if len(sys.argv) > 1 else None
    pairs = match_pairs(substr)
    if not pairs:
        print("Нет пар PDF/XML")
        return
    detailed = substr is not None

    print(f"{'book':<46}{'pg':>4}{'sec':>4}{'off':>4}{'agr':>5}{'cov':>6}{'mt':>4}{'tr':>4}{'bl':>4}{'lk':>4}{'dp':>4}{'cj':>4}")
    print('-'*92)
    all_rows = {}
    for pdf, xml in pairs:
        try:
            summ, rows = audit_book(pdf, xml, detailed)
        except Exception as e:
            print(f"ERR {xml.name[:40]}: {e}")
            continue
        all_rows[summ['book']] = (summ, rows)
        print(f"{summ['book'][:45]:<46}{summ['pdf_pages']:>4}{summ['sections']:>4}"
              f"{summ['offset']:>4}{summ['offset_agree']:>5}{summ['coverage']:>6}"
              f"{summ['n_empty']:>4}{summ['n_trunc']:>4}{summ['n_bloat']:>4}"
              f"{summ['n_leak']:>4}{summ['n_dup']:>4}{summ['n_cjk']:>4}")

    if detailed:
        for book, (summ, rows) in all_rows.items():
            print(f"\n{'='*92}\nДЕТАЛИ: {book}")
            print(f"offset={summ['offset']} (согласие якорей {summ['offset_agree']}), coverage={summ['coverage']}")
            print(f"{'#':>3} {'conf':>5} {'clen':>7} {'exp':>7} {'ratio':>6}  title / issues")
            for r in rows:
                rt = f"{r['ratio']:.2f}" if r['ratio'] is not None else "  - "
                mark = '  ' if not r['issues'] else '!!'
                print(f"{mark}{r['i']:>3} {r['conf']:>5.2f} {r['clen']:>7} "
                      f"{(r['exp'] or 0):>7} {rt:>6}  {r['title'][:50]}")
                if r['issues']:
                    print(f"        -> {', '.join(r['issues'])}")

if __name__ == '__main__':
    main()
