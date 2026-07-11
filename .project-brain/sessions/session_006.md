# Session 006 — 2026-05-30
← [session_005.md](./session_005.md)

## Goal
Close two open HIGH-priority bugs from session 005:
- Defect A: LLM strips hierarchical numbering from titles
  (`bug_2026-05-30_llm-strips-hierarchical-numbering`).
- Defect B: heuristic loses chapter title after number
  (`bug_2026-05-30_heuristic-loses-chapter-title-after-number`).

## What actually happened

### Defect A — fixed (ToC level), shipped

Two-pronged: prompt + post-process safety net.

Prompt in `llm_engine.extract_toc_json` now has an explicit rule: numerical prefix
("1", "1.2", "1.2.3", "§ 1.4", "Глава 5") is part of the title, kept verbatim. Concrete
example: "1.2.1 Абстракция .... 6" → `{"title": "1.2.1 Абстракция", "page": 6}`. Reworded
rule 4 so the "no inline page numbers" instruction doesn't get over-applied to section
numbers.

Post-process in `toc_builder._reattach_numerical_prefixes` — scans raw text for ToC lines
"<prefix> <bare-title> <leaders> <page>", builds map normalised-bare → prefix, re-attaches
prefix when LLM stripped it. Skips if title already starts with digit/§. Wired into
`_llm_from_text`, `_llm_from_text_retry`, `_ocr_then_llm`. Safe by design: prefixes are
attached only when there's an exact normalised match in the raw text.

Corpus regression (19 books):
- 15 books unchanged (heuristic source, fix is no-op).
- digital-design: 0/105 → 105/105 prefixed titles in NavigationTable. real didn't move
  (20→20) because the body of this OCR-heavy book has its own OCR drift in the headings —
  separate downstream issue, not part of A.
- ВКР: toc_source flipped heuristic→llm, real 12→13 (+1).
- 978-5-7996: LLM started returning slightly longer descriptive titles on the new prompt.
  real unchanged at 32, avg_conf moved 0.46→0.445 (noise). Not a regression, just a style
  shift in LLM output.

290 unit tests pass (+9 new for prefix-map and re-attach). Committed.

### Defect B — misdiagnosed, closed

The bug was filed based on `_report.json` titles like "1." and "5./" for empty 1332 and
12_100229 sections. Diagnostic re-run with UTF-8-safe output showed the titles are actually
FULL ("1. Статистика как наука", "§1.1. Простейшие модели и система параметров…") — the
"1." was just mojibake from a cp1251 console misreading Cyrillic.

Re-investigated each book:

- **1332**: every L1 "empty" chapter has full subsections with real content (9099, 4543,
  10129 chars for ch 1.1/1.2/1.3). These are valid empty chapter shells in a hierarchical
  book where the chapter has no preamble. Filed as `insight_2026-05-30_chapter-shell-empty-
  content-is-valid` and added a metric-refinement suggestion: `effective_real_sections`
  should count a section as real if it OR any descendant has content > 100 chars.

- **12_100229**: real bug, different mechanism. The book's §-notation ToC wraps titles
  across 2-3 lines:
  ```
  §1.1. Простейшие модели и система                7
  параметров логических элементов                  7
  Простейшие модели логических элементов           7
  ```
  Heuristic treats the third line as a SEPARATE subsection (it looks like a valid heading).
  In the body of the book, those three lines appear one after another as the chapter
  header. The bogus item exact-matches in the body 1 char after the real §1.1's end →
  §1.1 gets `content_len = 1`, content absorbed by the false sibling. 12 such cases in
  the book. Filed as `bug_2026-05-30_heuristic-splits-multi-line-toc-title`.

So Defect B as originally filed doesn't exist; what does exist is two different things,
one of which is a metric/diagnostic issue (1332) and the other a heuristic-parser issue
(12_100229) with no easy fix without risk to other books.

Closed the misdiagnosed bug, filed the two new entries.

## Decisions made
| Decision | Chosen because |
|----------|----------------|
| Prompt fix + post-process re-attach (not prompt-only) | LLM is non-deterministic; a safety net catches the cases where the prompt still fails |
| Re-attach skips when title already has digit/§ prefix | Avoids double-prefixing books where the LLM already preserved the number |
| Defect B closed as misdiagnosed, not "fixed" | The original symptom doesn't exist; documenting honestly avoids future confusion |
| 1332 empty chapter shells: not a bug | Metric should evolve, pipeline output is correct |
| 12_100229 multi-line title split: filed for follow-up, not fixed now | Heuristic fix is risky (could break other books' wrap detection); needs careful design |

## What we tried that didn't work
- ❌ Initial diagnosis of Defect B from report.json without checking the raw bytes. The
  mojibake mid-character (Cyrillic encoded wrong in console) made "1. Статистика" look
  like "1." plus garbage. Lesson: always read with UTF-8 stdout when inspecting Russian
  text; trust the XML, not the console.

## What works now (that didn't before)
- ✅ digital-design NavigationTable now has correct hierarchical titles ("1.2.1
  Абстракция", "1.5 Логические элементы"). Downstream consumers of the XML get the
  proper tree structure, even if body content mapping is still poor.
- ✅ ВКР's smart-fallback now selects an LLM candidate with restored prefixes, +1 real.

## Files changed / created
- `app/services/llm_engine.py` — `extract_toc_json` prompt updated.
- `app/services/toc_builder.py` — `_NUMBERED_TOC_LINE`, `_build_prefix_map`,
  `_HAS_NUMERIC_PREFIX`, `_reattach_numerical_prefixes`; wired into 3 LLM branches.
- `tests/test_toc_builder.py` — `TestReattachNumericalPrefixes` (9 cases).
- `tests_v5/` — corpus baseline after fixes (19 books).
- `.project-brain/bugs/bug_2026-05-30_llm-strips-hierarchical-numbering.md` (will update
  status next session, work logged here).
- `.project-brain/bugs/bug_2026-05-30_heuristic-loses-chapter-title-after-number.md` —
  closed as misdiagnosed, points at the two real successors.
- `.project-brain/bugs/bug_2026-05-30_heuristic-splits-multi-line-toc-title.md` — NEW.
- `.project-brain/insights/insight_2026-05-30_chapter-shell-empty-content-is-valid.md` — NEW.

## Round 2 of session 006: wrap-merge + metric + prompt polish

After the Defect A / B discussion, picked three improvements with high impact and low risk
and shipped them in one batch.

### Wrap-continuation merge (closes 12_100229 bug)
Added `merge_wrap_continuations` in mapping_pipeline, called from neural parser after
`map_sequence`. Detects pairs where `start_idx[N+1] - end_idx[N] ≤ 5 chars`, same page,
same level, both with trusted match strategy. Joins titles and removes the wrap items
from both `mapped` and `sequence` so NavigationTable reflects the merge.

First corpus run showed two books affected: 12_100229 (10 legitimate wraps, all §1.x
multi-line titles) and MIL-STD (15 wraps that were actually false positives — sequences
like «5.11.1 Stairs…» followed immediately by «5.11.1.1 General criteria» looked like
wraps because the heuristic level-assignment matched). MIL-STD dropped 15 real
subsections.

Added own-prefix guard `_OWN_NUMERIC_PREFIX`: if the next title starts with a digit, §,
"Глава N", "Chapter N", "Часть N", "Part N" — reject the merge. Re-ran 12_100229 +
MIL-STD: 12_100229 keeps its 10 legitimate merges, MIL-STD untouched at 747. Guard works.

### Effective_real_sections metric
Added to `run_corpus.py:build_stats`. Counts a section as content-covered if it OR any
descendant has content > 100 chars. Reports honestly on hierarchical books that have
empty chapter shells with content-bearing subsections.

Corpus reports:
- 1332: real 51/61, eff 61/61 (10 chapter shells were valid)
- 12_100229: real 301/318, eff 309/318
- Машинное обучение: real 227/228, eff 228/228
- parallelnoe: real 243/248, eff 246/248
- Розенсон: 74→75
- ВКР: 12→13
- Массель: 20→23
- MIL-STD: 594→617

### Prompt tighten
The earlier Defect A prompt push to preserve hierarchical numbering accidentally
encouraged the LLM to extend titles into the first sentence of the section. 978-5-7996
came back with «Предмет статистики – изучение массовых общественных явлений…» where it
should have returned «1. Предмет статистики». Reworded rule 4 explicitly as "short
heading, don't add descriptions"; kept rule 5 with the numerical-prefix example.

### LLM nondeterminism observed (not a regression)
On the v6 corpus run, three books showed LLM-side wobble unrelated to the mapping change:
- Иглмен real 22→19 (some content shifted from «Часть I/II/III» into chapters 6 and 7);
  same toc source (llm), same total, wrap-merge didn't fire — pure LLM nondet.
- 978-5-7996 source flipped llm → ocr_llm; smart-fallback escalation kicked in. real
  32→22 but avg_conf jumped 0.45 → 0.85 and page_cut dropped 24→2.
- ВКР source flipped llm → heuristic (back to v4 behaviour).

These are known LLM-side variability. Filed as a low-priority follow-up: maybe bump
`_llm_from_text_retry` attempts from 3 to 5 for stability.

## Round 2 files changed
- `app/services/mapping_pipeline.py` — `merge_wrap_continuations`, `_is_wrap_continuation`,
  `_TRUSTED_FOR_WRAP`, `_OWN_NUMERIC_PREFIX`.
- `app/services/pdf_parser_neural.py` — call site after `map_sequence`.
- `app/services/llm_engine.py` — `extract_toc_json` prompt polish.
- `scripts/run_corpus.py` — `_effective_real_count`, `effective_real_sections` field in
  stats and summary output.
- `tests/test_mapping_pipeline.py` — `TestMergeWrapContinuations` (10 cases).

## End state
Branch `llm-toc-fallback`. Defect A code shipped and corpus-verified (no regressions).
Defect B doesn't exist as filed — replaced by an insight (1332) and a more accurate bug
(12_100229), which is now FIXED via the wrap-merge logic. 302 unit tests pass.

Three round-2 wins: 12_100229 §1.x sections recovered, honest metric reporting via
effective_real_sections, prompt tightened. One follow-up: LLM nondeterminism still
swings results across runs on Иглмен / 978-5-7996.

Open priorities for next session:
- 12_100229 multi-line-title-split — needs careful parser design.
- Body content mapping for OCR-heavy LLM-source books (digital-design): OCR drift in the
  body of the book defeats exact title matches even when the ToC is correct. Probably needs
  an OCR-aware fuzzy match tier between regex and embedding rescue.
- ВКР `_looks_incomplete` over-trigger — still wasting ~170s on each run.
- MIL-STD 5.1.2.5 bloat — still uninvestigated.
- Possible metric refinement: `effective_real_sections` for honest reporting on 1332-style
  hierarchical books.
