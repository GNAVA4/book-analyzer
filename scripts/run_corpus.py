"""
Полный прогон всех книг из test/ через parse_pdf_neural НАПРЯМУЮ (без uvicorn —
обходит landmine «два сервера на :8000»). Пишет <stem>.xml + <stem>_report.json
в OUT_DIR (по умолчанию tests_v3). Отчёт берёт контент из flat_nodes напрямую,
поэтому content_len/preview корректны.

Usage:
    OUT_DIR=tests_v3 venv/Scripts/python.exe scripts/run_corpus.py [<substr> ...]
    (без аргументов — все PDF из test/)
"""
import sys, os, json, asyncio, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

import pathlib
from app.services.pdf_parser_neural import parse_pdf_neural
from app.services.xml_builder import build_tree_structure, dict_to_xml

ROOT = pathlib.Path(__file__).resolve().parent.parent
PDF_DIR = ROOT / "test"
OUT_DIR = ROOT / os.environ.get("OUT_DIR", "tests_v3")


def _effective_real_count(flat_nodes):
    """Count sections that have content >100 chars themselves OR have at least one
    descendant L>parent with content >100. Honest reporting for hierarchical books
    where an L1 chapter shell legitimately has no content of its own (1332 case)
    because everything lives in L2 subsections.
    """
    n = len(flat_nodes)
    has_own = [bool(node.get("content")) and len(node["content"]) > 100 for node in flat_nodes]
    covered = list(has_own)
    for i, node in enumerate(flat_nodes):
        if covered[i]:
            continue
        lvl = node.get("level", 1)
        for j in range(i + 1, n):
            jlvl = flat_nodes[j].get("level", 1)
            if jlvl <= lvl:
                break  # next sibling/parent — descendants window ended
            if has_own[j]:
                covered[i] = True
                break
    return sum(covered)


def build_stats(flat_nodes, meta):
    def sc(prefix):
        return sum(1 for n in flat_nodes if (n.get("match_strategy") or "").startswith(prefix))
    return {
        "total_sections": len(flat_nodes),
        "real_content_sections": sum(1 for n in flat_nodes if n.get("content") and len(n["content"]) > 100),
        "effective_real_sections": _effective_real_count(flat_nodes),
        "avg_confidence": round(sum(n.get("confidence", 1.0) for n in flat_nodes) / max(len(flat_nodes), 1), 3),
        "low_confidence_sections": sum(1 for n in flat_nodes if n.get("confidence", 1.0) < 0.80),
        "rescued_page_hint": sc("page_hint"),
        "rescued_embedding": sc("embedding_rescue"),
        "rescued_llm": sc("llm_rescue"),
        "page_cut": sc("page_cut"),
        "reverted_cluster": sc("reverted_cluster"),
        "reverted_in_toc": sc("reverted_in_toc"),
        "reverted_out_of_order": sc("reverted_out_of_order"),
        "toc_source": meta.get("toc_source", "unknown"),
        "deep_scan_used": meta.get("deep_scan_used", False),
        "ocr_used": meta.get("ocr_used", False),
        "toc_validation": meta.get("toc_validation"),
    }


async def process_one(pdf: pathlib.Path):
    t0 = time.time()
    last = [-1]

    async def progress(pct, msg):
        if pct != last[0]:
            print(f"    [{pct:3d}%] {str(msg)[:70]}")
            last[0] = pct

    flat_nodes, toc_seq, meta = await parse_pdf_neural(
        str(pdf), progress_callback=progress,
        deep_scan=True, use_ocr=True, llm_expand=True, validate_toc_ocr=True,
    )
    stats = build_stats(flat_nodes, meta)
    xml = dict_to_xml(build_tree_structure(flat_nodes), toc_items=toc_seq)

    OUT_DIR.mkdir(exist_ok=True)
    stem = pdf.stem
    (OUT_DIR / (stem + ".xml")).write_text(xml, encoding="utf-8")
    report = {
        "file": pdf.name,
        "pipeline_flags": {"deep_scan": True, "use_ocr": True, "llm_expand": True, "validate_toc_ocr": True},
        "stats": stats,
        "sections": [{
            "title": n.get("title", ""),
            "page": n.get("page", ""),
            "confidence": n.get("confidence", 0),
            "match_strategy": n.get("match_strategy", ""),
            "level": n.get("level", 1),
            "content_len": len((n.get("content") or "").strip()),
            "content_preview": (n.get("content") or "").strip()[:200],
        } for n in flat_nodes],
    }
    (OUT_DIR / (stem + "_report.json")).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    dt = time.time() - t0
    print(f"  OK {stem[:40]}: sections={stats['total_sections']} real={stats['real_content_sections']} "
          f"eff={stats['effective_real_sections']} "
          f"toc={stats['toc_source']} ocr={stats['ocr_used']} ({dt:.0f}s)")
    return {"file": pdf.name, "stats": stats, "sec": dt}


async def main(substrs):
    pdfs = sorted(PDF_DIR.glob("*.pdf"))
    if substrs:
        pdfs = [p for p in pdfs if any(s.lower() in p.name.lower() for s in substrs)]
    print(f"OUT_DIR={OUT_DIR}  books={len(pdfs)}")
    results = []
    for pdf in pdfs:
        print(f"\n=== {pdf.name} ===")
        try:
            results.append(await process_one(pdf))
        except Exception as e:
            import traceback; traceback.print_exc()
            print(f"  FAIL {pdf.name}: {e}")
            results.append({"file": pdf.name, "error": str(e)})

    print("\n=== SUMMARY ===")
    for r in results:
        if "error" in r:
            print(f"  FAIL {r['file'][:45]}: {r['error'][:60]}")
        else:
            s = r["stats"]
            print(f"  {r['file'][:45]:<46} sec={s['total_sections']:>4} real={s['real_content_sections']:>4} "
                  f"eff={s['effective_real_sections']:>4} "
                  f"toc={s['toc_source']:<12} cluster={s['reverted_cluster']} pcut={s['page_cut']} ({r['sec']:.0f}s)")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
