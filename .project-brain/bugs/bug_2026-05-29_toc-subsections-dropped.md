# BUG: Heuristic ToC parser drops subsections (two distinct mechanisms)
_Filed: 2026-05-29 session 004 | Status: FIXED (A heuristic, B smart LLM fallback)_
_Fixed: 2026-05-29 session 004_

## Symptom
NavigationTable is missing whole levels of subsections that exist in the PDF's printed ToC,
even though the ToC pages are within the first 20 pages (which the heuristic reads).
- Release It!: subsections 2.1–2.5, 3.1–3.5, 4.1–…, 7.1–…, etc. ALL missing. Only ch.1 keeps 1.1–1.6.
- Розенсон: nearly all subsections under each Глава missing (e.g. "Вещь и Дизайн", "Вещь и Культура").

Downstream effect: with no subsection anchors, a chapter is one undifferentiated blob, which makes
the content-misplacement bug worse (see [[bug_2026-05-28_part-divider-absorbs-chapter-body]]).

## To reproduce
Run `HeuristicParser().parse_toc(_split_sticky_toc_lines(raw_first20pages))` on either book and
dump `toc_to_linear_sequence`.

## Root cause — TWO independent mechanisms

### Mechanism A — Release It! (dual ToC + page-less divider)
The book has BOTH "Краткое содержание" (chapters only, no subsections) and full "Содержание"
(with all subsections), consecutively in the first pages. Both are parsed in one pass into one tree.
- The brief contents adds "Часть I. Стабильность" (a divider with NO page number) to `seen_titles`.
- While parsing the full contents, after "1.6 … 21" the next line is again
  "Часть I. Стабильность" (no page). In `parse_toc`, line ~125:
  `if not has_page and self._is_content_start(norm_line, seen_titles): break`
  `_is_content_start` matches the page-less divider against the already-seen title → returns True →
  **the whole ToC parse breaks at 1.6**. Everything after (2.x … 18.x) is lost.
- The chapter-level entries still appear because they came from the brief contents block.

Confirmed: raw text after "1.6. Прагматичная архитектура … 21" is "Часть I. Стабильность\n2. …".

### Mechanism B — Розенсон (title/page on separate lines, non-numbered subsection titles)
Subsections are printed as: title line, then page number on the NEXT line, and the titles are
plain prose ("Вещь и Дизайн") — not "Глава"/"§"/numbered.
- Only `structure_start`-matching lines become `pending` (SCENARIO Б).
- A plain subsection title does NOT match `structure_start`, so it never becomes `pending`.
- The following bare page-number line then has no `pending` to attach to → counted as a miss → dropped.
So any non-numbered subsection whose page sits on the next line is silently lost.

## What we tried (that didn't work)
- ❌ Treating this as a content-mapping problem — it's upstream: the items never enter the sequence,
  so mapping never had a chance.

## Fix A (applied + verified, session 004)
In `toc_parser.py::parse_toc`:
1. Length guard before the item regexes — skip `item_pattern`/`loose_item_pattern` on lines >200
   chars. Those lazy `.+?/.*?` + separator-alternation patterns cause catastrophic backtracking on
   long body lines; once the parser reads into body (Клейнман) it could hang. Pure safety, no behaviour
   change for real ToC items (always <200 chars).
2. `_is_content_start` break is suppressed when the page-less line is a "Часть/Part/Раздел" divider:
   in dual-ToC books that divider recurs inside the detailed ToC and is not the start of body text.

```python
if not has_page and self._is_content_start(norm_line, seen_titles):
    if not re.match(r'^\s*(часть|part|раздел)\b', line_raw, re.IGNORECASE):
        break
```

### Result A (heuristic count OLD nav → NEW heuristic), verified across all 11 books + 233 tests pass
- Release It!: 34 → 153 (106 L2 subsections, all 2.x–18.x captured). FIXED.
- Heuristic-source books unchanged: Do Good 18, Виды 15→16, MIL-STD 747→750, Клейнман 40→41,
  Массель 23→24, parallelnoe 248. No regression.
- Кениг/Иглмен/0e6e53b show heuristic 0/6/0 — NOT regressions: these use LLM/OCR for ToC
  (`toc_source = llm/ocr`); heuristic was always insufficient and still falls back as before.

## Mechanism B (Розенсон) — DEFERRED, not fixed
Розенсон is genuinely hard for the streaming heuristic: (1) subsection titles are non-numbered and
their page sits on the NEXT line; (2) many subsection titles WRAP across 2–3 lines before the page;
(3) recurring subsections ("Вопросы для проверки") repeat under every chapter.
Attempts to patch this (next-line lookahead, pending-accumulation, misses==0 gate) each traded one
book for another: ungated → Розенсон complete (85) but Клейнман read into body and collected garbage
(75 w/ dedication + body numbered-lists); misses==0 gate → Клейнман clean (57) but Розенсон truncated
at Глава 6 (lost Главы 7–9) because wrapped titles inflate the miss counter.
Decision (chosen): smart LLM/OCR fallback with validation — see [[adr_003_smart_llm_toc_fallback]].

## Fix B (applied + LIVE-verified, session 004)
- `_looks_incomplete(seq, raw_text)` in toc_builder — algorithmic trigger (no models): raw ToC
  entry-lines / heuristic items > 1.5 ⇒ incomplete ⇒ run fallback.
- `_llm_from_text_retry` — LLM extract with up to 3 retries on formal/CJK errors.
- `_select_best` — scores candidates (heuristic/llm/ocr_llm) via `toc_validate.score_toc`, picks the
  best VALID one (formal + no CJK + grounding≥0.8); else keeps heuristic. Never ships unvalidated LLM.
- Hardening: robust JSON salvage `_parse_toc_items` in llm_engine (recovers items from truncated/
  malformed JSON — Розенсон's first attempt failed json.loads entirely); `TOC_MAX_TOKENS=3000`;
  clamp LLM item level to 1..3 in `_normalize_llm_items` (LLM emitted level 4 → had falsely
  invalidated a perfect 77-item ToC).

### Live result (LM Studio, qwen2.5-7b)
- Розенсон: heuristic 20 → **llm 77** (71 subsections), grounding 1.0, 0 CJK, formal clean. SELECTED.
- Клейнман: heuristic 41 → **llm 57** (54 subsections), grounding 1.0, 0 CJK. SELECTED.
- Complete books (Виды UI 15, parallelnoe 248, Release It! 153) → still `heuristic`, NO model calls
  (trigger doesn't fire). Zero regression.
- 260 unit tests pass.

## Affected files
- `app/services/toc_parser.py` — `parse_toc` (`_is_content_start` break; SCENARIO Б/В pending logic)

## Detection tooling
- Dump NavigationTable levels vs the printed ToC; compare. `scripts/audit_content.py` also flags the
  downstream TRUNCATED/BLOATED that result.
