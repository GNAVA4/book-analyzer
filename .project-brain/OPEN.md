# OPEN ITEMS — Book Analyzer
_Last updated: 2026-05-30 session 005_

## In progress
_(nothing active)_

## Done this session (was in progress)
- [x] **VKR ToC duplicate fix** (`_drop_fuzzy_pageless_dupes`) — code applied, 12 unit tests
  pass, live ВКР prod XML no longer has trailing «ГЛАВА 1» dupe. См. bug_2026-05-29_vkr-toc-
  duplicate-chapter (updated status).
- [x] **OCR 400 recovery** (`_extract_text_from_ocr_error`) — FIXED live-verified on 0e6e53b
  (4 pages, 1757 chars recovered). См. bug_2026-05-29_ocr-drops-parseable-pages (FIXED).
- [x] **Corpus run 19 books** → tests_v4 — new baseline. 12 books v3↔v4 diff analyzed (mostly
  no-op for my fixes), 7 new books surfaced 2 new defect classes.

## TODO (новые приоритеты — после нового корпуса)
- [ ] **Дефект А: LLM strips hierarchical numbering** — digital-design (20/105 real, ocr_llm),
  978-5-7996 (24/32 page_cut, llm). См. `bug_2026-05-30_llm-strips-hierarchical-numbering`.
  Подходы: фикс prompt + post-process в `_normalize_llm_items` (re-attach prefix из raw text).
- [ ] **Дефект Б: Heuristic loses chapter name after number** — 1332 (10 пустых «1.», «2.»),
  12_100229 (6 пустых + дубли split-а). См. `bug_2026-05-30_heuristic-loses-chapter-title-
  after-number`. Подход: multi-line lookahead в `HeuristicParser`.
- [ ] **MIL-STD 5.1.2.5 absorbed +61k chars** — Class-2 residual? Замерить через
  `audit_content.py` на v4, проверить, какая секция реально должна владеть этим текстом.
- [ ] **Клейнман: live 36 page_cut vs CONTEXT claim 0** — `scripts/mapping_audit.py` показывает
  одно, реальный пайплайн — другое. Разобраться где правда и обновить.
- [ ] **OCR digital-design — drift в titles** («прицелы»/«принципы»). Below FUZZY_THRESHOLD=0.85.
  Опционально: понизить порог fuzzy_rescue для коротких слов, или второй проход с OCR-aware
  edit-distance.

## TODO (продолжающиеся из сессии 004)
- [ ] **MIL-STD 154 not-found / 92 EMPTY** — coverage .944 → text present but mis-attributed; likely class 2.
- [ ] **parallelnoe appendix D** — dense std:: reference entries get mis-located by embedding_rescue.
- [ ] **report.json content_len/preview are all 0** (test_v2) — pre-existing, source of truth = XML.
- [ ] **Fix PIPELINE_DIFF.md note** — says "LLM clean is sequential", actually asyncio.gather.

## Open bugs
- [ ] `bug_2026-05-30_llm-strips-hierarchical-numbering.md` (HIGH, new) — see Дефект А above.
- [ ] `bug_2026-05-30_heuristic-loses-chapter-title-after-number.md` (HIGH, new) — see Дефект Б above.
- [ ] `bug_2026-05-29_vkr-toc-duplicate-chapter.md` — ToC dup PART closed; residual ВВЕДЕНИЕ/
  ЗАКЛЮЧЕНИЕ getting ToC fragments (mapping side, same class as subsection-into-toc).
- [ ] `bug_2026-05-29_subsection-maps-into-toc.md` — residual: Кениг «3.Свет»/«4.Текстура».
- [ ] MIL-STD ~92 short clauses exact-matched but empty.
- [ ] parallelnoe appendix D — embedding_rescue mis-locates dense std:: reference.

Synthesis: [[insight_2026-05-29_corpus-content-audit-v3]] — ToC extraction good, subsection
content mapping is the bottleneck for OCR/LLM books.

## Closed this session
- [x] `bug_2026-05-29_ocr-drops-parseable-pages.md` — FIXED 2026-05-30 (recover from 400 body
  + PyMuPDF text-layer fallback), live-verified on 0e6e53b.

## Closed bugs (prior sessions)
- [x] `bug_2026-05-28_release-it-regression.md` — session 001.
- [x] Иглмен 2 not-found — session 002.
- [x] `bug_2026-05-28_part-divider-absorbs-chapter-body.md` — session 004.
- [x] `bug_2026-05-28_do-good-design-copyright-spam.md` — session 004.
- [x] `bug_2026-05-29_toc-subsections-dropped.md` — session 004 (A heuristic + B smart LLM).
- [x] `bug_2026-05-29_kleinman-pagecut-regression.md` — session 004 (list-context fix).

## Open questions / decisions needed
- [ ] **`_looks_incomplete` over-triggers on books with text-layer body refs in first 20 pages**.
  ВКР: heuristic 14 = верный ToC, но raw_entries 33 → ratio 2.36 > 1.5 → fallback запускает
  LLM+OCR ~170s впустую. Опции: окно ОТ маркера СОДЕРЖАНИЕ (риск — маркер может быть OCR-битый),
  или soft-cap ±10 страниц, или второе условие монотонности по pages + доля level≥2.
- [ ] **GROUNDING_MIN=0.8 / incompleteness ratio 1.5** — calibrated offline; confirm on live runs.
- [ ] **scrub_foreign_script threshold** — removes ALL CJK/Arabic. Edge case: books with intentional CJK.
- [ ] **CONTEXT vs реальность по Клейнману** — claim «0 page_cut» против live 36 page_cut.
  Обновить CONTEXT после расследования.

## Deferred (not forgotten)
- [ ] **0e6e53b LLM-clean speed** — 14 low-conf sections, parallel but slow. Acceptable.
- [ ] **toc_validation (OCR coverage) metric** — noisy when heuristic ToC is perfect. Informational.
- [ ] **Context length guard** — no early warning if LM Studio ctx < 8192. Manual workaround.
- [ ] **Do Good Design copyright spam** — low priority. Options in bug file.
