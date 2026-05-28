# OPEN ITEMS — Book Analyzer
_Last updated: 2026-05-28 session 001 (updated after page_cut fix)_

## In progress
_(nothing active)_

## TODO
- [ ] **Иглмен 6 not-found sections** — page_cut fallback is now implemented; re-run Иглмен
  to confirm it also fixes the 6 large-typography chapter headings there.
- [ ] **Fix PIPELINE_DIFF.md note** — says "LLM clean is sequential", actually uses asyncio.gather.
  Low priority, documentation only.
- [ ] **Verify parallelnoe result** — 248 sections, ~160 with content, haven't checked quality of content.

## Open bugs
- [ ] `bug_2026-05-28_do-good-design-copyright-spam.md` — "Об авторе" captures copyright/legal text

## Closed bugs (this session)
- [x] `bug_2026-05-28_release-it-regression.md` — FIXED. Root cause: large-typography headings absent
  from body text. Fix: page_cut fallback in mapping_pipeline.py. Result: 25→31 real_content_sections.

## Open questions / decisions needed
- [ ] **scrub_foreign_script threshold** — currently removes ALL CJK/Arabic.
  Edge case: books with intentional CJK quotes/terminology. Acceptable loss?

## Deferred (not forgotten)
- [ ] **0e6e53b LLM-clean speed** — 14 low-conf sections → each goes through LLM clean (14 × ~35K chars).
  Already parallel via asyncio.gather but still slow. Deferred: acceptable quality over speed for now.
- [ ] **toc_validation metric reliability** — coverage_pct is noisy when heuristic ToC is perfect.
  Deferred: metric is informational only for now.
- [ ] **Context length guard** — pipeline should warn/fail early if LM Studio loaded with ctx < 8192.
  Deferred: workaround is manual pre-load with -c 8192.
- [ ] **Do Good Design copyright spam** — `_restored_after_rescue_fail` restores false positive match
  for "Об авторе". Low priority. See bug file for options.
