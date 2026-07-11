# BUG: Release It! — 7 front-matter sections conf=0.00 after session 001
_Filed: 2026-05-28 session 001 | Status: fixed_
_Fixed: 2026-05-28 session 001_

## Symptom
`7128_p_Нейгард_М_Release_It!` XML: 34 sections total, 7 have `confidence=0.00` and empty content.
All 7 in front matter / early chapter 1 (pages 13–24):
- "Для кого предназначена эта книга?" (p.13), "Структура книги" (p.14), "Благодарности" (p.15)
- "1.1." (p.17), "1.2." (p.18), "1.5." (p.20), "Часть I. Стабильность" (p.24)

## Root cause (confirmed)
These section headings are rendered as **large decorative typography** — PyMuPDF extracts them
as empty or not at all. The title text doesn't exist in the body text stream.

`find_real_indices` finds these titles only INSIDE the ToC pages (where they appear as plain text
in the table of contents). `_verify_and_correct_order` → `_looks_like_toc_content` correctly
reverts all 7 as `reverted_in_toc`. Then rescue (page-hint, fuzzy, embedding, LLM) all fail
because the target text is physically absent from body pages.

Diagnostic confirmed via direct `_search_with_confidence` in window around estimated position:
- "Благодарности" p=15: hits=0/1 — word absent from body
- "1.2. Важность интуиции" p=18: hits=0/2 — words absent
- "Часть I. Стабильность" p=24: hits=1/2 — only "часть" (too common)

## What we tried (that didn't work)
- ❌ Assumed it was a `_split_sticky_toc_lines` or dedup regression — was wrong. The headings
  were never in body text; they're large-typography elements PyMuPDF can't extract.
- ❌ Looking at mapping_pipeline.py code without running — couldn't identify cause without
  adding debug logging and running diagnostics.

## Fix
Added `page_cut` fallback to `mapping_pipeline.py::map_sequence`:
- Runs after ALL rescue tiers, for sections still at `start_idx=-1` with a known `page`
- Sets `start_idx = end_idx = _estimate_position_from_page(page, total_pages, full_text_len)`
- `confidence=0.30`, `match_strategy='page_cut'`
- Content is sliced from estimated page position to next found section

Also:
- `pdf_parser_neural.py`: page_cut sections skip `fix_chapter_boundary` (no heading to trim),
  go directly to `fast_clean_chunk`
- `mapping_pipeline.py::_verify_and_correct_order`: page_cut sections skipped in out-of-order
  check and do NOT update `running_max` (approximate positions must not anchor ordering logic —
  see insight `insight_2026-05-28_page-cut-must-not-anchor-running-max.md`)

## Result
| Metric | Before fix | After fix |
|--------|-----------|-----------|
| real_content_sections | 25/34 | 31/34 |
| avg_confidence | 0.654 | 0.716 |
| reverted_in_toc | 7 | 0 |
| reverted_out_of_order | 0 | 0 |

## Why this happened / how to prevent
This is the same architectural issue as Иглмен (6 not-found sections). Large/decorative
typography in PDFs is not extractable by PyMuPDF as plain text. Any section whose heading
is ONLY rendered as a graphical element will fail all text-based rescue strategies.

`page_cut` is the correct long-term fallback for this class of problem.

## Affected files
- `app/services/mapping_pipeline.py` — page_cut fallback + out-of-order exclusion
- `app/services/pdf_parser_neural.py` — page_cut path in clean step
