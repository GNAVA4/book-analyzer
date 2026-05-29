# BUG: Клейнман content regression — wrong-occurrence match cascaded current_pos (cov 0.98 → 0.10)
_Filed: 2026-05-29 session 004 | Status: FIXED (by the list-context match-preference fix)_
_Fixed: 2026-05-29 session 004_

## CORRECTED root cause (my first hypothesis — "Fix A page_cut" — was WRONG)
With the COMPLETE ToC, Клейнман maps perfectly. The tests_v3 breakage was a cascade in
`find_real_indices`: an early section matched the WRONG occurrence (a list/bulleted region or far
spot), which pushed `current_pos` forward past the real positions of later titles; those titles then
were not found from `current_pos` → fell to `page_cut` → garbled/misplaced content (36 page_cut).
This is the SAME mechanism as [[bug_2026-05-29_subsection-maps-into-toc]] — wrong-occurrence matching.

## Fix (applied + verified)
The session-004 list-context fix (`_find_first_nonlist` / `_is_list_context` in
`pdf_utils._search_with_confidence`) makes matches prefer the prose occurrence, keeping `current_pos`
sane. Verified: heuristic-40 ToC now maps with **36 exact + 4 exact_normalized, ZERO page_cut**;
«Введение» rawlen 121709 (= OLD test_v2 121479), «Гарри Харлоу» 6429 — content correct, cov ~0.98.
Так что fix для Розенсона/Кенига попутно вылечил и Клейнмана.

Secondary improvement (`toc_builder._llm_from_text_retry`): retry the LLM ToC when it returns
suspiciously FEW items vs the raw ToC entry count (LLM is non-deterministic — for Клейнман it returned
57 some runs, 7 others; the 7-item run let the incomplete heuristic-40 win selection). This makes the
LLM ToC more reliably complete. Note: Клейнман maps fine with EITHER the heuristic-40 or llm-57 ToC now.

## Fix A note
Fix A (defer far page-distance reverts to page_cut) was NOT the cause here. It remains in place and is
correct for Do Good (reliable pagination). Keep watching for books with non-linear pagination, but
Клейнман's issue was the current_pos cascade, not Fix A.

---
_Original (incorrect) analysis below:_

## Symptom
Клейнман content collapsed between test_v2 and tests_v3 despite an IDENTICAL ToC (same 40 items).
- test_v2 (OLD): 40 sections, all matched directly, content CORRECT. e.g. «Гарри Харлоу» =
  "ГАРРИ ХАРЛОУ (1905–1981) Обезьяны — это серьезно…". coverage 0.98.
- tests_v3 (NEW): 36/40 sections are `page_cut` (conf 0.30); content misplaced and partly garbled.
  «Гарри Харлоу» now holds Pavlov-dog text ("слоноотделение у собак" — garbled OCR of "слюно…");
  «Джон Боулби» [exact] holds ToC leader dots. coverage 0.10. Many sections ~834 chars (≈10% of exp).

## To reproduce
1. `AUDIT_XML_DIR=tests_v3 venv/Scripts/python.exe scripts/audit_content.py "Клейнман"` → cov 0.10, 38 TRUNC.
2. Compare with `AUDIT_XML_DIR=test_v2 … "Клейнман"` → cov 0.98.

## Root cause (analysis)
Клейнман's printed page numbers do NOT map linearly to text position (uneven text/figure density):
`_estimate_position_from_page(p102) = 158793` lands on early-book (Pavlov) content, not Харлоу.
The titles DO exist in the body (OLD matched them directly), but their exact matches are FAR from the
linear page-estimate → page-distance check reverts them. Then **session-004 Fix A**
([[bug_2026-05-28_part-divider-absorbs-chapter-body]]) changed the fallback: instead of RESTORING the
reverted (far-but-correct) match, it now DEFERS to `page_cut`, which positions by the unreliable
page-estimate → wrong/garbled content. For Do Good (reliable pagination) Fix A helped; for Клейнман
(unreliable pagination) it regressed.

The text layer also has OCR-quality garbling in places ("слонны", "отказылся", "психологическей"),
which makes the misplaced page_cut content additionally unreadable.

## What we tried (that didn't work)
- ❌ Verified Class-2 fix only on Массель/parallelnoe (B safe) — did NOT re-check Клейнман after Fix A.
  The corpus run revealed the regression.

## Fix (directions — needs design)
- Fix A should NOT blindly prefer page_cut over a restore when the page-estimate is unreliable.
  Options: (a) keep BOTH candidates and pick the one whose content is non-junk / longer / matches the
  title; (b) only defer-to-page_cut when page-estimates are globally consistent (anchor agreement
  high); (c) detect non-linear pagination (anchor offset variance) and trust text matches over page_cut.
- General: when restore vs page_cut, prefer the position whose sliced content actually contains the
  section's title keywords.

## Affected files
- `app/services/mapping_pipeline.py` — `_restored_after_rescue_fail` (Fix A), page-distance check,
  page_cut.

## Detection tooling
- `scripts/audit_content.py` per-section ratio + content preview; compare test_v2 vs tests_v3.
