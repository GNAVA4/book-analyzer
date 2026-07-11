"""
Live-проверка build_toc на реальной книге (нужен LM Studio для неполных книг).

Usage:
    venv/Scripts/python.exe scripts/toc_live_check.py <book_substring> [--no-ocr]

Для ПОЛНЫХ книг (Виды UI, parallelnoe) build_toc возвращает heuristic рано,
БЕЗ вызова моделей. Для неполных (Розенсон, Клейнман) вызывается LLM (+эмбеддер).
"""
import sys, os, asyncio
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import fitz, pathlib
from app.services.toc_builder import build_toc
from app.services.toc_validate import score_toc

PDF_DIR = pathlib.Path(__file__).resolve().parent.parent / "test"


async def main():
    substr = sys.argv[1]
    enable_ocr = "--no-ocr" not in sys.argv
    pdf = [p for p in PDF_DIR.glob("*.pdf") if substr.lower() in p.name.lower()][0]
    doc = fitz.open(pdf)
    full_text = "".join(doc[i].get_text() for i in range(len(doc)))

    def extractor():
        return full_text

    seq, source, _ = await build_toc(
        doc, extractor, enable_ocr=enable_ocr, enable_deep_scan=False, enable_llm_expand=True
    )
    doc.close()

    print(f"\nКнига: {pdf.name}")
    print(f"source = {source}   items = {len(seq)}   L2+ = {sum(1 for s in seq if s.get('level',1)>1)}")
    sc = await score_toc(seq, full_text, embed=False)
    print(f"grounding(lex) = {sc['grounding']}  cjk = {len(sc['cjk_items'])}  formal = {sc['formal_issues'][:3]}")
    print("--- first 6 / last 6 ---")
    for s in seq[:6]:
        print(f"  L{s.get('level')} p{str(s.get('page')):>5} {s.get('title','')[:50]}")
    if len(seq) > 12:
        print("  ...")
        for s in seq[-6:]:
            print(f"  L{s.get('level')} p{str(s.get('page')):>5} {s.get('title','')[:50]}")


if __name__ == "__main__":
    asyncio.run(main())
