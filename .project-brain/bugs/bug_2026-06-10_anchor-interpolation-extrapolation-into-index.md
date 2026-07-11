# BUG: Anchor-interpolation extrapolation places page_cut sections inside the back-matter index
_Filed: 2026-06-10 session 008 | Status: FIXED in working tree (uncommitted)_

## Symptom
Session 007 introduced anchor-interpolation in the page_cut fallback. On the
corpus, AI.pdf lost 17 sections of real content. Investigation showed that
late-book page_cut sections (chapters 19.x, 20.x near page 800) were placed by
extrapolation past the last available anchor, landing INSIDE the alphabetical
index at the end of the book.

Concrete (tests_v6 vs tests_v7):
- `19.6 Summary, Bibliographical and Historical Notes, Exercises` p=797:
  - v6 (linear page_cut): 14 938 chars — proper bibliography text starting
    "operties of various estimators. Devroye (1987) gives a thorough
    introduction…"
  - v7 (anchor extrapolation): 27 chars of garbage — "P., 760, 1064,
    1073\\ninfer" — an entry from the back-matter alphabetical index.

Similar damage on `20.1 Statistical Learning` (11 930 → 22 chars), `20.2`
(29 827 → 55), `20.3` (26 923 → 49). 16 AI sections affected, ~80k chars of
real content evaporated.

12_100229 had separately a content-loss case where page_cut crossed over a
prior exact-match section (different mechanism, addressed via ordering
guard — see fix section).

## To reproduce
1. Checkout commit `151726e` (session 007: anchor-interpolation introduced).
2. Run `scripts/run_corpus.py AI.pdf MIL-STD 12_100229`.
3. Diff per-section content_len against the prior baseline. AI's late chapters
   collapse from thousands of chars to dozens.

## Root cause
`_interpolate_position_from_page` in `mapping_pipeline.py` did three things:
1. interpolate between bracketing anchors (the safe case);
2. extrapolate FORWARD past the last anchor when the requested page is later;
3. extrapolate BACKWARD before the first anchor when the requested page is
   earlier.

Both extrapolations were calculated proportionally — but the proportionality
breaks where text density changes outside the anchor range. AI's anchors are
concentrated in the body; the back of the book (index, bibliography pages)
has higher character density per page than body content. Extrapolating
proportionally from the last anchor sent each subsequent page_cut section
deeper into the index region.

A separate mechanism caused 12_100229 losses: even between anchors, the new
positions were ordered consistently with anchor data but could land EARLIER
than the prior trustworthy section's end_idx, so the prior section's content
got sliced off mid-sentence.

## Fix (applied to working tree, not yet committed)
Two changes to the page_cut logic in `mapping_pipeline.py`.

1. **Bracketing-only interpolation.** `_interpolate_position_from_page` only
   interpolates when the requested page has BOTH a `prev_a` and a `next_a`
   anchor. Otherwise it returns `_estimate_position_from_page` (the original
   linear estimate). Linear was always the right answer outside the observed
   anchor range — extrapolation just risked the index region.

2. **Ordering guard in the page_cut loop.** After computing the position,
   clamp it to `>= max(prior mapped section's end_idx, start_idx)`. Prevents
   the page_cut from cutting off the prior section's content. On the test
   re-run, 19 of 120 page_cut sections in 12_100229 hit this clamp (and 17 of
   23 in MIL-STD).

## Verification (partial — corpus rerun pending per user request)
- 67/67 unit tests pass, including new tests for the bracketing-only behavior
  and the ordering guard.
- The two fixes are orthogonal: bracketing-only addresses AI 19.x type
  losses; ordering guard addresses 12_100229 type mid-word truncation.
- The original target case (MIL-STD 5.1.2.5: linear page 29 estimate landed
  in multi-page ToC region) STILL works: 5.1.2.5 sits BETWEEN two
  bracketing anchors (5.1.2.3 page 27 and 5.1.3 page 29), so interpolation
  applies and the 61k bloat stays fixed.

## Affected files
- `app/services/mapping_pipeline.py` — `_interpolate_position_from_page`
  (extrapolation removed), page_cut loop (ordering guard added).
- `tests/test_mapping_pipeline.py` — two extrapolation tests rewritten to
  expect linear fallback, two ordering-guard tests added.

## Lessons (filed separately as insights)
- [[insight_2026-06-10_anchor-extrapolation-is-dangerous]]
- [[insight_2026-06-10_content-loss-is-real-not-redistribution]]

## Analysis of the working-tree fix (session 008)

### Likely better than session 007 v7
- **AI 19.x sections**: bracketing-only restriction means pages past the
  last anchor fall back to linear estimate (v6 behaviour). The 16 sections
  that landed in the back-matter index should recover to their v6 content
  lengths. NOT YET VERIFIED with corpus run — this is a hypothesis based on
  code logic. The same trap as session 007 if I claim it without checking.
- **12_100229 mid-word truncations**: ordering guard clamps page_cut
  positions to >= prior end_idx. Partial run showed 19 of 120 page_cut
  sections clamped. The three sections inspected by hand (§ 1.5, § 2.10,
  § 2.11) had specific mid-word cuts; guard removes that mechanism.
- **MIL-STD 5.1.2.5**: bracketing case applies (anchors on pages 27 and
  29), interpolation still fires, the 61 677 → 33 char fix holds.
- **System is simpler**: less "smart guessing" outside the observed sample
  range, more trust in the linear default.

### Possibly still worse than v6 baseline
- **12_100229 partial recovery only.** Partial corpus showed real 301 →
  279 with both fixes (vs 275 with guard alone, vs 301 v6 baseline). −22
  unaccounted-for sections remain. The ordering guard catches mid-content
  cuts. There's a separate, subtler effect where page_cut positions stay
  AFTER prior end_idx but still shift section boundaries by hundreds of
  chars, dropping real-content below the 100-char threshold for some
  sections that were borderline.
- **AI not actually verified.** Logic says it should recover; corpus run
  pending. Until verified, the AI 17-section loss is open.
- **Books where extrapolation happened to be correct.** Unknown if any
  exist in the corpus. Code-logic alone can't tell us — needs run.
- **Out of scope of this fix**: 978-5-7996 LLM nondeterminism,
  digital-design heavy OCR drift in body, Кениг 3.Свет/4.Текстура
  residual.

## Resolution options

**A. Commit current fixes + full corpus rerun.** Both fixes are
conservative (guard is no-op when not violated; bracketing-only reverts
to v6 behaviour outside brackets). Run all 19 books, inspect 2–3 lost-real
XML sections per affected book per the verification protocol, then ship.

**B. Targeted verification first (AI + 12_100229 + MIL-STD + 1332 + Виды
UI).** ~10 minutes. AI confirms bracketing-only recovers the 17 sections.
12_100229 confirms guard's effect. MIL-STD confirms 5.1.2.5 still fixed.
1332 + Виды UI as healthy-book regression anchors. If clean → full corpus
→ commit. If not → switch to C or D.

**C. Revert anchor-interpolation entirely, keep only the ordering guard.**
Safest option. Sacrifices MIL-STD 5.1.2.5 (the 61k bloat returns). Right
choice if the value of fixing one MIL-STD section is below the risk of
shifting other books' boundaries.

**D. Narrow anchor-interpolation to "obvious junk zone" detection.** Only
fire when the linear estimate would place a section in a clearly
implausible region (e.g. < 50K chars in a >300-page book = almost
certainly inside the multi-page ToC region at the front). MIL-STD page 29
qualifies; AI page 797 does not. Defensible but requires choosing a
threshold.

### My recommendation
B → A. Targeted re-run validates the two hypotheses (AI recovery,
MIL-STD preservation) in 10 minutes. If both confirm, full corpus + commit
follows. If AI doesn't recover as expected, switch to C (full revert) or
D (narrow scope) without having committed a regression.
