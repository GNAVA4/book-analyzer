# BUG: Part-divider (page_cut) absorbs the body of the chapter sharing its page
_Filed: 2026-05-28 session 004 | Status: open_

## Symptom
A section exists in XML with high confidence (0.90) but its `<content>` is a tiny fragment,
while an adjacent low-confidence divider section holds the text that belongs to it.

Release It! example (test_v2):
- `2. Исключение, помешавшее работе авиакомпании` (p.24, conf=0.90) → content_len **1893**, expected ~29984.
  Its content is the TAIL of chapter 2 (a thread dump), not the chapter.
- `Часть I. Стабильность` (p.24, conf=0.30, page_cut) → content_len **30611**, expected ~0.
  Its content is the BODY of chapter 2 (ends at "Глава 3 •" = start of ch3).

Same pattern in the front matter:
- `Предисловие` (conf=0.90) content starts with the heading "Структура книги" (LEAK) — absorbs the
  bodies of the following subsections, which are then truncated (`Структура книги` 547, `Благодарности` 99,
  `1.1` 193 chars).

## To reproduce
1. Process Release It! PDF
2. Audit: `venv/Scripts/python.exe scripts/audit_content.py "Release"`
3. See section 13 `2. Исключение` ratio=0.06 (TRUNCATED) and section 12 `Часть I` clen=30611 exp~0.

## Root cause (confirmed)
"Часть I. Стабильность" and "2. Исключение" are on the SAME page (24). The chapter-2 heading is
rendered as large/decorative typography → absent from the PyMuPDF body text. So:
- "Часть I" gets a `page_cut` start position at the real start of the chapter-2 body.
- "2. Исключение" is text-matched only at a small fragment LATER in the chapter (a code/dump block).
- Content slicing assigns [Часть I.start .. 2.Исключение.start] to "Часть I" (≈ whole chapter) and
  [2.Исключение.start .. ch3.start] to "2. Исключение" (≈ tail fragment).

Net: content is NOT lost (book coverage = 0.972) but **mis-attributed** to the neighbouring divider.
Coverage metrics and confidence look fine — the bug is only visible when comparing per-section
content volume to the expected page-range volume.

## What we tried (that didn't work)
- ❌ Trusting fitz-vs-fitz coverage alone — coverage is high (0.972) because text isn't lost, only
  misplaced. Per-section ratio (content_len / expected page-range len) is what exposes it.

## Fix
Not yet applied. Candidate directions (need design decision):
- When a `page_cut` divider and a text-matched section share a page (or are adjacent), the divider
  should not own body text — clamp divider content to near-empty and let the chapter own the slice.
- Or: detect that the section AFTER a divider has start_idx > divider.start and reassign the slice.
- Front-matter LEAK: a high-conf section whose content's first line equals the NEXT section's title
  is over-extended — boundary should be cut at that heading.

## Affected files
- `app/services/mapping_pipeline.py` — page_cut positioning + slice attribution
- `app/services/pdf_parser_neural.py` — content slicing between consecutive sections

## Detection tooling
- `scripts/audit_content.py` — per-section content_len vs expected page-range len (ratio), LEAK/DUP/TRUNC flags
