# INSIGHT: Class-2 content misplacement — exact titles matching in back-matter cluster
_Filed: 2026-05-29 session 004_
_Category: architecture_

## The finding
Content "missing" from sections (high confidence, empty/tiny body) is NOT lost — it is mis-attributed
because chapter titles match by EXACT text in a back-of-book cluster (chapter list / index / dividers),
far from where their real bodies are. The slicing `full_text[end_idx : nearest start_idx > end_idx]`
then gives the body span to whichever section sits at the body's start (often a page_cut divider).

Two concrete failures (Do Good Design, measured; full_text_len=268389, page-distance tol=80516):
1. **`_restored_after_rescue_fail` restores false-positive far matches.** A match reverted by
   page-distance (because it's far from its page estimate) is restored at conf×0.7 when rescue finds
   nothing better. But "far" was exactly why it was suspect — restoring re-introduces the false
   positive. Глава 1 (dist 184689), 3 (154751), 5, 7, and «Об авторе» (dist 256946 → copyright text).
2. **Clustered exact matches within tolerance escape detection.** Глава 8–12: conf=1.0 exact matches
   at positions 246492→249525 (a ~3K span for 5 chapters!) while their page-estimates span 180K–230K.
   Distances 18K–66K are UNDER the 80K tolerance, so page-distance doesn't revert them, and
   `_verify_and_correct_order` only reverts rescue/page_hint strategies (not exact). They keep wrong
   positions → Глава 6 page_cut absorbs all their bodies (98804 chars, ×4.56 bloat).

## How we found it
`scripts/mapping_audit.py` (build_toc + map_sequence + slicing, no LLM-clean) with start/est/dist
columns. Compared Release It! (fixed) vs Do Good (broken).

## Key contrast (why ToC fix alone wasn't enough)
- **Release It!**: the ToC fix (session 004) added subsections 2.1–2.5, 3.1–… which become intra-chapter
  cut points. Measured: «Часть I» 30611→4090, ch.2 distributed across 2.1–2.5 (ratios ~1.0).
  Class-2 essentially RESOLVED for free by the richer ToC. No mapping change needed.
- **Do Good**: ToC is chapter-level only (no subsections to anchor). The exact-match-in-back-matter
  cluster must be fixed in mapping.

## Fix (APPLIED + verified, session 004)
Both A and B implemented in `mapping_pipeline.py`; measured with `scripts/mapping_audit.py`.
Do Good after A+B: Глава 6 ×4.56→1.12, Глава 7 →1.22, Главы 8–12 0.78–1.13 (were 0.01–0.14).
Regression: B fires 0 times on parallelnoe(248)/Массель; A is scope-limited to page-distance reverts.
264 unit tests pass (incl. 4 new `_revert_position_clusters` tests).

## Proposed fix (mapping_pipeline) — IMPLEMENTED as below
- **A) Defer far reverts to page_cut**: in `_restored_after_rescue_fail`, do NOT restore a
  page-distance backup if the section has a known page (page_cut will place it correctly by page).
  Restore only when there is no page (page_cut can't help). Fixes ch1,3,5,7 + «Об авторе» copyright.
- **B) Cluster revert**: detect a run of ≥3 consecutive ToC sections whose matched start positions are
  crammed into a tiny text span (e.g. ≤3000 chars) while their page-estimates are widely spaced —
  that's a list/index, not bodies. Revert the run to not_found so page_cut spreads them by page.
  Fixes ch8–12.

## Applies when
Chapter-level ToC (no subsections) + the book repeats chapter titles in a back-matter list/index.

## Does NOT apply when
Books with subsection-rich ToC (the subsections anchor content) — those are fixed by ToC quality, not
mapping. Don't over-tighten page-distance tolerance globally: linear page→position estimate is crude,
a tight tolerance would false-revert legitimate matches whose ToC page is imprecise.
