# Session 001 — 2026-05-28

_First session tracked in .project-brain_

## Goal
Fix P0 bugs from analysis of 11 test books:
1. OCR failure on `0e6e53b` (always returned empty XML)
2. Sticky ToC lines in OCR output (Иглмен)
3. CJK contamination in content
4. Duplicate sections in NavigationTable (Массель)
Enable all pipeline flags by default. Run all test books. Document results.

## What actually happened
Started with OCR crash diagnosis → found cp1251/U+FFFD root cause → fixed → re-ran 0e6e53b (35 sections).
Then worked through remaining improvements: CJK scrub, sticky lines, exact_normalized, dedup, heuristic trim.
Added toc_validator.py, safety net, WebSocket double-close fix, scripts/run_pipeline.py.
Enabled all flags by default.
Discovered Release It! regression during final analysis — 7 sections conf=0.00 (were working before).
Session ended with regression unresolved and documentation/session files being created.

## Decisions made
| Decision | Options considered | Chosen because |
|----------|--------------------|----------------|
| scrub_foreign_script on all paths | Only in LLM path | OCR artifacts reach fast_clean_chunk too |
| dedup key = title only | title+page (old) | Duplicate entries survive on slightly different pages |
| all flags ON by default | opt-in (old) | User preference: full capability always |
| exact_normalized → conf=1.0 | Keep at 0.85 (tokenized) | Whitespace-only diff is a reliable match |

## What we tried that didn't work
- Initial hypothesis: OCR skipping pages with >300 chars → rejected by user ("300 символов это много для пустой страницы")
- Initial hypothesis: LLM clean is sequential → WRONG, it uses asyncio.gather (parallel already)

## What works now (that didn't before)
- `0e6e53b`: 35 sections, 34 with content, 0 CJK chars (was: empty XML)
- Массель: avg_conf=1.0, no duplicates (was: avg=0.85, duplicate "ПРИНЦИПЫ")
- Иглмен: 22 sections (was: ~14 sticky merged sections)
- WebSocket no longer crashes on client disconnect
- All OCR print calls safe on Windows cp1251

## Files changed
- `app/main.py` — stdout.reconfigure for cp1251 safety
- `app/api.py` — WebSocket double-close fix, validate_toc_ocr flag, all flags default True
- `app/services/ocr_engine.py` — _safe_print, per-page timeout, empty page skip
- `app/services/toc_builder.py` — _split_sticky_toc_lines, dedup on all paths, title-only key
- `app/services/toc_parser.py` — _trim_description_after_section_name, _COMPLETE_SECTION_NAMES
- `app/services/toc_validator.py` — NEW: OCR validation of extracted ToC
- `app/services/mapping_pipeline.py` — exact_normalized in page-distance whitelist
- `app/services/pdf_utils.py` — exact_normalized promotion logic
- `app/services/llm_engine.py` — scrub_foreign_script() public function, CJK-aware retry
- `app/services/pdf_parser_neural.py` — scrub on all paths, validate_toc_ocr, safety net, all flags default
- `static/index.html` — all checkboxes checked by default, validate_toc_ocr added
- `scripts/run_pipeline.py` — NEW: E2E test runner
- `.gitignore` — added xml/
- `PIPELINE_DIFF.md` — NEW: comparison early vs current implementation
- `SESSION_DIFF.md` — NEW: this session's changes (comprehensive)
- Tests: test_toc_builder, test_toc_validator, test_mapping_pipeline, test_pdf_utils, test_llm_engine

## Bugs found
- `bugs/bug_2026-05-28_release-it-regression.md` — Release It! 7 conf=0.00 sections
- `bugs/bug_2026-05-28_do-good-design-copyright-spam.md` — "Об авторе" captures legal text

## Insights found
- `insights/insight_2026-05-28_ocr-cp1251-crash.md` — Windows cp1251 + U+FFFD = silent OCR failure
- `insights/insight_2026-05-28_cjk-in-fast-clean-path.md` — CJK reaches XML via fast_clean path
- `insights/insight_2026-05-28_dedup-key-must-be-title-only.md` — title+page key causes duplicates

## End state
Branch `llm-toc-fallback`, 4 commits ahead of origin.
All 11 test books processable without crash.
Release It! has 7 empty sections — open regression, next investigation step: trace which verify check fires.
All other books: same or better than before session.
.project-brain/ initialized this session.
