# PROJECT CONTEXT — Book Analyzer
_Last updated: 2026-05-28 session 001_

## What this project is
PDF/DOCX/TXT book parser: extracts Table of Contents and section content into structured XML files.
Neural pipeline uses a multi-tier cascade with LLM, OCR, and embeddings as fallbacks — all running locally via LM Studio.

## Stack
Python 3.12 + FastAPI + WebSocket + PyMuPDF (fitz) + LM Studio (local API at 127.0.0.1:1234)
Models: `qwen2.5-7b-instruct` (LLM, ctx=8192), `glm-ocr` (vision OCR), `text-embedding-qwen3-embedding-0.6b` (embeddings)
Frontend: single-page `static/index.html` (vanilla JS + dark theme)

## Current state
- **0e6e53b** (OCR book): 35 sections, 34 with content, 0 CJK chars — fully working after cp1251 fix
- **Массель**: 23 sections, avg_conf=1.0, 0 not_found — perfect
- **Иглмен**: 22 sections (6 not found — large typography headers PyMuPDF can't extract)
- **Клейнман, Розенсон**: working normally
- **parallelnoe**: 248 sections, ~160 with content, ~50 reverted in appendices
- **Release It!**: ⚠️ REGRESSION — 34 sections but 7 conf=0.00 (pages 13–24, front matter)
- All pipeline flags ON by default: `deep_scan`, `use_ocr`, `llm_expand`, `validate_toc_ocr`

## Active work
Release It! regression: 7 sections (pages 13–24) have confidence=0.00 and empty content.
Titles: "Для кого предназначена…", "Структура книги", "Благодарности", "1.1.", "1.2.", "1.5.", "Часть I."
Likely cause: page-distance check reverts exact-match found in ToC zone, rescue then fails for pages 13–24.
Investigation started but not finished — interrupted by user request to document session.

## Decisions that affect ALL code
- **CONFIDENCE_THRESHOLD=0.85**: above → `fast_clean_chunk` (algo), below → LLM boundary+clean
- **scrub_foreign_script on all paths**: CJK scrub applied after EVERY section regardless of confidence path — never skip
- **LM Studio ctx=8192**: must be started with `-c 8192`, default 4096 causes "Context size exceeded"
- **All flags ON by default**: `deep_scan=True, use_ocr=True, llm_expand=True, validate_toc_ocr=True` — all features always run
- **PAGE_DISTANCE_TOLERANCE_RATIO=0.30**: match > 30% of text from expected page position → suspect
- **LLM clean IS parallel**: `process_large_text` uses `asyncio.gather` — do not claim it's sequential

## Known landmines ⚠️
- **GPU max 90%**: at 100% user's display disappears — never load all models simultaneously
- **cp1251 console**: `print()` with U+FFFD (OCR replacement char) crashes on Windows cp1251 — always use `_safe_print()` in ocr_engine or ensure stdout is reconfigured
- **LM Studio context**: JIT loads model with default 4096 ctx — must pre-load with 8192
- **_restored_after_rescue_fail**: can restore false positive exact-matches (e.g. copyright spam in Do Good Design)
- **Large typography headers**: PyMuPDF doesn't extract section titles rendered as images/large-font vectors — page-cut fallback not yet implemented
- **toc_validation coverage=0.05 is noisy**: OCR validation is unreliable when heuristic already found ToC perfectly (Клейнман case)
- **dedup key = title only**: removing page from key fixes "2.ПРИНЦИПЫ" vs "2.Принципы" but could merge legitimately same-titled sections at different pages (acceptable tradeoff)

## File map (key files)
- `app/api.py` — HTTP POST /upload + WebSocket /ws/analyze, progress events, double-close fix
- `app/services/pdf_parser_neural.py` — main coordinator (~265 lines), thin, delegates to cascades
- `app/services/toc_builder.py` — 6-level ToC cascade: heuristic→LLM→OCR+heuristic→OCR+LLM→deep_scan→LLM-expand
- `app/services/mapping_pipeline.py` — 5-level mapping: regex→page-hint→fuzzy→embedding→LLM rescue + 2× verify
- `app/services/toc_parser.py` — HeuristicParser (regex-based ToC extraction from raw text)
- `app/services/toc_validator.py` — OCR validation of extracted ToC (±5 pages, coverage_pct metric)
- `app/services/pdf_utils.py` — `find_real_indices`, `_search_with_confidence`, `fast_clean_chunk`, readability check
- `app/services/llm_engine.py` — LLM client: extract_toc_json, locate_section, fix_boundary, clean_text, scrub_foreign_script
- `app/services/ocr_engine.py` — glm-ocr client with _safe_print, per-page timeout, empty-page skip
- `app/services/embedding_engine.py` — embedding client for semantic section search
- `app/services/xml_builder.py` — builds XML output with NavigationTable + sections
- `scripts/run_pipeline.py` — E2E test: POST /upload + WebSocket, saves xml/<name>.xml
- `static/index.html` — frontend: dark theme, cancel button, pipeline progress stages
- `tests/` — pytest suite (no real LLM/OCR needed): 200+ tests passing
