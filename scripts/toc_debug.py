"""
Диагностика умного fallback: показывает КАЖДОГО кандидата ToC и его валидацию,
чтобы понять, почему выбран тот или иной источник.

Usage: venv/Scripts/python.exe scripts/toc_debug.py <book_substring>
"""
import sys, os, asyncio
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import fitz, pathlib
from app.services.toc_builder import (
    _heuristic, _split_sticky_toc_lines, _llm_from_text_retry,
    _dedup_and_order, _count_toc_entry_lines,
)
from app.services.toc_validate import score_toc, is_valid

PDF_DIR = pathlib.Path(__file__).resolve().parent.parent / "test"


async def show(name, seq, full_text, rec):
    sc = await score_toc(seq, full_text, rec)
    print(f"\n[{name}] items={len(seq)} L2+={sum(1 for s in seq if s.get('level',1)>1)} "
          f"grounding={sc['grounding']} cjk={len(sc['cjk_items'])} "
          f"formal={sc['formal_issues'][:4]} valid={is_valid(sc)}")
    return sc


async def main():
    substr = sys.argv[1]
    pdf = [p for p in PDF_DIR.glob("*.pdf") if substr.lower() in p.name.lower()][0]
    doc = fitz.open(pdf)
    raw_text = "".join(doc[i].get_text() + "\n" for i in range(min(20, len(doc))))
    full_text = "".join(doc[i].get_text() for i in range(len(doc)))
    doc.close()

    rec = _count_toc_entry_lines(raw_text)
    print(f"{pdf.name}\nraw_entry_count={rec}")

    heur = _dedup_and_order(_heuristic(_split_sticky_toc_lines(raw_text)))
    await show("heuristic", heur, full_text, rec)

    llm = _dedup_and_order(await _llm_from_text_retry(raw_text))
    await show("llm", llm, full_text, rec)

    # покажем первые/последние пункты LLM-кандидата
    if llm:
        print("\n  LLM first 8 / last 4:")
        for s in llm[:8]:
            print(f"    L{s.get('level')} p{str(s.get('page')):>5} {s.get('title','')[:50]}")
        print("    ...")
        for s in llm[-4:]:
            print(f"    L{s.get('level')} p{str(s.get('page')):>5} {s.get('title','')[:50]}")


if __name__ == "__main__":
    asyncio.run(main())
