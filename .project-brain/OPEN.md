# OPEN ITEMS — Book Analyzer
_Last updated: 2026-05-28 session 001_

## In progress
- [ ] **Release It! regression** — started session 001, investigation paused.
  7 sections (pages 13–24) have conf=0.00. Titles exist in body text.
  Hypothesis: page-distance reverts exact-match in ToC zone, rescue window for pages 13–24 is wrong.
  Next step: trace mapping logs for these 7 sections — add debug print to see which verify check fires.

## TODO
- [ ] **Page-cut fallback** — for sections where title not found in text (large typography headers),
  fall back to slicing by page-hint instead of by title match.
  Affects: Иглмен (6 sections), Do Good Design (2 sections).
  Context: architectural decision needed — how to merge page-cut results with text-search results.
- [ ] **Fix PIPELINE_DIFF.md note** — says "LLM clean is sequential", actually uses asyncio.gather.
  Low priority, documentation only.
- [ ] **Verify parallelnoe result** — 248 sections, ~160 with content, haven't checked quality of content.

## Open bugs
- [ ] `bug_2026-05-28_release-it-regression.md` — Release It! 7 conf=0.00 sections (regression this session)
- [ ] `bug_2026-05-28_do-good-design-copyright-spam.md` — "Об авторе" captures copyright/legal text

## Open questions / decisions needed
- [ ] **Page-cut vs title-search** — when to use page-cut fallback?
  Option A: only when title literally absent from text (current "not_found")
  Option B: always as secondary check when confidence < threshold
  Decision needed before implementing page-cut fallback.
- [ ] **scrub_foreign_script threshold** — currently removes ALL CJK/Arabic.
  Edge case: books with intentional CJK quotes/terminology. Acceptable loss?

## Deferred (not forgotten)
- [ ] **0e6e53b LLM-clean speed** — 14 low-conf sections → each goes through LLM clean (14 × ~35K chars).
  Already parallel via asyncio.gather but still slow. Deferred: acceptable quality over speed for now.
- [ ] **toc_validation metric reliability** — coverage_pct is noisy when heuristic ToC is perfect
  (OCR of ToC pages doesn't match heuristic format). Deferred: metric is informational only for now.
- [ ] **Context length guard** — pipeline should warn/fail early if LM Studio loaded with ctx < 8192.
  Deferred: workaround is manual pre-load with -c 8192.
