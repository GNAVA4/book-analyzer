# PROJECT CONTEXT — Book Analyzer
_Last updated: 2026-06-10 session 007_

## What this project is
PDF/DOCX/TXT book parser: extracts Table of Contents and section content into structured XML files.
Neural pipeline uses a multi-tier cascade with LLM, OCR, and embeddings as fallbacks — all running locally via LM Studio.

## Stack
Python 3.12 + FastAPI + WebSocket + PyMuPDF (fitz) + LM Studio (local API at 127.0.0.1:1234)
Models: `qwen2.5-7b-instruct` (LLM, ctx=8192), `glm-ocr` (vision OCR), `text-embedding-qwen3-embedding-0.6b` (embeddings)
Frontend: single-page `static/index.html` (vanilla JS + dark theme)
Server runs via venv: `c:\book analyzer\venv\Scripts\python.exe -m uvicorn app.main:app`

## Current state — session 007 corpus (tests_v7/, 19 books)

| Книга | Real/Total | toc | Note |
|---|---|---|---|
| 0e6e53b | 35/35 | ocr_llm | **OCR 400 fix: 4 pages recovered (+1757 chars)** |
| Release It! | 126/129 | heuristic | OK |
| Виды UI | 15/15 | heuristic | OK |
| Do Good | 18/18 | heuristic | OK |
| Иглмен | 22/22 | llm | OK |
| Массель | 20/23 | heuristic | OK |
| Розенсон | 74/77 | llm | +5 real vs v3 (list-context фикс с сессии 004) |
| Кениг | 19/20 | llm | residual Кениг «3.Свет»/«4.Текстура» |
| parallelnoe | 243/248 | heuristic | OK |
| Клейнман | 40/40 | heuristic | **РЕАЛЬНО 36 page_cut, avg_conf 0.368** — отличается от заявленного в CONTEXT сессии 004 (там «0 page_cut»). Нужна проверка/обновление |
| MIL-STD | 594/747 | heuristic | **REGRESSION: 8 секций exact→page_cut**, 5.1.2.5 поглотила +61k chars |
| **ВКР** | 12/14 | heuristic | **ToC dup FIXED**: 15→14; toc_source flipped ocr_heuristic→heuristic |
| **bookфизика** *(new)* | 474/475 | heuristic | excellent |
| **Машинное обучение** *(new)* | 227/228 | heuristic | excellent |
| **AI** *(new)* | 184/184 | heuristic | OK (73 page_cut but >5k chars each — linear pagination works) |
| **978-5-7996** *(new)* | 32/32 | llm | **Дефект А: LLM strips numbering, 24 page_cut** |
| **1332** *(new)* | 51/61 | heuristic | **Дефект Б: 10 titles = только «1. », «2. »…** |
| **12_100229** *(new)* | 301/328 | heuristic | **Дефект Б + дубли split-а в §-нотации** |
| **digital-design** *(new)* | 20/105 | ocr_llm | **ToC FIXED (s6): все 105 titles с префиксами**. Body mapping ещё страдает — OCR drift в теле требует OCR-aware fuzzy |

## Active work (session 007 — DONE)
- ✅ **Anchor-interpolation page_cut**: `_build_page_anchors` собирает (page, position)
  из доверенных match-стратегий, `_interpolate_position_from_page` линейно интерполирует
  между bracketing анкорами. Используется в финальном page_cut цикле. **MIL-STD 5.1.2.5:
  61 677 → 33 chars** (целевой кейс). Побочный эффект — page_cut redistribution на
  12_100229 (real -26), AI (-17) — вероятно более честная нарезка, но без manual
  inspection трудно сказать.
- ✅ **content_quality.py** + метрика `junk_sections` в reports — детектор leader-dots /
  bullet / toc_fragment / whitespace. Observation-only сейчас. MIL-STD 80, digital-design
  36, 12_100229 10. Готов к wiring в `_verify_and_correct_order` как 3rd check.
- ✅ **OCR-aware fuzzy tier** `_ocr_aware_fuzzy_locate` + `_ocr_fold` между fuzzy и
  embedding. Gated by `toc_source.startswith('ocr')`. На корпусе 4 хита (3 на 0e6e53b,
  1 на digital-design). Тяжёлый drift не лечит — нужно усиление.
- ✅ **retry 3→5 в `_llm_from_text_retry`** — small stability bump, не починил 978-5-7996.

## Active work (session 006 — DONE)
- ✅ **Defect A (LLM strips hierarchical numbering) — FIXED at ToC level.** Prompt update +
  `_reattach_numerical_prefixes` страховочная сетка. Сканирует raw text на номерные ToC-строки,
  строит map нормализованный-bare→prefix, приклеивает префикс к LLM-title без digit/§ начала.
  Wired into `_llm_from_text`, `_llm_from_text_retry`, `_ocr_then_llm`.
  digital-design NavigationTable: 0/105 → 105/105 prefixed. ВКР real +1.
- ✅ **Defect B — MISDIAGNOSED.** Изначальный симптом («1.», «5./» как titles) был mojibake
  в cp1251 console. Real titles полные. Разделено на:
  - 1332: empty L1 главы = valid shells (содержание в L2 children).
    `insight_2026-05-30_chapter-shell-empty-content-is-valid`.
  - 12_100229: heuristic split многострочного title в 3 items → **FIXED**.
- ✅ **Multi-line wrap merge — FIXED.** Mapping-side `merge_wrap_continuations` склеивает
  соседние items когда gap ≤ 5 chars, тот же level/page, trusted strategy, и nxt без своего
  numeric prefix. Own-prefix guard защищает MIL-STD от ложных склеек «5.11.1»+«5.11.1.1».
  12_100229: 10 склеек, 0 ложных в корпусе. 302 unit-tests.
- ✅ **`effective_real_sections` метрика** в `run_corpus.py`: считает секцию покрытой если
  её content >100 ИЛИ у её descendant'а content >100. Честная отчётность для иерархических
  книг (1332: real 51 → eff 61; parallelnoe: 243 → 246; Массель: 20 → 23).
- ✅ **Промпт `extract_toc_json` сужен:** rule 4 явно «короткий заголовок, не описание/
  первое предложение», rule 5 сохраняет требование на префикс с примером.

## Active work (session 005 — DONE)
- ✅ `_drop_fuzzy_pageless_dupes` в `toc_builder._dedup_and_order` — fuzzy ≥ 0.88 на title-norm,
  гард len<8. Unit-tested. Live-обходит OCR drift «ИЗУЧЕНЯЯ»/«ИЗУЧЕНЯЮ».
- ✅ `_extract_text_from_ocr_error` + `BadRequestError` ветка в `ocr_engine` — извлечение
  встроенного OCR-текста из тела 400, fallback на `page.get_text()`. Live-verified: 4 страницы
  0e6e53b восстановлены (+1757 chars).
- ✅ Корпусный прогон 19 книг → tests_v4. Корпусный диф v3↔v4: мои фиксы не дали регрессий
  (на 11 из 12 v3-книг total_sections идентичен; mil-std регрессия — от пред-сессионного коммита).
- ✅ 2 новых класса дефектов задокументированы (Дефекты А и Б — см. ниже).

## NEXT
- Дефект А (LLM strips hierarchical numbering) — поправить `extract_toc_json` prompt и/или
  пост-процессинг в `_normalize_llm_items` (re-attach prefix из raw text).
- Дефект Б (heuristic loses chapter name) — multi-line lookahead в `HeuristicParser`.
- MIL-STD 5.1.2.5 +61k bloat — расследовать (Class-2 residual?).
- Клейнман live 36 page_cut vs CONTEXT claim 0 — выяснить и обновить.
- `_looks_incomplete` over-triggers на ВКР (раздувает run-time на ~170s впустую).

## Decisions that affect ALL code
- **CONFIDENCE_THRESHOLD=0.85**: above → `fast_clean_chunk` (algo), below → LLM boundary+clean
- **scrub_foreign_script on all paths**: CJK scrub applied after EVERY section regardless of confidence path — never skip
- **LM Studio ctx=8192**: must be started with `-c 8192`, default 4096 causes "Context size exceeded"
- **All flags ON by default**: `deep_scan=True, use_ocr=True, llm_expand=True, validate_toc_ocr=True`
- **PAGE_DISTANCE_TOLERANCE_RATIO=0.30**: match > 30% of text from expected page position → suspect
- **LLM clean IS parallel**: `process_large_text` uses `asyncio.gather` — do not claim it's sequential
- **page_cut confidence=0.30**: approximate position from page number. Bypasses fix_boundary, uses fast_clean_chunk.
- **page_cut excluded from running_max**: approximate positions must not anchor ordering logic.
- **page_cut runs AFTER final verify**: must be LAST step in map_sequence. See insight.
- **ToC heuristic: don't break on page-less «Часть/Part/Раздел»** (Mechanism A): in dual-ToC books
  the divider recurs inside the detailed ToC; breaking there drops all later subsections.
- **ToC heuristic length-guard = 250**: lines >250 chars skip item_pattern regex (catastrophic
  backtracking hung Клейнман when parser read into body). Real ToC items are <250.
- **ToC incompleteness trigger is algorithmic (no models)**: raw ToC entry-lines / heuristic items
  > 1.5 ⇒ incomplete ⇒ run smart LLM/OCR fallback. Books below threshold are NOT touched.
  **CAVEAT (session 005):** Over-triggers when first 20 pages contain body refs to page nums
  (ВКР: ratio 2.36, heuristic gives correct 14 items, fallback wastes ~170s).
- **Smart ToC fallback validates against hallucination**: CJK + formal + embedding grounding≥0.8;
  never ship unvalidated LLM ToC. See ADR 003.
- **Content slicing is text-order based**: section content = full_text[end_idx : nearest start_idx in
  text]. Misplacement happens when matched positions violate ToC order → divider/neighbour absorbs body.
- **Mapping Class-2 guards**: (A) far page-distance reverts defer to page_cut when page known; (B)
  `_revert_position_clusters` reverts ≥3 crammed back-matter title matches to page_cut.
- **Match the PROSE occurrence, not the list one** (`_find_first_nonlist`/`_is_list_context` in
  pdf_utils): titles often appear first in an in-body bulleted summary / leader-dot ToC; matching there
  gives "•"/dots content AND cascades `current_pos` forward (later titles → page_cut). Prefer the
  occurrence followed by prose.
- **LLM ToC is non-deterministic** — can return far fewer items than it should; `_llm_from_text_retry`
  retries when items << raw ToC entry count.
- **Audit metric trap**: `coverage` and "real sections >100 chars" MASK misplacement — they count
  dots/"•"/ToC fragments as content and stay high when text is merely misplaced. Judge content QUALITY
  (does body match its title / is it junk), not length/coverage.
- **page_cut quality depends on linear pagination**: page_cut is only as good as
  `(page-1)/total_pages × text_len`. For books with uneven text/figure density (Клейнман) the estimate
  is far off. Trust text matches over page_cut when pagination is non-linear.
- **NEW (session 005): fuzzy-pageless dedup**: `_drop_fuzzy_pageless_dupes` removes page-less ToC items
  that fuzzy-match (SequenceMatcher ratio ≥ 0.88) a paged item. Guard: len < 8 → skip. Targets OCR drift
  variants («ИЗУЧЕНЯЯ»/«ИЗУЧЕНЯЮ» = 1 letter difference on 80-char title).
- **NEW (session 005): OCR 400 recovery**: glm-ocr `Failed to parse input at pos N` errors embed the
  page text in the body; extract via regex on `body['error']`/`str(e)`; fallback on `page.get_text()`.
  Never silently drop pages.
- **NEW (session 006): LLM ToC numerical prefix preservation**: prompt explicitly требует
  сохранять «1», «1.2», «1.2.3», «§ 1.4», «Глава 5» как часть title. Плюс post-process
  `_reattach_numerical_prefixes` приклеивает префикс из raw text если LLM всё же срезал.
  Применяется ко ВСЕМ путям LLM-извлечения ToC.
- **NEW (session 006): «empty L1 главы» в отчётах ≠ потеря контента**. Если у L1 секции
  есть L2 children с реальным контентом — это правильная иерархическая структура (1332).
  Метрика `real_content_sections` обманывает; нужен `effective_real_sections` который
  засчитывает coverage через детей.

## Known landmines ⚠️
- **GPU max 90%**: at 100% user's display disappears — never load all models simultaneously
- **cp1251 console**: `print()` with U+FFFD crashes on Windows — always use `_safe_print()`
- **LM Studio context**: must pre-load with `-c 8192`
- **_restored_after_rescue_fail**: can restore false positive exact-matches (Do Good Design)
- **Two python instances RECURRING ISSUE**: after `&`-backgrounding in bash combined with
  `run_in_background=true` tool param, TWO Python processes spawn (one venv, one system 3.11).
  Don't combine both backgrounding mechanisms — pick one. Verify with `Get-CimInstance
  Win32_Process -Filter "Name='python.exe'"` before testing a fix.
- **Scripts run with venv Python**: system Python 3.14 doesn't have project deps. Always use `venv/Scripts/python.exe`
- **Background bash task uses snapshot of script**: if `run_pipeline.py` is edited while a bash background task is already running it, the running process uses the old version (Python reads the file once at startup)
- **dedup key = title only**: acceptable tradeoff, see insight
- **toc_validation coverage=0.05 is noisy**: Клейнман case — treat as informational only
- **NEW (session 005): LLM extract_toc_json drops numerical prefixes**: «1.2.1 Абстракция» →
  «Абстракция». Makes titles ambiguous. Hits books that escalate to llm/ocr_llm source.
- **NEW (session 005): tests_v3 was generated MID-session-004**, before commits `c81fcf0` and
  `dddc2ff`. So v3↔v4 diffs include those commits' effects (e.g. MIL-STD 8× exact→page_cut, Розенсон
  +5 real). v3 is NOT a clean baseline for evaluating session 005 fixes in isolation.

## File map (key files)
- `app/api.py` — HTTP POST /upload + WebSocket /ws/analyze
- `app/services/pdf_parser_neural.py` — main coordinator, thin
- `app/services/toc_builder.py` — 6-level ToC cascade + smart fallback + **fuzzy-pageless dedup (s5)**
- `app/services/mapping_pipeline.py` — 5-level mapping + 2× verify + page_cut last
- `app/services/toc_parser.py` — HeuristicParser
- `app/services/toc_validator.py` — OCR validation of ToC
- `app/services/pdf_utils.py` — find_real_indices, fast_clean_chunk, readability check, list-context preference
- `app/services/llm_engine.py` — LLM client + scrub_foreign_script + ToC JSON salvage
- `app/services/ocr_engine.py` — glm-ocr client with `_safe_print` + **400 recovery (s5)**
- `app/services/embedding_engine.py` — embedding client
- `app/services/xml_builder.py` — XML output builder
- `app/services/toc_validate.py` — ToC candidate validation/scoring (CJK, formal, grounding)
- `scripts/run_corpus.py` — direct E2E runner (bypasses uvicorn landmine); OUT_DIR env
- `scripts/run_pipeline.py` — uvicorn-based E2E test runner
- `scripts/analyze_xml.py` — standalone XML quality checker
- `scripts/audit_content.py` — XML↔PDF per-section ratio audit
- `scripts/fitz_blindness.py` — per-page fitz-blindness map
- `scripts/render_pages.py` — render PDF pages → .audit_pages/*.png
- `scripts/toc_regression_check.py` — heuristic ToC count vs old nav (offline regression)
- `tests_v3/` — baseline 12 books (pre-list-context-fix end of session 004)
- `tests_v4/` — NEW baseline 19 books (post-session-005 fixes)
- `static/index.html` — frontend
- `tests/` — pytest suite 281+ tests
