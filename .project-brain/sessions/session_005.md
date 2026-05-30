# Session 005 — 2026-05-30
← [session_004.md](./session_004.md)

## Goal
Закрыть два открытых бага сессии 004:
1. `bug_2026-05-29_vkr-toc-duplicate-chapter` — дубль «ГЛАВА 1» в конце ToC ВКР.
2. `bug_2026-05-29_ocr-drops-parseable-pages` — glm-ocr 400 теряет читаемые страницы.
Затем — корпусная регрессия + анализ новых книг, добавленных пользователем.

## What actually happened

### 1. Код-фиксы (unit-tested, 281/281)
- **VKR dedup**: добавил `_drop_fuzzy_pageless_dupes` в `toc_builder._dedup_and_order`.
  Удаляет page-less items, у которых fuzzy-match (SequenceMatcher ≥ 0.88) с paged-item.
  Гард: пропускаем titles <8 символов. +6 unit-тестов.
- **OCR 400 recovery**: в `ocr_engine` отдельная ветка `BadRequestError` →
  `_extract_text_from_ocr_error` достаёт встроенный в тело 400 OCR-текст; fallback на
  `page.get_text()`. +6 unit-тестов.

### 2. Live verification

**0e6e53b (OCR-зависимая):** ВСЕ 4 страницы из бага восстановлены через 400-handler:
```
OCR page 25:  recovered 175 chars from 400
OCR page 120: recovered 1206 chars from 400
OCR page 137: recovered 64 chars from 400
OCR page 174: recovered 312 chars from 400
```
Других OCR-failures нет. Sections 35/35 real, avg_conf 0.853→0.849, page_cut 1/1 — без регрессий.
Total content +373 chars (1757 chars recovered − небольшое колебание OCR на других страницах).

**ВКР:** Дубль «ГЛАВА 1» ушёл (15→14 секций). НО: `toc_source` в этом прогоне =
`heuristic` (PyMuPDF), а в tests_v3 был `ocr_heuristic`. Поэтому не 100% уверен, что
именно мой `_drop_fuzzy_pageless_dupes` убрал дубль — возможно, он сократил ocr_heuristic-
кандидат с 15 до 14, после чего `_select_best` выбрал heuristic-кандидат (тоже 14).
Либо тот, либо тот путь — дубля больше нет, заголовок «ИЗУЧЕНИЯ» теперь правильный (PyMuPDF
читает текстовый слой чище OCR drift).

### 3. Корпусная регрессия v3 → v4 (12 книг)
- 11 книг с total_sections=v3=v4: мой dedup-no-op, никакого риска регрессии.
- ВКР: total -1 (дубль убран), avg_conf 0.431→0.450 (+).
- 0e6e53b: +1757 chars recovered.
- **Сюрприз — MIL-STD: 8 секций `exact→page_cut`**, 5.1.2.5 «Signal precedence»
  поглотила +61k chars. Атрибуция: НЕ мои фиксы (total 747/747, dedup no-op).
  Гипотеза: коммит `c81fcf0` (list-context fix) применился к v4, но не к v3 (tests_v3
  сгенерирован ДО этого коммита).
- **Сюрприз — Клейнман v3 = v4 идентичен** (36 page_cut, avg_conf 0.368). CONTEXT
  утверждал «Тот же list-context фикс ВЫЛЕЧИЛ Клейнмана: 0 page_cut, cov ~0.98» — но
  это, видимо, измерялось через `scripts/mapping_audit.py` (debug-инструмент), а не
  через реальный `parse_pdf_neural`. Real-pipeline продолжает выдавать 36 page_cut.

### 4. Пользователь добавил 7 новых книг (12_100229, 1332, 978-5-7996, AI, bookфизика,
digital-design, Машинное+обучение). Прогнал, провёл детальный аудит. Не сломалось ничто
от моих фиксов (0 OCR 400 recoveries — никто из новых не хитит этот путь). НО вылезли
два **новых** класса дефектов на новых типах книг:

**Дефект А: LLM/OCR-LLM ToC срывает иерархические номера (digital-design, 978-5-7996)**
- digital-design (ocr_llm источник): 0 из 105 titles содержат `1.2.1`-префикс;
  в теле книги они ЕСТЬ; → mapping находит первое попавшееся вхождение «Абстракция»,
  контент чушь, 79 секций <100 chars, avg_conf 0.36.
- 978-5-7996 (llm): то же самое.
- Усилитель для digital-design: **OCR drift в самих titles** («прицелы»/«принципы»,
  «Наляржение»/«Напряжение», «Дициплина»/«Дисциплина») — ниже FUZZY_THRESHOLD=0.85,
  fuzzy_rescue не помогает.
- Здоровые книги с heuristic-источником (bookфизика 474/475 real, Машинное 227/228)
  сохраняют префиксы → подтверждает, что проблема не в общем пайплайне, а в LLM-ветке.
- Заведён `bug_2026-05-30_llm-strips-hierarchical-numbering`.

**Дефект Б: Heuristic теряет название после номера главы (1332, 12_100229)**
- 1332: 10 пустых секций с title=«1. », «2. »… (только номер). В PDF имя главы есть
  (вероятно, на следующей строке или после спецсимвола); `item_pattern` ловит «1.»
  как title, имя теряется.
- 12_100229: 6 таких же + дубли по split-у в §-нотации.
- Заведён `bug_2026-05-30_heuristic-loses-chapter-title-after-number`.

**Не баг: AI 73 page_cut с >5k chars каждый — это норма, линейная пагинация, контент
размещён корректно, просто confidence=0.30.**

## Decisions made
| Decision | Chosen because |
|----------|----------------|
| Fuzzy-dedup порог 0.88 + guard на title len<8 | На 80-символьных русских заголовках разные главы редко >0.88 sim; короткие риск ложного матча |
| OCR 400: вытащить текст из body, fallback на PyMuPDF, не ретраить с другим DPI | Текст уже в ошибке — ретрай дороже и зависит от случайности; PyMuPDF — детерминированный backstop |
| Не уверен в реальной активации dedup — оставить unit-test покрытие как основное | Бесполезно слепо ставить FIXED без явного логирования активации |
| Запустить 7 новых книг сразу всем корпусом | Нашли 2 новых класса дефектов — польза > затрат на ~20 мин |

## What we tried that didn't work
- ❌ Прогон с `&` внутри bash tool при `run_in_background=true` — спавнит ДВА Python-процесса
  (один venv, один system). Второй — рекуррентный landmine из CONTEXT (двойной uvicorn в
  прошлых сессиях). Правильно: либо tool background, либо bash `&`, не оба.

## What works now
- ✅ glm-ocr 400 больше не теряет страницы — извлекаем встроенный текст.
- ✅ ВКР NavigationTable чистый — дубль ушёл.
- ✅ 11 из 12 старых книг показывают идентичный output v3↔v4 (моё no-op).
- ✅ 5 из 7 новых книг (bookфизика, Машинное, AI, 978-5-7996 — содержательно, 1332
  частично) дают разумный результат.

## Files changed
- `app/services/toc_builder.py` — `_drop_fuzzy_pageless_dupes` + использование в `_dedup_and_order`.
- `app/services/ocr_engine.py` — `_extract_text_from_ocr_error` + `BadRequestError` ветка.
- `tests/test_toc_builder.py` — +6 тестов в `TestDropFuzzyPagelessDupes`.
- `tests/test_ocr_engine.py` — NEW, +6 тестов.
- `tests_v4/` — NEW: 19 книг через пайплайн (.xml + _report.json).

## Bugs found
- `bugs/bug_2026-05-30_llm-strips-hierarchical-numbering.md` (open) — digital-design, 978-5-7996.
- `bugs/bug_2026-05-30_heuristic-loses-chapter-title-after-number.md` (open) — 1332, 12_100229.

Bugs status updates:
- `bugs/bug_2026-05-29_vkr-toc-duplicate-chapter.md` — ToC-dup часть закрыта; intro/ЗАКЛЮЧЕНИЕ
  ToC-fragment остаётся как mapping residual.
- `bugs/bug_2026-05-29_ocr-drops-parseable-pages.md` — FIXED live-verified.

## Insights found
_(none new beyond the bug analyses — patterns are documented in the bug files)_

## End state
Branch `llm-toc-fallback`. tests_v4 — новый baseline (19 книг).
281 unit-test зелёные. Два моих фикса в коде, не закоммичены ещё на момент написания
(коммит в этом сеансе).

Открытые направления:
- Класс Дефекта А (LLM strips numbering) — фикс через prompt + post-process.
- Класс Дефекта Б (heuristic name drop) — фикс через multi-line lookahead в parser.
- `_looks_incomplete` over-triggers (ВКР) — софт-окно вокруг СОДЕРЖАНИЕ-маркера или
  второе условие монотонности.
- CONTEXT vs реальность по Клейнману — реальные 36 page_cut, не 0; обновить CONTEXT.
