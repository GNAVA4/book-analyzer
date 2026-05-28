# PROJECT CONTEXT — Book Analyzer
_Last updated: 2026-05-29 session 004_

## What this project is
PDF/DOCX/TXT book parser: extracts Table of Contents and section content into structured XML files.
Neural pipeline uses a multi-tier cascade with LLM, OCR, and embeddings as fallbacks — all running locally via LM Studio.

## Stack
Python 3.12 + FastAPI + WebSocket + PyMuPDF (fitz) + LM Studio (local API at 127.0.0.1:1234)
Models: `qwen2.5-7b-instruct` (LLM, ctx=8192), `glm-ocr` (vision OCR), `text-embedding-qwen3-embedding-0.6b` (embeddings)
Frontend: single-page `static/index.html` (vanilla JS + dark theme)
Server runs via venv: `c:\book analyzer\venv\Scripts\python.exe -m uvicorn app.main:app`

## Current state — full run results (session 003, all 11 books → test_v2/)

| Книга | Секций | Real | avg_conf | ToC source | OCR |
|-------|--------|------|----------|------------|-----|
| 0e6e53b (OCR) | 35 | **35** | 0.853 | ocr_llm | ✅ |
| Виды интерфейсов | 15 | **15** | 1.000 | heuristic | — |
| Клейнман | 40 | **40** | 1.000 | heuristic | — |
| Иглмен | 22 | **22** | 0.694 | llm | — |
| Кениг | 20 | 19 | 0.935 | llm | — |
| Do Good Design | 18 | 16 | 0.726 | heuristic | — |
| Release It! | 34 | 31 | 0.716 | heuristic | — |
| Розенсон | 20 | 16 | 0.833 | heuristic | — |
| Массель | 23 | 20 | 0.996 | heuristic | — |
| parallelnoe | 248 | 244 | 0.751 | heuristic | — |
| MIL-STD-1472G (EN) | 747 | 593 | 0.986 | heuristic | — |

Results saved to `test_v2/` — each book has `.xml` + `_report.json` (stats + per-section breakdown).
Content quality NOT yet audited — next session task.

## Active work (session 004)
Content-quality audit DONE → 3 defect classes. ToC (class 1) FIXED. NEXT: class 2 content misplacement.
- **Mechanism A (ToC dual-ToC) FIXED** in toc_parser.py: Release It! ToC 34→153.
- **Smart LLM/OCR ToC fallback DONE & live-verified** (ADR 003): incomplete heuristic (algorithmic
  trigger, no models) → LLM extract w/ retry → validate (CJK+formal+grounding≥0.8) → pick best valid.
  Розенсон 20→77 (llm), Клейнман 41→57 (llm); complete books untouched (no model calls); 260 tests pass.
- **NEXT: Class 2 content misplacement** in mapping_pipeline (bug part-divider-absorbs-chapter-body).

## Audit tooling (session 004, scripts/)
- `audit_content.py` — per-section ratio = XML content_len / PDF page-range text len (offset-anchored).
  Flags EMPTY/TRUNCATED/BLOATED/LEAK/DUP. NOTE: coverage stays high even with bad misplacement —
  trust per-section RATIO, not coverage. fitz-vs-fitz is blind to scans (use vision for those).
- `fitz_blindness.py` — per-page map of low-text+graphics pages (SCAN/SPARSE). Most flagged pages
  are legit full-page illustrations, NOT lost text (verified by vision).
- `render_pages.py` — render PDF pages → .audit_pages/*.png for vision ground-truth.
- `toc_regression_check.py` — heuristic ToC item count vs old NavigationTable (offline regression).

## Decisions that affect ALL code
- **CONFIDENCE_THRESHOLD=0.85**: above → `fast_clean_chunk` (algo), below → LLM boundary+clean
- **scrub_foreign_script on all paths**: CJK scrub applied after EVERY section regardless of confidence path — never skip
- **LM Studio ctx=8192**: must be started with `-c 8192`, default 4096 causes "Context size exceeded"
- **All flags ON by default**: `deep_scan=True, use_ocr=True, llm_expand=True, validate_toc_ocr=True`
- **PAGE_DISTANCE_TOLERANCE_RATIO=0.30**: match > 30% of text from expected page position → suspect
- **LLM clean IS parallel**: `process_large_text` uses `asyncio.gather` — do not claim it's sequential
- **page_cut confidence=0.30**: approximate position from page number. Bypasses fix_boundary, uses fast_clean_chunk.
- **page_cut excluded from running_max**: approximate positions must not anchor ordering logic.
- **page_cut runs AFTER final verify**: must be LAST step in map_sequence. See insight.
- **ToC heuristic: don't break on page-less «Часть/Part/Раздел»** (Mechanism A): in dual-ToC books
  (краткое+детальное) the divider recurs inside the detailed ToC; breaking there drops all later subsections.
- **ToC heuristic length-guard = 250**: lines >250 chars skip item_pattern regex (catastrophic
  backtracking hung Клейнман when parser read into body). Real ToC items are <250.
- **ToC incompleteness trigger is algorithmic (no models)**: raw ToC entry-lines / heuristic items
  > 1.5 ⇒ incomplete ⇒ run smart LLM/OCR fallback. Books below threshold are NOT touched.
- **Smart ToC fallback validates against hallucination**: CJK + formal + embedding grounding≥0.8;
  never ship unvalidated LLM ToC. See ADR 003.

## Known landmines ⚠️
- **GPU max 90%**: at 100% user's display disappears — never load all models simultaneously
- **cp1251 console**: `print()` with U+FFFD crashes on Windows — always use `_safe_print()`
- **LM Studio context**: must pre-load with `-c 8192`
- **_restored_after_rescue_fail**: can restore false positive exact-matches (Do Good Design)
- **Two uvicorn servers RECURRING ISSUE**: after any restart, old Python 3.11 instance may linger on port 8000 and silently serve stale code. ALWAYS verify with `Get-CimInstance Win32_Process -Filter "Name='python.exe'"` before testing. Kill by PID explicitly.
- **Scripts run with venv Python**: system Python 3.14 doesn't have project deps. Always use `venv/Scripts/python.exe`
- **Background bash task uses snapshot of script**: if run_pipeline.py is edited while a bash background task is already running it, the running process uses the old version (Python reads the file once at startup)
- **dedup key = title only**: acceptable tradeoff, see insight
- **toc_validation coverage=0.05 is noisy**: Клейнман case — treat as informational only

## File map (key files)
- `app/api.py` — HTTP POST /upload + WebSocket /ws/analyze
- `app/services/pdf_parser_neural.py` — main coordinator, thin
- `app/services/toc_builder.py` — 6-level ToC cascade
- `app/services/mapping_pipeline.py` — 5-level mapping + 2× verify + page_cut last
- `app/services/toc_parser.py` — HeuristicParser
- `app/services/toc_validator.py` — OCR validation of ToC
- `app/services/pdf_utils.py` — find_real_indices, fast_clean_chunk, readability check
- `app/services/llm_engine.py` — LLM client + scrub_foreign_script
- `app/services/ocr_engine.py` — glm-ocr client with _safe_print
- `app/services/embedding_engine.py` — embedding client
- `app/services/xml_builder.py` — XML output builder
- `app/services/toc_validate.py` — NEW: ToC candidate validation/scoring (CJK, formal, grounding)
- `scripts/run_pipeline.py` — E2E test runner; OUT_DIR env var; saves .xml + _report.json
- `scripts/analyze_xml.py` — standalone XML quality checker (CJK, ToC-like content, short sections)
- `scripts/audit_content.py` — XML↔PDF per-section ratio audit (EMPTY/TRUNCATED/BLOATED/LEAK/DUP)
- `scripts/fitz_blindness.py` — per-page fitz-blindness map (SCAN/SPARSE)
- `scripts/render_pages.py` — render PDF pages → .audit_pages/*.png (vision ground-truth)
- `scripts/toc_regression_check.py` — heuristic ToC count vs old nav (offline regression)
- `test_v2/` — baseline run: 11 books × (xml + _report.json)
- `static/index.html` — frontend
- `tests/` — pytest suite 200+ tests
