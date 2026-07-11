# Session 012 — 2026-06-27/28
← [session_011.md](./session_011.md)

## Goal
Implement "fix A" (OCR-drift in body, digital-design 22% real). User initially chose
A2 (embedding localization). Investigation overturned the premise.

## Diagnosis pivot (the real root cause)
The premise "OCR drift breaks matching" was WRONG. Cached digital-design extraction
showed full_text used for mapping was only **25178 chars** = title + ToC (leader dots)
+ preface, NOT the body. But `get_all_text(doc)` (PyMuPDF text layer) returns
**1.586M chars** of real, readable body (verified at deep offsets: exercises @200k,
tri-state buffers @500k, MIPS asm @900k, LCD @1.3M).

Root cause: in `pdf_parser_neural.parse_pdf_neural`,
`full_text = ocr_text if ocr_text else get_all_text(doc)`. When a document is READABLE
but its ToC needed OCR escalation, `build_toc` runs `ocr_document(max_pages=OCR_TOC_PAGES)`
(ToC-region only) and returns that short `ocr_text` (~25k). It then OVERRODE the full
readable body. → mapping searched 25k of ToC → 94 sections page_cut into front-matter.

(User caught two of my wrong turns en route: I first claimed "body missing" — false; then
mis-read 1.586M as ToC — it's the whole book. Their push to "compare the text sources
directly instead of trusting the readability flag" shaped the final fix.)

## Fix (pdf_parser_neural.py)
New `_pick_body_source(text_layer, ocr_text)`: choose the body text by DIRECTLY
comparing candidates, not the pre-sampled readability flag.
- no ocr_text → text_layer.
- text_layer garbage (detect_garbage_text on a front+mid sample) → ocr_text (0e6e53b).
- text_layer clean AND len >= len(ocr_text) → text_layer (digital-design, 978).
- clean but shorter than ocr → ocr_text (full-body OCR wins on coverage).
+5 unit tests (test_body_source.py). 80 mapping/xml/body tests pass.

## Verification — tests_v14 vs tests_v13 (full corpus)
real 2500→2578 (+78); effective 2551→2630 (+79); duplicates 4→2; ZERO regressions.

**The +78 count massively understates it — this was a SYSTEMIC bug hitting every book
whose ToC escalated to OCR** (full_text was ToC-OCR, not body):

| book | real | exact matches | content median | note |
|------|------|---------------|----------------|------|
| digital-design | 24→97 | 8→63 | 26→1977 | the headline case |
| Клейнман | 40→40 | 4→40 | 2345→7216 | count masked a full transformation |
| Кениг | 19→19 | 6→19 | 450→4298 | same |
| ВКР | 12→11* | 2→14 | 451→7343 | *raw −1 but eff 13→14 |
| 978 | 21→26 | — | 224→2052 | page_cut now lands in body |
| Иглмен | 18→19 | — | — | minor + |
| other 14 | unchanged | — | — | not OCR-escalated → ocr_text None → same path |

- Клейнман: all 40 sections now exact text-matches with correct body
  ("Жан Пиаже" → "ЖАН ПИАЖЕ (1896–1980) Развитие детей"). Was 36 page_cut.
- ВКР "−1" is CORRECT: subsections grew 3–6× (3.2: 5234→31738; 3.3: 58→12264);
  the −1 is chapter shells correctly emptying (content moved into subsections).
  effective went 13→14. Not a regression.

## Two prior memory diagnoses were WRONG (superseded)
- "Клейнман: non-linear pagination, 36 page_cut" → actually this body-source bug.
- "Кениг: subsection-into-toc residual" → same bug.

## Remaining problems (post-fix)
- digital-design: 46 page_cut (OCR-extracted ToC titles don't string-match the text-layer
  body). Of those: 40 have >500 chars (land in body), ~7 junk (front-matter), only 1 truly
  lost (<100). NEXT candidate: fuzzy / OCR-aware match for the unmatched titles now that
  the real body is available to search.
- 12_100229: 120 page_cut — separate issue (heuristic book, never escalated OCR;
  untouched by this fix).
- MIL-STD 5.1.2.5 bloat — accepted earlier.

## Files
- `app/services/pdf_parser_neural.py` — `_pick_body_source` + body-source selection.
- `tests/test_body_source.py` — +5 tests.

## Insight filed
- [[insight_2026-06-28_body-source-was-toc-ocr-not-body]]
