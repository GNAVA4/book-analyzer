# BUG: ВКР — NavigationTable duplicates "ГЛАВА 1" at the end (empty page) + intro gets ToC fragment
_Filed: 2026-05-29 session 004 | Status: open_

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
