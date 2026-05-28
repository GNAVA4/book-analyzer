# Session 002 — 2026-05-28
← [session_001.md](./session_001.md)

## Goal
Re-run Иглмен to confirm page_cut fixes its 6 not-found large-typography sections.

## What actually happened
Ran pipeline → got 20/22 (not 22/22 as expected). Investigated: 2 sections still at conf=0.00
despite having page numbers. Traced to page_cut placement bug: page_cut ran BEFORE the final
_verify_and_correct_order. Sections found by rescue (embedding), set to start_idx != -1, then
reverted by final verify as out-of-order → stuck at conf=0.00 because page_cut already ran.
Also discovered two uvicorn servers were running simultaneously (old Python 3.11 + new venv).
Fixed page_cut ordering → re-ran → 22/22.

## Decisions made
| Decision | Options considered | Chosen because |
|----------|--------------------|----------------|
| Move page_cut after final verify | Keep before (old), move after | Sections reverted by final verify need page_cut too |

## What we tried that didn't work
- ❌ Assumed fix applied on first re-run — wrong. Old Python 3.11 uvicorn server was still
  running and receiving requests. Always verify with `Get-CimInstance Win32_Process` that only
  ONE uvicorn is running before testing a fix.

## What works now (that didn't before)
- Иглмен: 22/22 sections with content (was 20/22 after first page_cut fix in session 001)
- page_cut now correctly catches sections reverted by final verify as out-of-order

## Files changed
- `app/services/mapping_pipeline.py` — moved page_cut block after final _verify_and_correct_order

## Bugs found
_(none new)_

## Insights found
- `insights/insight_2026-05-28_page-cut-must-run-after-final-verify.md`

## End state
Branch `llm-toc-fallback`.
All test books: 0 not_found after fix (Иглмен 22/22, Release It! 31/34, 0e6e53b 34/35).
No active P0 issues. Open: Do Good Design copyright spam (low priority), parallelnoe quality check.
