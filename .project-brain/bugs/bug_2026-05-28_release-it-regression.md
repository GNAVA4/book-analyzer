# BUG: Release It! — 7 front-matter sections conf=0.00 after session 001
_Filed: 2026-05-28 session 001 | Status: open_

## Symptom
`7128_p_Нейгард_М_Release_It!` XML: 34 sections total, 7 have `confidence=0.00` and empty content.
All 7 are in front matter / early chapter 1 (pages 13–24):
- "Для кого предназначена эта книга?" (p.13)
- "Структура книги" (p.14)
- "Благодарности" (p.15)
- "1.1. ..." (p.17), "1.2. ..." (p.18), "1.5. ..." (p.20)
- "Часть I. Стабильность" (p.24)

Before session 001: 33 sections, avg_conf=0.91, these sections had content.
After session 001: 34 sections, avg_conf=0.654, these 7 conf=0.00.

## To reproduce
1. Start server with `uvicorn app.main:app`
2. Load model qwen2.5-7b-instruct with -c 8192
3. Run: `python scripts/run_pipeline.py "test/7128_p_Нейгард_М_Release_It!...pdf"`
4. Open resulting XML, filter `confidence="0.00"` sections

## Root cause
Unknown — investigation started but not finished. Hypotheses (most likely first):

**H1 (most likely):** `_looks_like_toc_content` fires on these sections because their exact-match
position is INSIDE the ToC pages (pp. 8–12). `_verify_and_correct_order` marks them `reverted_in_toc`.
Then rescue window for pages 13–24 is wrong/too narrow — doesn't find them.

**H2:** `exact_normalized` promotion (new in session 001) gives these sections conf=1.0 →
page-distance tolerance triggers (30% of text_len) → marked as suspect → backup/restore cycle fails.

**H3:** `_split_sticky_toc_lines` applied to PyMuPDF raw_text changed `sequence` structure for
this book specifically, shifting page estimates and breaking the rescue window calculation.

## What we tried (that didn't work)
- ❌ Looked at mapping_pipeline.py code — didn't find obvious cause without running with debug logs
  Next needed: add logging to `_verify_and_correct_order` to print which check fires for each section

## Fix
Not yet applied. Next step:
1. Add temporary debug print in `_verify_and_correct_order` to log: section title, which check fired
2. Re-run Release It! and check logs
3. Based on logs: adjust rescue window or `_looks_like_toc_content` sensitivity for front-matter pages

## Why this happened / how to prevent
Session 001 changes to dedup and sticky-line splitting changed the `sequence` structure enough
to affect page position estimates and match positions. Need regression test that runs Release It!
and asserts avg_conf > 0.80 and not_found < 3.

## Affected files
- `app/services/mapping_pipeline.py` — verify logic (suspected)
- `app/services/toc_builder.py` — dedup + sticky lines changed sequence (suspected trigger)
