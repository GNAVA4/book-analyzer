"""
Лёгкое измерение СМЕЩЕНИЯ контента (Класс 2): прогоняет build_toc + map_sequence +
воспроизводит нарезку контента, но БЕЗ дорогого LLM-clean шага. Показывает длину
сырого среза каждой секции vs ожидаемый объём по диапазону страниц → ratio.

Цель: увидеть, кто кого поглощает (BLOATED) и кто пуст (EMPTY/TRUNCATED), уже на
свежем маппинге (после фикса ToC). Нужен LM Studio (rescue в map_sequence).

Usage: venv/Scripts/python.exe scripts/mapping_audit.py <book_substring>
"""
import sys, os, asyncio, statistics, re
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import fitz, pathlib
from app.services.pdf_utils import get_all_text, clean_footer_header
from app.services.toc_builder import build_toc
from app.services.mapping_pipeline import map_sequence, _estimate_position_from_page, PAGE_DISTANCE_TOLERANCE_RATIO

PDF_DIR = pathlib.Path(__file__).resolve().parent.parent / "test"


def norm(s):
    s = (s or "").lower()
    return re.sub(r'\s+', ' ', re.sub(r'[^\wЀ-ӿ]+', ' ', s)).strip()


def title_key(t):
    t = re.sub(r'^\s*(глава|часть|chapter|part)?\s*\d+[.\)]?\s*', '', t or '', flags=re.I)
    return norm(t)


def estimate_offset(mapped, pages_norm):
    deltas = []
    for m in mapped:
        tp = m['item'].get('page')
        if not isinstance(tp, int):
            continue
        key = title_key(m['item'].get('title', ''))
        if len(key) < 8:
            continue
        cand = [i for i in range(len(pages_norm)) if key in pages_norm[i]]
        if cand:
            deltas.append(min(cand, key=lambda i: abs(i - tp)) - tp)
    return int(round(statistics.median(deltas))) if deltas else 0


async def main():
    substr = sys.argv[1]
    pdf = [p for p in PDF_DIR.glob("*.pdf") if substr.lower() in p.name.lower()][0]
    doc = fitz.open(pdf)
    pages = [doc[i].get_text() for i in range(len(doc))]
    pages_norm = [norm(p) for p in pages]

    full_text = clean_footer_header(get_all_text(doc))

    seq, source, _ = await build_toc(
        doc, lambda: full_text, enable_ocr=False, enable_deep_scan=False, enable_llm_expand=True
    )
    print(f"{pdf.name}\nToC source={source}  sections={len(seq)}")
    mapped = await map_sequence(seq, full_text, len(doc))
    doc.close()

    offset = estimate_offset(mapped, pages_norm)

    def expected_len(idx):
        tp = mapped[idx]['item'].get('page')
        if not isinstance(tp, int):
            return None
        start = max(0, min(tp + offset, len(pages)))
        nxt = None
        for j in range(idx + 1, len(mapped)):
            np_ = mapped[j]['item'].get('page')
            if isinstance(np_, int) and np_ >= tp:
                nxt = max(start, min(np_ + offset, len(pages))); break
        if nxt is None:
            nxt = len(pages)
        return sum(len(pages[i]) for i in range(start, nxt))

    ftl = len(full_text)
    tol = max(int(ftl * PAGE_DISTANCE_TOLERANCE_RATIO), 30_000)
    print(f"offset={offset}  full_text_len={ftl}  page_dist_tolerance={tol}")
    print(f"{'#':>3} {'conf':>5} {'start':>8} {'est':>8} {'dist':>8} {'rawlen':>7} {'exp':>7} {'ratio':>6} strat / title")
    for i, m in enumerate(mapped):
        page = m['item'].get('page')
        est = _estimate_position_from_page(page, len(pages), ftl) if isinstance(page, int) else -1
        if m['start_idx'] == -1:
            print(f"{i:>3} {m['confidence']:>5.2f} {'-1':>8} {est:>8} {'--':>8} {'--':>7} {'--':>7} {' NF':>6} {m.get('match_strategy',''):<14} {m['item'].get('title','')[:38]}")
            continue
        dist = abs(m['start_idx'] - est) if est >= 0 else -1
        start = m['end_idx']
        end = min((x['start_idx'] for x in mapped if x['start_idx'] > start), default=ftl)
        rawlen = len(full_text[start:end].strip())
        exp = expected_len(i)
        ratio = (rawlen / exp) if exp and exp > 50 else None
        flag = ''
        if rawlen < 50:
            flag = 'EMPTY'
        elif ratio is not None and ratio < 0.3 and (exp - rawlen) > 1500:
            flag = 'TRUNC'
        elif ratio is not None and ratio > 3.0 and (rawlen - (exp or 0)) > 3000:
            flag = 'BLOAT'
        far = 'FAR' if (dist >= 0 and dist > tol) else ''
        rt = f"{ratio:.2f}" if ratio is not None else "  -"
        mark = '!!' if flag else '  '
        print(f"{mark}{i:>3} {m['confidence']:>5.2f} {m['start_idx']:>8} {est:>8} {dist:>8} {rawlen:>7} {(exp or 0):>7} {rt:>6} {m.get('match_strategy','')[:13]:<14}{m['item'].get('title','')[:34]} {flag}{far}")


if __name__ == "__main__":
    asyncio.run(main())
