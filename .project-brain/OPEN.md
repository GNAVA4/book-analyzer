# OPEN ITEMS — Book Analyzer
_Last updated: 2026-05-29 session 004_

## In progress
_(nothing active)_

## Done this session (was in progress)
- [x] **Class 2: content misplacement** — FIXED. Release It! resolved by ToC subsections; Do Good fixed
  by mapping Fix A (defer far page-distance reverts to page_cut) + Fix B (`_revert_position_clusters`).
  Measured ratios ~1.0; no regressions (B 0 false reverts on parallelnoe/Массель); 264 tests pass.
  See bug_2026-05-28_part-divider-absorbs-chapter-body + insight_2026-05-29_class2-misplacement-mechanism.
- [x] **Smart LLM/OCR ToC fallback** — DONE & live-verified. Розенсон 20→77 (llm), Клейнман 41→57 (llm),
  complete books untouched, 260 tests pass. See bug_2026-05-29_toc-subsections-dropped + ADR 003.
  Optional follow-up: raise LM Studio ctx to ~24K and TOC_MAX_TOKENS to ~6000 (user offered) — only
  matters for ToCs bigger than ~85 items; salvage parser already handles truncation gracefully.

## TODO
- [ ] **Class 2: content misplacement** — `mapping_pipeline.py`. Text not lost but filed under wrong
  section (divider/neighbour absorbs a chapter's body). Confirmed: Release It! «Часть I» ate ch.2 body;
  Do Good «Глава 6» ×4.5 while ch.7–14 empty (conf=1.0); Кениг/Массель/parallelnoe same pattern
  (exact short-title matches landing out-of-order). See bug_2026-05-28_part-divider-absorbs-chapter-body.
- [ ] **Content quality audit of remaining books** — Do Good / Кениг / parallelnoe content handed to me
  by user; audit_content flags are class-2 misplacement (not ToC). Massель: 3 EMPTY (conf=1.0).
- [ ] **MIL-STD 154 not-found / 92 EMPTY** — coverage .944 → text present but mis-attributed; likely class 2.
  Re-measure with new mapping (Fix A/B) before investigating further.
- [ ] **parallelnoe appendix D** — dense std:: reference entries get mis-located by embedding_rescue
  (TRUNC/BLOAT in D.x). Pre-existing, NOT caused by Fix A/B. Separate from class 2; low priority.
- [ ] **Re-run full pipeline + re-audit all 11 books** with session-004 changes (ToC + mapping) to
  refresh test_v2 baseline and confirm corpus-wide improvement. Needs LM Studio.
- [ ] **report.json content_len/preview are all 0** (test_v2) — post-hoc artifact (session 003 script
  edited mid-run). Source of truth for content = the XML, not the report. Regenerate reports if needed.
- [ ] **Fix PIPELINE_DIFF.md note** — says "LLM clean is sequential", actually asyncio.gather. Docs only.

## Open bugs
_(none P0)_

## Closed this session
- [x] `bug_2026-05-28_part-divider-absorbs-chapter-body.md` — content misplacement (class 2). FIXED.
- [x] `bug_2026-05-28_do-good-design-copyright-spam.md` — "Об авторе" copyright. FIXED (defer to page_cut).
- [x] `bug_2026-05-29_toc-subsections-dropped.md` — A heuristic + B smart LLM fallback. FIXED.

## Closed bugs
- [x] `bug_2026-05-28_release-it-regression.md` — FIXED session 001.
- [x] Иглмен 2 not-found (page_cut ordering) — FIXED session 002.

## Open questions / decisions needed
- [ ] **GROUNDING_MIN=0.8 / incompleteness ratio 1.5** — calibrated offline; confirm on live runs, tune if needed.
- [ ] **scrub_foreign_script threshold** — removes ALL CJK/Arabic. Edge case: books with intentional CJK.

## Deferred (not forgotten)
- [ ] **0e6e53b LLM-clean speed** — 14 low-conf sections, parallel but slow. Acceptable.
- [ ] **toc_validation (OCR coverage) metric** — noisy when heuristic ToC is perfect. Informational only.
- [ ] **Context length guard** — no early warning if LM Studio ctx < 8192. Manual workaround.
- [ ] **Do Good Design copyright spam** — low priority. Options in bug file.
