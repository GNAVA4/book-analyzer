# Session 013 — 2026-06-28
← [session_012.md](./session_012.md)

## Goal
Detailed analysis of 12_100229's remaining problems (user shared XML screenshots),
then implement "Mechanism 2": the per-page watermark «Библиотека БГУИР» (787× = once per
page) polluting every section's content, which clean_footer_header missed.

## Mechanism 2 — per-page watermark removal
### Root cause
`HEADER_JUNK_MIN_LEN = 20`, condition `len(s) > 20`. «Библиотека БГУИР» = 16 chars →
slips under the threshold, never removed despite 787 occurrences. Can't just lower the
length floor — it would delete stop-words («и» 115×, «в» 71×).

### Two traps hit during implementation (both caught before commit)
1. **Position churn.** First version cleaned the watermark from full_text BEFORE mapping
   → text 15k shorter → page_cut linear positions shifted → 136 sections lost >50 chars,
   incl. REAL ones (§8.3 1947→40, §11.1 8072→33). FIX: decouple — keep full_text intact
   for mapping (positions = v14), strip the watermark only from each section's sliced
   CONTENT. New `footer_junk_lines()` + `strip_junk_lines()`; clean_footer_header composes
   them. After fix: §8.3 stays 1947, only 9 sections with |Δ|>200.
2. **Content deletion risk.** Threshold 0.30 (line on >30% of pages) catches not just
   watermarks but repeated CONTENT: parallelnoe «Объявление» 39% / «Результат» 32% (code
   labels), ВКР «Дальневосточный»/«Северо-Западный» 31% (federal-district names in stat
   tables!). Raised threshold to **0.60**: true watermarks are on 82–100% of pages
   («Библиотека БГУИР» 100%, «MIL-STD-1472G» 100%, «Chapter» 82%), content on ≤39%. The
   39%↔82% gap is clean. (Also: footer_junk_lines matches exact stripped LINES, so the
   substring-count scare for «Chapter»/«Объявление» overstated — real line-removal is even
   safer; only «Библиотека БГУИР» and «MIL-STD-1472G» actually get removed corpus-wide.)

### Implementation
- `HEADER_WATERMARK_PAGE_FRACTION = 0.60`, `HEADER_WATERMARK_MIN_LEN = 5` (pdf_utils).
- `footer_junk_lines(text, total_pages)` (long headers >20/×4 + short per-page watermarks
  on >60% pages), `strip_junk_lines(text, junk)`.
- pdf_parser_neural: full_text = clean_footer_header(full_text) [long headers, = v14],
  footer_junk computed once, stripped from each section's raw_chunk at slice time.
- pdf_parser_fast reverted to old clean_footer_header (no churn there).
- +4 unit tests. 340 pass.

### Verification (targeted)
- 12_100229: «БГУИР» in previews 45→0 (~787 instances removed from content). Positions
  stable (§8.3 1947→1947). real 302→294 (−8) — HONEST: the 8 were junk sections (running
  headers «Функциональные узлы…», «Гпава 4», page-number lists) padded above 100 by the
  watermark; revealed as <100 real. Not loss.
- MIL-STD: «MIL-STD-1472G» 22→0. real 596→595 (−1 honest). 5.1.3.1 real content intact
  (5595→5550, only watermark removed).
- Other 17 books: unchanged (no ≥60%-page short watermark).

## Other 12_100229 problems identified (NOT yet fixed — from user screenshots)
Decomposed Class 1 into distinct mechanisms (analysis, not implemented):
- M1: page_cut → front-matter ToC leader-dots (~10 early sections). ToC = first ~50k chars.
- M3: page_cut slice absorbs a big ToC block («Линии передачи токовая петля» = 8458 of ToC).
- Running-header + underscore FILL noise («…микросхемы______», «Гпава 4____») — survives
  clean_footer_header (underscores make it non-identical).
- Short chapter headers «Глава 9»/«Гпава 3» (<20 chars, ~30× = per-chapter) not removed.
- Spurious glossary/index sections («микроконтроллерах» p721 → «и БИС» 5 chars; «Приложение
  2» → BEDORAM/BIST/BSCs defs): ToC extracted glossary terms as sections.
- Titles absent from body (only in ToC) → forced page_cut (the 110 body-landing page_cut
  have real-ish prose; the ~10 ToC-landing ones are junk).

## Files
- `app/services/pdf_utils.py` — footer_junk_lines / strip_junk_lines / watermark fraction.
- `app/services/pdf_parser_neural.py` — content-level watermark strip (decoupled).
- `app/services/pdf_parser_fast.py` — reverted to old clean_footer_header.
- `tests/test_pdf_utils.py` — +4 watermark tests.

## Insight filed
- [[insight_2026-06-28_watermark-vs-content-frequency]]
