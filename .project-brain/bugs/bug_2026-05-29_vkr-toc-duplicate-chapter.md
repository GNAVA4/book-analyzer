# BUG: ВКР — NavigationTable duplicates "ГЛАВА 1" at the end (empty page) + intro gets ToC fragment
_Filed: 2026-05-29 session 004 | Status: code change applied, AWAITING live verification + corpus regression_
_Code applied: 2026-05-30 session 005 — needs live re-run before marking FIXED_

## Code change (applied — unit-tested only)
Root: the two "ГЛАВА 1" entries differ by ONE OCR letter («ИЗУЧЕН**ЯЯ**» on the ToC page vs.
«ИЗУЧЕН**ЯЮ**» from the chapter heading) — exact-norm dedup in `_dedup_and_order` missed them.
Added `_drop_fuzzy_pageless_dupes` (SequenceMatcher ratio ≥ 0.88) as a second pass: any page-less
entry that fuzzy-matches an earlier paged entry is dropped. Guard: skip when title length < 8 chars
(avoids "Введ" colliding with "Введение").

281 unit tests pass (+6 new dedup, +6 new ocr) — but **no live pipeline run yet** on ВКР or the rest
of the corpus. Until that's done, do not claim FIXED.

Files: `app/services/toc_builder.py` (`_drop_fuzzy_pageless_dupes`, called from `_dedup_and_order`).
Tests: `tests/test_toc_builder.py::TestDropFuzzyPagelessDupes` (6 cases).

## Verification still needed
- Live re-run ВКР: confirm trailing "ГЛАВА 1" with empty page is gone, ratio stays high.
- Re-run the rest of tests_v3 corpus and diff against the previous XMLs — any book where a real
  page-less entry was legitimately distinct from a paged one is at risk if the 0.88 threshold is wrong.
- Particularly watch: books with subsection structures where heuristic emits page-less entries
  (e.g. Розенсон, Клейнман — anything where smart LLM fallback fired).

## Residual (separate — content mapping, not ToC)
ВВЕДЕНИЕ/ЗАКЛЮЧЕНИЕ get ToC fragments — same class as the still-residual `subsection-maps-into-toc`
case (Кениг 3.Свет/4.Текстура): titles that only match inside the ToC region. Tracked by that bug.

## Symptom
ВКР (`ВКР_БАК_Лаванг_РА_УСБО-02-21.pdf`, toc_source=ocr_heuristic) — the ToC parses fine from page 1,
BUT the NavigationTable lists "ГЛАВА 1 …" a SECOND time at the very END, after ЗАКЛЮЧЕНИЕ, with an
empty `page=""`:
```
<Item title="ЗАКЛЮЧЕНИЕ" page="82" level="1"/>
<Item title="ГЛАВА 1 ТЕОРЕТИЧЕСКИЕ ОСНОВЫ … ОБЪЕМА ПЛАТНЫХ УСЛУГ НАСЕЛЕНИЮ" page="" level="1"/>
```
Also content misplacement: `<section title="ВВЕДЕНИЕ">` content is a ToC fragment
("…структурных сдвигов…24 / 2.2 … / ГЛАВА 3 … 41 …"), not the real introduction. ЗАКЛЮЧЕНИЕ content
is also wrong. (user-confirmed)

## To reproduce
Open `tests_v3/ВКР_БАК_Лаванг_РА_УСБО-02-21.xml` → NavigationTable has trailing duplicate ГЛАВА 1
with empty page; ВВЕДЕНИЕ/ЗАКЛЮЧЕНИЕ contents are ToC fragments.

## Root cause (hypothesis)
- ToC dup: OCR-heuristic likely picked up "ГЛАВА 1" both from the real ToC and from a running
  header / repeated mention, and `_dedup_and_order` (title-only key) did not merge it because the
  duplicate has `page=None` and a slightly different normalized title, or it was appended after
  ordering. Needs tracing in `_dedup_and_order` / `toc_to_linear_sequence` page-backfill.
- Content: ВВЕДЕНИЕ matched inside the ToC region (gets the ToC fragment) — same class as
  [[bug_2026-05-29_subsection-maps-into-toc]].

## Fix (directions)
- `_dedup_and_order`: ensure title-only dedup also merges entries when one has page=None (drop the
  page-less duplicate if a paged one with the same normalized title exists). Guard against appending
  a page-less duplicate after the ordered list.
- Content: shared with the subsection-into-ToC fix.

## Affected files
- `app/services/toc_builder.py` — `_dedup_and_order`, `_normalize_llm_items`/ocr path.
- `app/services/toc_parser.py` — `toc_to_linear_sequence` page backfill.
- `app/services/mapping_pipeline.py` — in-ToC content (shared fix).
