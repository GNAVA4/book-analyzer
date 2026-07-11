# Insight — mapping used ToC-OCR text as the body when a readable doc's ToC escalated to OCR

_Filed: 2026-06-28 (session 012). Provenance: cached digital-design extraction +
tests_v14 full corpus._

## The bug
`parse_pdf_neural`: `full_text = ocr_text if ocr_text else get_all_text(doc)`.
`build_toc`, when the heuristic/LLM ToC is incomplete, ESCALATES to OCR but only OCRs
the ToC pages (`ocr_document(max_pages=OCR_TOC_PAGES)`) and RETURNS that short text as
`ocr_text`. For a READABLE document (body fine in the PDF text layer), this short ToC-OCR
then overrode the full readable body → mapping searched ~25k of ToC instead of the real
~1.5M body. digital-design: 94 sections page_cut into front-matter; real 24/112.

## Why it hid for so long
- The `real_content_sections` count didn't expose it: page_cut sections still scored
  >100 chars (from front-matter/preface), so the count looked "ok-ish".
- It was MISDIAGNOSED per-book: "Клейнман non-linear pagination", "Кениг subsection-into-toc",
  "digital-design OCR drift" — all were the SAME body-source bug. Each book that escalated
  its ToC to OCR was affected.

## The fix
`_pick_body_source(text_layer, ocr_text)`: choose the body by directly comparing the two
candidate texts (quality via detect_garbage_text on a front+mid sample, and coverage via
length), instead of trusting the pre-sampled readability flag:
- garbage text layer → OCR (0e6e53b stays correct);
- clean text layer with length >= ocr_text → text layer (digital-design, 978, Клейнман,
  Кениг, ВКР);
- clean but shorter than ocr → ocr (full-body OCR wins on coverage).

## Impact (tests_v14)
real 2500→2578, effective 2551→2630, dups 4→2, zero regressions. But the count understates
it: Клейнман 4→40 exact matches (median content 2345→7216), Кениг 6→19, ВКР 2→14, all with
correct full body instead of page_cut approximations.

## Lessons
- Don't trust a pre-sampled boolean flag for a per-document decision when you can compare
  the actual artifacts (user's point). The readability flag sampled pages; the real test is
  the extracted text itself.
- The `real>100` count is a trap (again): a page_cut into front-matter counts as "real".
  Judge by match_strategy mix and content correctness, not the count.

Supersedes the page_cut-related framing in the Клейнман/Кениг notes. Related:
[[insight_2026-06-27_pagecut-dup-is-same-page-collision]],
[[insight_2026-06-27_v6-linear-best-on-AI-ordering-guard-cascades]].
