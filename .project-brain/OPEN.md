# OPEN ITEMS — Book Analyzer
_Last updated: 2026-06-10 session 007_

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

## Done this session (was in progress)
- [x] **Дефект А: LLM strips hierarchical numbering** — FIXED at ToC level (session 006).
  Prompt update + `_reattach_numerical_prefixes` safety net. digital-design ToC 0/105 →
  105/105 prefixed; ВКР real +1; 15 books unchanged. 290 unit tests. Code shipped.
  Body-mapping for OCR-heavy books still poor (digital-design real stays 20/105) — это
  отдельная задача OCR-aware fuzzy mapping, не часть Дефекта А.
- [x] **Дефект Б** — MISDIAGNOSED. Original symptom ("1.", "5./" as titles) was mojibake
  in cp1251 console. Real titles are full. Splits into two follow-ups:
  - 1332: empty L1 chapters are valid shells with content-bearing children
    ([[insight_2026-05-30_chapter-shell-empty-content-is-valid]]).
  - 12_100229: heuristic splits multi-line ToC titles
    ([[bug_2026-05-30_heuristic-splits-multi-line-toc-title]]).

## TODO (новые приоритеты после сессии 006)
- [x] **OCR-aware body mapping** — PARTIAL FIX 2026-06-10 (session 007). Tier
  `_ocr_aware_fuzzy_locate` добавлен между fuzzy и embedding rescue. Gated by
  `toc_source.startswith('ocr')`. Live: digital-design 1 хит, 0e6e53b 3 хита.
  Жесткий OCR drift («прицелы»/«принципы», 3-char diff в 7-char слове) всё ещё ниже
  порога 0.72 даже после folding — нужна следующая итерация (semantic verification
  или edit-distance с пониженным порогом + защита от false-positive).
- [x] **Heuristic splits multi-line ToC title** — FIXED 2026-05-30 (session 006) via
  mapping-side `merge_wrap_continuations` with own-prefix guard. 12_100229: 10 sections
  recovered. MIL-STD untouched after guard.
- [x] **Metric refinement** — DONE 2026-05-30 (session 006). `effective_real_sections` в
  `run_corpus.py`. 1332 теперь 61/61, parallelnoe 246/248, 12_100229 309/318 (отражает
  реальное покрытие).
- [x] **MIL-STD 5.1.2.5 absorbed +61k chars** — FIXED 2026-06-10 (session 007) через
  anchor-interpolation в page_cut. 5.1.2.5: 61 677 → 33 chars. Соседи перебалансировались.
- [ ] **Клейнман: live 36 page_cut vs CONTEXT claim 0** — `scripts/mapping_audit.py` показывает
  одно, реальный пайплайн — другое. Разобраться где правда и обновить.
- [x] **978-5-7996 длинные descriptive title** — Промпт сужен (session 006): rule 4
  явно говорит «short heading, don't add descriptions». На прогоне tests_v6 LLM
  переключилась на ocr_llm source (smart-fallback escalation), real 32→22 — это
  LLM-nondeterminism on new prompt, не регрессия маппинга.
- [x] **LLM nondeterminism — retry bumped 3→5** (session 007). Тривиальный фикс, но
  не помог 978-5-7996 (sec 33→13→29 across runs). Нужен более глубокий подход (semantic
  validation of LLM output, или caching prior best result).
- [ ] **978-5-7996 LLM качание не починилось** — даже после retry 3→5 секции скачут
  29→13 в tests_v7. Возможно проблема в том, что наша `is_valid` валидация на этой книге
  ловит разные «валидные» ответы каждый раз и они качественно разные. Может стоит
  вписать stable-cache по hash от raw_text → выбранный ToC.

## TODO (продолжающиеся из сессии 004)
- [ ] **MIL-STD 154 not-found / 92 EMPTY** — coverage .944 → text present but mis-attributed; likely class 2.
- [ ] **parallelnoe appendix D** — dense std:: reference entries get mis-located by embedding_rescue.
- [ ] **report.json content_len/preview are all 0** (test_v2) — pre-existing, source of truth = XML.
- [ ] **Fix PIPELINE_DIFF.md note** — says "LLM clean is sequential", actually asyncio.gather.

## Open bugs
- [x] `bug_2026-05-30_llm-strips-hierarchical-numbering.md` — FIXED at ToC level session 006.
  Body-mapping residual for OCR-heavy books moved into separate OCR-aware-fuzzy TODO.
- [x] `bug_2026-05-30_heuristic-loses-chapter-title-after-number.md` — CLOSED misdiagnosed.
- [ ] `bug_2026-05-30_heuristic-splits-multi-line-toc-title.md` (NEW session 006) — 12_100229.
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

## Closed session 006
- [x] `bug_2026-05-30_llm-strips-hierarchical-numbering.md` — FIXED 2026-05-30 (prompt +
  `_reattach_numerical_prefixes`). 290 unit tests. digital-design ToC fully prefixed.
- [x] `bug_2026-05-30_heuristic-loses-chapter-title-after-number.md` — MISDIAGNOSED;
  replaced by `insight_2026-05-30_chapter-shell-empty-content-is-valid` (1332) and
  `bug_2026-05-30_heuristic-splits-multi-line-toc-title` (12_100229).
- [x] `bug_2026-05-30_heuristic-splits-multi-line-toc-title.md` — FIXED 2026-05-30
  (mapping-side `merge_wrap_continuations` + own-prefix guard). 12_100229: 10 sections
  recovered. MIL-STD: zero false positives after guard. +10 unit tests, total 302.

## Closed session 007
- [x] **MIL-STD 5.1.2.5 bloat** — FIXED via anchor-interpolation page_cut (61 677 → 33
  chars). Side effect: 12_100229 real -26, AI real -17 from page_cut redistribution.
- [x] **content-quality detector** — `content_quality.py` + `junk_sections` метрика в
  reports. Observation-only сейчас, готов для wiring в mapping pipeline как 3rd verify.
- [x] **OCR-aware fuzzy tier** — добавлен, gated by toc_source. Слабый эффект (1-3 хита
  на корпус) из-за того что digital-design OCR drift в body слишком жёсткий для
  threshold 0.72. Tier живёт, ждёт усиления.
- [x] **retry 3→5 в `_llm_from_text_retry`** — committed, no major effect on stability.

## Open TODOs for session 008+
- [ ] **Wire content-quality detector в `_verify_and_correct_order`** as 3rd check:
  если контент junk → revert в not_found → rescue заново. Может помочь Кенигу, MIL-STD,
  digital-design.
- [ ] **Ordering guard for anchor-interpolation page_cut** — может уменьшить
  redistribution side-effect на 12_100229 / AI. Правило: page_cut позиция должна быть
  >= позиции previous mapped section.
- [ ] **Stronger OCR-aware mapping** — для тяжёлого drift'а как «прицелы»/«принципы».
  Подходы: (а) semantic verification — после fuzzy hit проверить embedding similarity
  candidate region vs ToC title; (б) sentence-level matching вместо title-level;
  (в) fuzzy с порогом 0.55-0.65 + защита от false-positive через page-hint window.
- [ ] **978-5-7996 stable selection** — LLM качание не лечится retry. Возможно нужен
  cache best-known-good по hash сырого текста.

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
