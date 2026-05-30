# BUG: LLM/OCR-LLM ToC extraction strips hierarchical numbering from titles
_Filed: 2026-05-30 session 005 | Status: open_

## Symptom
On books where ToC source ends up `llm` or `ocr_llm`, the extracted titles drop their
hierarchical numbers ("1.2.1 Абстракция" → "Абстракция", "1.1 План игры" → "План игры").
This makes section titles generic and ambiguous in the body — `find_real_indices` matches
the first occurrence of the bare word/phrase, which is rarely the right one. Result:
section content is a snippet (10–100 chars) or a wrong neighbour's body.

Concrete (tests_v4):
- **digital-design**: 105 sections, only 20 real (>100 chars), **79 tiny** (<100 chars).
  avg_conf 0.362, 83 page_cut. Titles: «Абстракция», «План игры», «Конструкторская дисциплина»
  — body uses «1.2.1 Абстракция», «1.1 План игры», «1.2.2 Конструкторская дисциплина».
- **978-5-7996**: 32 sections, 24 page_cut, avg_conf 0.46. Titles: «Общие положения»,
  «Предмет статистики», «Признак».

Both books have toc_source ∈ {llm, ocr_llm}. Heuristic-source books in the same corpus
(bookфизика 475/474 real, Машинное обучение 228/227 real) keep numerical prefixes and map cleanly.

## To reproduce
1. Run a book whose first 20 pages can't be parsed by heuristic (forcing LLM fallback).
2. Inspect the NavigationTable — items will have no numerical prefix.

## Root cause (hypothesis — needs code verification)
1. The LLM `extract_toc_json` prompt likely either implicitly or explicitly produces "clean"
   semantic titles. The model normalizes "1.2.1 Абстракция" to "Абстракция".
2. `_normalize_llm_items` doesn't add the numbers back.

Verify by inspecting the prompt in `llm_engine.extract_toc_json` and a few raw LLM outputs.

## Fix (directions)
- **Prompt fix**: instruct LLM to preserve all numerical/letter prefixes verbatim
  ("Include section numbers like '1.2.1' as part of the title if present in the source.").
- **Post-processing**: if raw ToC text contains "<digits...> <title>" patterns that match
  LLM-extracted bare titles, re-attach the prefix.
- **Mapping side**: when a title is generic (single common word, short), search **near**
  the expected page position rather than first occurrence in book. Currently page-hint
  fires only as a rescue tier; making it a primary strategy for generic-looking titles
  would reduce mismatching.

## Affected files
- `app/services/llm_engine.py` — `extract_toc_json` prompt.
- `app/services/toc_builder.py` — `_normalize_llm_items` (could re-attach prefixes).
- Maybe `app/services/mapping_pipeline.py` — page-hint priority for generic titles.

## Detection tooling
- `scripts/audit_content.py` flags this indirectly (low ratio).
- Better signal: in `_report.json` count titles matching `^\d+(\.\d+)*\.?\s+\S` —
  bookфизика 474/475, digital-design 0/105.

## Compounding effect for digital-design
Beyond the numbering issue, this book also has **OCR drift in titles** themselves
(«прицелы» / «принципы», «Наляржение» / «Напряжение», «Дициплина» / «Дисциплина»).
These typos are below FUZZY_THRESHOLD=0.85 so fuzzy_rescue can't recover them. Separate
mechanism but worsens this book's outcome.
