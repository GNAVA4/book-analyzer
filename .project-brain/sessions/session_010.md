# Session 010 — 2026-06-27
← [session_009.md](./session_009.md)

## Goal
After reverting global anchor-interpolation to v6 plain linear (session 009, verified
on tests_v11 = v6 with AI fully recovered), the only remaining regression is MIL-STD
5.1.2.5 bloat (61 677 chars). User chose a **local backward-anchor** to fix it without
reintroducing the cascade-prone global anchor machinery.

## Root cause of MIL-STD 5.1.2.5 bloat (verified on tests_v11)
5.1.2.5 is `page_cut` at page 29. The book is front-loaded (huge multi-page ToC), so
the linear page→position estimate for page 29 lands far too EARLY; content slicing then
runs from that early position to the next found exact match (5.1.3), absorbing 61 677
chars. The section's START is wrong, not just its length — so a pure length-cap would be
cosmetic. The real text of 5.1.2.5 sits in a NARROW window between its exact-matched
neighbours: 5.1.2.3 @p27 (exact) … 5.1.3 @p29 (exact), bracket span = 2 pages.

## Calibration data (tests_v11 page_cut runs bounded by exact matches)
- MIL-STD: EVERY page_cut run is tightly bracketed — bracket_span ≤ 4 pages.
- AI: the damaging back-matter runs are wide — ch.19–22 run span = 79, ch.24–27 run has
  no next bound; single-section runs span 6–21. (These were what global anchor-interp
  poisoned.)
- Clean separator: **bracket_span ≤ 5 pages** fires for all MIL-STD runs, none of AI's.

## Implementation (mapping_pipeline.py)
- New constant `ANCHOR_BRACKET_MAX_SPAN_PAGES = 5` (documented, calibrated, tunable).
- New `_BRACKET_BOUND_STRATEGIES` = strong text matches incl. page_hint variants
  (exact / exact_normalized / tokenized_regex / page_hint_exact / page_hint_tokenized_regex).
- Final page_cut loop rewritten to be RUN-aware: a maximal run of consecutive page_cut
  sections is interpolated EVENLY BY COUNT between the nearest prior strong-match end_idx
  and nearest next strong-match start_idx — but ONLY when both bounds exist and their ToC
  pages differ by 0 < Δ ≤ 5 (tight bracket) and positions are ordered. Otherwise each
  section falls back to plain linear `_estimate_position_from_page` (v6).
- Even-by-count distribution (not by page) avoids the same-page collision (5.1.2.5 and
  5.1.3 are both "page 29").
- Locality + tight span ⇒ no global anchor list, no ordering-guard floor, no cascade.
- Log: page_cut line now appends "(N locally bracketed)".

## Tests
- `TestPageCutLocalBracket`: tight bracket interpolates between neighbours; wide bracket
  (span 19) falls back to linear.
- `TestPageCutLinear` retained (linear estimate + misplaced-prior-does-not-drag).
- Full suite **332 passed**.

## Verification — tests_v12 RUNNING (bg brckm95v2)
Expectations:
- MIL-STD 5.1.2.5: 61 677 → reasonable size (real text in [end_5.1.2.3, start_5.1.3]).
- MIL-STD real ≥ 594 (ideally back toward the +4 anchor-interp gave: ~598).
- AI: stays 184 (back-matter runs have span ≥6 → not bracketed → linear, unchanged).
- 12_100229 / others: no regression.

## tests_v12 RESULT — bracket safe, but 5.1.2.5 NOT fixed
- AI 184 (dup-signal healthy 8925×2) — not regressed. ✓
- Bracket fired: 38× (12_100229), 7× (MIL-STD), etc. No regressions.
- Corpus TOTAL 2500 = v11 (MIL-STD +1, 978 −1 noise) → **net effect ≈ zero**.
- **MIL-STD 5.1.2.5 STILL 61677.** Debug (temp print, removed) revealed why:
  - prior bound 5.1.2.3 (p27) @ pos 250640
  - next bound 5.1.3 (p29) @ pos **218308** — BEFORE 5.1.2.3!
  - `next_pos > prior_pos` fails → bracket correctly REFUSES the inverted bound →
    5.1.2.5 falls back to linear (early, in front-matter ToC: preview "5.9.12.2 Tools. ....").
- Root: 5.1.3 "Computer controls" is a bare heading (content ". \n5.1.3.1", 10 chars);
  it + 5.1.3.1 sit at pos ~218k while 5.1.2.3 (earlier in doc) is at 250k — a position-
  ORDER INVERSION (page-29 sections appear before page-27 in the char stream). MIL-STD
  has a giant leader-dot front-matter ToC; matching there is anomalous.
- **Conclusion:** local backward-anchor is correct + safe but cannot fix 5.1.2.5,
  because the bloat is caused by a MIS-PLACED neighbour (5.1.3), not a front-matter
  linear underestimate. Fixing 5.1.2.5 needs a separate matching-order investigation
  (why 5.1.3/5.1.3.1 land at 218k), out of scope for page_cut.

## Decision pending (user's)
- Keep local bracket (safe, ~0 net corpus effect, doesn't fix target) + accept 5.1.2.5
  bloat (1 section, doesn't hurt real-count); OR
- Revert local bracket too → cleanest minimal v6+chars (simplicity-first); OR
- Go deeper: investigate the 5.1.3 position inversion (separate matching task).

## Notes
- `chars` XML attribute (session 009) retained.
- Working tree now: v6-linear base + `chars` + local backward-anchor. Not committed.
