"""
E2E прогон PDF через работающий /upload + /ws/analyze.

Сохраняет результат в xml/<basename>.xml и печатает stats + meta.

Usage:
    python scripts/run_pipeline.py <pdf_path> [<pdf_path> ...]
"""

import asyncio
import json
import os
import sys
from pathlib import Path

import httpx
import websockets

# Force stdout flushing — useful when running piped to a file
sys.stdout.reconfigure(encoding='utf-8', errors='replace', line_buffering=True)


API_BASE = "http://127.0.0.1:8000"
WS_URL = "ws://127.0.0.1:8000/ws/analyze"
OUT_DIR = Path(os.environ.get("OUT_DIR", "xml"))
# Включается через env: VALIDATE_TOC_OCR=1 python scripts/run_pipeline.py ...
VALIDATE_TOC_OCR = bool(int(os.environ.get("VALIDATE_TOC_OCR", "0")))


async def process_one(pdf_path: Path) -> dict:
    print(f"\n=== {pdf_path.name} ===")

    # 1) /upload
    async with httpx.AsyncClient(timeout=120.0) as client:
        with open(pdf_path, "rb") as f:
            r = await client.post(
                f"{API_BASE}/upload",
                files={"file": (pdf_path.name, f, "application/pdf")},
            )
        r.raise_for_status()
        up = r.json()
        temp_id = up["temp_id"]
        print(f"  uploaded → {temp_id}")

    # 2) /ws/analyze
    async with websockets.connect(WS_URL, max_size=64 * 1024 * 1024) as ws:
        await ws.send(json.dumps({
            "temp_id": temp_id,
            # Все флаги включены — полный набор возможностей pipeline'а.
            "deep_scan": True,
            "use_ocr": True,
            "llm_expand": True,
            "validate_toc_ocr": True,
        }))

        last_pct = -1
        result = None
        while True:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=600.0)
            except asyncio.TimeoutError:
                print("  TIMEOUT waiting for message")
                return {"error": "timeout", "file": pdf_path.name}
            msg = json.loads(raw)
            t = msg.get("type")
            if t == "progress":
                pct = msg.get("percent", 0)
                if pct != last_pct:
                    print(f"  [{pct:3d}%] {msg.get('message', '')[:80]}")
                    last_pct = pct
            elif t == "complete":
                result = msg
                break
            elif t == "error":
                print(f"  ERROR: {msg.get('message')}")
                return {"error": msg.get("message"), "file": pdf_path.name}

    if not result:
        return {"error": "no result", "file": pdf_path.name}

    OUT_DIR.mkdir(exist_ok=True)
    stem = pdf_path.stem

    # Save XML
    out_path = OUT_DIR / (stem + ".xml")
    out_path.write_text(result["xml"], encoding="utf-8")

    stats = result.get("stats", {})
    print(f"  saved → {out_path}")
    print(f"  stats: {json.dumps(stats, ensure_ascii=False)}")

    # Save companion report: stats + per-section breakdown parsed from XML
    import xml.etree.ElementTree as ET
    report = {
        "file": pdf_path.name,
        "pipeline_flags": {
            "deep_scan": True,
            "use_ocr": True,
            "llm_expand": True,
            "validate_toc_ocr": True,
        },
        "stats": stats,
        "sections": [],
    }
    try:
        root = ET.fromstring(result["xml"])
        for sec in root.findall(".//section"):
            # Контент лежит в дочернем <content>, а не в sec.text — иначе
            # content_len/preview всегда 0 (баг отчётов test_v2).
            c = sec.find("content")
            content = (c.text if c is not None else sec.text) or ""
            report["sections"].append({
                "title": sec.get("title", ""),
                "page": sec.get("page", ""),
                "confidence": float(sec.get("confidence", 0)),
                "match_strategy": sec.get("match_strategy", ""),
                "content_len": len(content.strip()),
                "content_preview": content.strip()[:200],
            })
    except Exception as e:
        report["xml_parse_error"] = str(e)

    report_path = OUT_DIR / (stem + "_report.json")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  report → {report_path}")

    return {"file": pdf_path.name, "stats": stats, "xml_path": str(out_path)}


async def main(paths: list[str]):
    results = []
    for p in paths:
        try:
            r = await process_one(Path(p))
            results.append(r)
        except Exception as e:
            print(f"  EXCEPTION: {e}")
            results.append({"error": str(e), "file": p})

    print("\n=== SUMMARY ===")
    for r in results:
        if "error" in r:
            print(f"  FAIL {r['file']}: {r['error']}")
        else:
            s = r.get("stats", {})
            print(
                f"  OK {r['file']}: "
                f"sections={s.get('total_sections', '?')} "
                f"real={s.get('real_content_sections', '?')} "
                f"low_conf={s.get('low_confidence_sections', '?')} "
                f"toc={s.get('toc_source', '?')} "
                f"ocr={s.get('ocr_used', '?')}"
            )


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: run_pipeline.py <pdf> [<pdf> ...]")
        sys.exit(1)
    asyncio.run(main(sys.argv[1:]))
