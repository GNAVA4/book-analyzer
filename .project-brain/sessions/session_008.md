# Session 008 — 2026-06-10
← [session_007.md](./session_007.md)

## Goal
Honestly verify session 007's anchor-interpolation change. Initial corpus
results showed 12_100229 real -26 and AI real -17, which I framed as
"possibly correct redistribution". User pushed back and asked for evidence,
which is what should have happened before shipping.

## What actually happened

### Verification round
Opened the XML for four 12_100229 lost-real sections that previously had
exact-strategy matches with substantial content. Three of the four showed
real prose cut off mid-word: § 1.5 «Передача сигналов в цифровых узлах…» went
from 313 chars («При работе ЦУ в межсоединениях (линиях связи) может…») to
83 chars («…множество импульсн») — slicing chopped the section in the
middle of a word.

Opened AI 19.6 «Summary, Bibliographical and Historical Notes». v6 (linear
page_cut): 14 938 chars of correct bibliography. v7 (anchor-extrapolation):
27 chars («P., 760, 1064, 1073\\ninfer») — a fragment from the alphabetical
index at the very end of the book. The section was placed past the last
anchor, extrapolation projected it deep into the back-matter.

That is two distinct failure modes, both real content loss:

1. **Ordering violation** (12_100229): a page_cut position landed BEFORE
   the end of the prior trusted-mapped section. The slicing rule
   `next_start = min(start_idx > current_start)` then ate the prior
   section's tail.

2. **Extrapolation drift** (AI): a page_cut position past the last anchor
   projected forward using the local (text/pages) rate. The rate breaks
   where book density changes — index pages are denser than body. Sections
   ended up tens of thousands of chars past where they should have been.

### Fixes (in working tree, not committed pending user direction)

**Ordering guard in page_cut loop.** After computing the position, clamp to
`>= max(prior mapped section's end_idx, start_idx)`. On a partial corpus
rerun: 19 of 120 page_cut sections in 12_100229 hit the clamp; 17 of 23 in
MIL-STD; AI exercised it 0 times (its problem was not ordering).

**Bracketing-only interpolation.** `_interpolate_position_from_page` now
returns the linear estimate unless BOTH a `prev_a` AND a `next_a` anchor
exist for the requested page. No extrapolation. This addresses the AI 19.x
case directly — those pages are past the last anchor, so they fall back to
linear, which was correct in v6.

The original target case (MIL-STD 5.1.2.5) is preserved: section 5.1.2.5 is
between bracketing anchors (5.1.2.3 at page 27 and 5.1.3 at page 29), so
interpolation still applies and the 61 677 → 33 chars bloat fix holds.

Unit tests: 67/67 in mapping_pipeline. Extrapolation tests rewritten to
expect linear fallback; ordering-guard tests added.

### Partial corpus verification (5 books)
Run with both fixes against tests_v8:

| Book | v6 real | v7 real | v8 real |
|------|--------:|--------:|--------:|
| 12_100229 | 301 | 275 | 279 |
| AI | 184 | 167 | 167 |
| MIL-STD | 594 | 594 | 598 |
| 1332 | 51 | 51 | 51 |
| 978-5-7996 | 22 | 10 | 21 |

12_100229: partial recovery (+4). Most of the regression remains because
ordering guard only addresses one mechanism (mid-content cuts), and the
remaining 22 lost sections are likely smaller-scale boundary shifts that
the guard doesn't catch.

AI: still at 167. The bracketing-only restriction was applied to the code
but the test corpus didn't include AI in the targeted re-run. Per
inspection, the new code should give AI 19.x back to near 14k chars each.
Full corpus rerun pending user direction.

MIL-STD: improves to 598 real (+4 from v7's 594, +4 from v6). 5.1.2.5 stays
fixed.

978-5-7996: back near v6 baseline (21 vs 22) — that book's main issue is
LLM nondeterminism, not the mapping changes.

Stopped here at user's instruction.

## Decisions made
| Decision | Chosen because |
|----------|----------------|
| Bracketing-only interpolation (no extrapolation) | Extrapolation projected past anchor range into the index region; linear estimate was safer outside the bracket |
| Ordering guard via floor at prior end_idx | Direct fix for the 12_100229 mid-word truncation; cheap; reversible per page_cut section |
| Stop before corpus rerun on user instruction | User wants to direct the next step explicitly |

## What we tried that didn't work
- ❌ Initial framing of v7 results as "redistribution, possibly more honest".
  User pushed back. Inspection of XML content confirmed real loss. The
  framing was a hedge against my own lack of verification.
- ❌ Ordering guard alone did not recover AI. AI's failure mode was
  extrapolation drift, not ordering violation; the guard never fired.
  Required the second fix (bracketing-only).

## What works now (after fixes, partial verification)
- ✅ MIL-STD 5.1.2.5 stays fixed (33 chars, no bloat).
- ✅ 12_100229 partially recovers (+4 of -26).
- ✅ 67/67 unit tests pass.

## What needs full corpus verification (not run per user request)
- AI 19.x sections — should recover to ~14k each via bracketing-only.
- Klein, parallelnoe, Розенсон, Иглмен — no expected change but unverified.
- All books with toc_source!='heuristic' that exercise OCR-aware fuzzy —
  no expected change but unverified.

## Files changed (working tree)
- `app/services/mapping_pipeline.py` — `_interpolate_position_from_page`
  restricted to bracketing only; page_cut loop has ordering guard with
  clamp counting.
- `tests/test_mapping_pipeline.py` — two extrapolation tests rewritten to
  expect linear; two ordering-guard tests added; 67/67 pass.

## Insights filed
- [[insight_2026-06-10_anchor-extrapolation-is-dangerous]]
- [[insight_2026-06-10_content-loss-is-real-not-redistribution]]

## Bug filed
- [[bug_2026-06-10_anchor-interpolation-extrapolation-into-index]]

## Analysis after stop (better / worse / options)

### Better than v7 (likely, partially verified)
- AI 19.x sections — should recover via bracketing-only fallback to
  linear estimate (NOT VERIFIED with corpus; code-logic only).
- 12_100229 mid-word truncations — guard removes that specific mechanism
  (verified via partial run: 19 of 120 page_cut sections clamped, real
  275 → 279).
- MIL-STD 5.1.2.5 — bracketing case applies, fix holds; +4 real overall.
- System is simpler: less guessing past observed data.

### Possibly worse than v6 baseline
- 12_100229 only partially recovers (−22 sections remain vs v6).
  Ordering guard doesn't catch subtler boundary shifts that still drop
  some sections below the 100-char threshold.
- AI recovery is a hypothesis, not a measurement.
- Books where extrapolation was incidentally correct (none observed,
  but possible).

### Out of scope
- 978-5-7996 LLM nondeterminism.
- digital-design heavy OCR drift in body.
- Кениг 3.Свет/4.Текстура residual.

## Resolution options (recorded for next direction)

**A. Commit current fixes + full corpus rerun.** Conservative fixes
(guard is no-op when not violated; bracketing-only reverts to v6 outside
brackets). Full run, per-book inspection of 2-3 affected XMLs, then ship.

**B. Targeted verification first** (AI + 12_100229 + MIL-STD + 1332 +
Виды UI). ~10 min. AI confirms recovery. 12_100229 confirms guard.
MIL-STD confirms 5.1.2.5 still fixed. If clean → A. If not → C or D.

**C. Revert anchor-interpolation entirely, keep only ordering guard.**
Safest. Sacrifices MIL-STD 5.1.2.5 fix.

**D. Narrow anchor-interpolation to clearly-implausible-zone detection.**
Only fire when linear estimate lands in obvious junk (e.g. < 50K chars in
a >300-page book). MIL-STD qualifies; AI doesn't. Defensible but threshold
needs justification.

Recommended: B → A.

## End state
Branch `llm-toc-fallback`. Working tree has two uncommitted fixes
(bracketing-only + ordering guard). User requested stop before corpus
rerun and commit. Awaiting direction on which option (A/B/C/D) to
pursue.
