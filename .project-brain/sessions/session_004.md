# Session 004 — 2026-05-29
← [session_003.md](./session_003.md)

## Goal
Детальный аудит соответствия XML-вывода РЕАЛЬНОСТИ (PDF в test/) — найти баги и
несоответствия в КОНТЕНТЕ секций (пользователь видит глазами, что многого нет, напр. Release It!).
Затем чинить найденное: сначала ToC, потом механизм смещения контента.

## What actually happened

### 1. Построил методику аудита (2 слоя)
Пользователь верно заметил: сравнивать XML с `fitz.get_text()` — кругово (пайплайн под капотом
тот же fitz). Решение — два слоя:
- `scripts/audit_content.py` — per-section ratio = content_len / объём текста PDF на диапазоне
  страниц секции (со смещением ToC→PDF по якорям). Флаги EMPTY/TRUNCATED/BLOATED/LEAK/DUP.
- `scripts/fitz_blindness.py` + `scripts/render_pages.py` — карта «слепоты fitz» (мало текста +
  графика) → рендер в PNG → читаю глазами (vision ground-truth, независимо от fitz).
Вывод (инсайт `insight_2026-05-29_content-audit-method.md`): SCAN-страницы в этих книгах —
легитимные иллюстрации (фото McDonald's/Marlboro в Do Good; абстрактная живопись в Иглмен), НЕ
потерянный текст. Главный дефект — НЕ слепота к сканам, а **смещение контента** в текстовом слое.

### 2. Нашёл 3 класса дефектов (отчёт пользователю)
- Класс 1: **неполнота ToC** (теряются подразделы) — bug_2026-05-29_toc-subsections-dropped.
- Класс 2: **смещение контента** (текст уезжает в соседнюю/разделительную секцию) —
  bug_2026-05-28_part-divider-absorbs-chapter-body.
- Класс 3: дубли контента (parallelnoe, приложение A) — следствие класса 2.

### 3. Пользователь дал визуальный разбор ToC по всем книгам (ОЧЕНЬ ценно)
Release It! и Розенсон теряют подразделы (2.1, 2.2…); Массель/Кениг/Do Good/parallelnoe — ToC ок,
контент под вопросом; Клейнман/Виды — ок; Иглмен — ToC ок несмотря на части. Это развернуло
работу на ПЕРВОПРИЧИНУ (неполный ToC → нет точек разреза → смещение контента).

### 4. Чинил ToC (Класс 1)
Два разных механизма потери подразделов:
- **Mechanism A (Release It!, двойное оглавление)**: `_is_content_start` ложно ломает парсинг на
  page-less разделителе «Часть I», который уже виден из «Краткого содержания». → FIXED.
- **Mechanism B (Розенсон)**: подзаголовки ненумерованные, страница на след. строке, заголовки
  переносятся на 2–3 строки, повторы «Вопросы для проверки». Потоковый парсер не тянет —
  каждый патч чинил одну книгу ценой мусора в другой (Клейнман набирал тело книги). → DEFERRED,
  решено делать умный LLM-fallback.

### 5. Решение по Розенсону → умный LLM/OCR-fallback (одобренный план)
Пользователь: «делать умно — retry на формальные ошибки, проверка через эмбеддер на галлюцинации,
контроль CJK; качество важнее скорости; OCR пусть тоже помогает». Триггер неполноты —
АЛГОРИТМИЧЕСКИЙ (без моделей): считаем строки-пункты в сыром тексте ToC vs вывод эвристики;
ratio>1.5 = неполно (проверено: Розенсон 4.14, Клейнман 1.76; Release It! 0.99 и др. — не трогаем).
План: `C:\Users\rusla\.claude\plans\sparkling-brewing-pillow.md`.

## Decisions made
| Decision | Chosen because |
|----------|----------------|
| Аудит контента = 2 слоя (ratio + vision) | fitz-vs-fitz круговой; vision — единственная независимая правда |
| ToC чинить ДО смещения контента | подразделы дают точки разреза, уменьшают смещение |
| Mechanism A: не ломать парсинг на page-less «Часть/Part/Раздел» | разделитель повторяется в детальном ToC дв.оглавления |
| Length-guard regex 250 символов | катастрофический бэктрекинг item_pattern вешал Клейнман; 250 не режет легит. пункты |
| Розенсон → умный LLM/OCR-fallback, НЕ патч эвристики | потоковый парсер фундаментально не тянет этот макет; качество > хрупкость |
| Триггер fallback = алгоритмический сигнал неполноты | осознать неполноту без вызова моделей (ratio сырых пунктов / эвристики > 1.5) |

## What we tried that didn't work
- ❌ Патчи Mechanism B в эвристике (next-line lookahead, pending-accumulation, гейт misses==0):
  каждый трейдил книги. ungated → Розенсон 85 но Клейнман 75 с мусором тела; misses==0 →
  Клейнман чист 57 но Розенсон обрезан на Главе 6. DO NOT снова патчить эвристику под Розенсон —
  это путь к whack-a-mole. Правильный путь — LLM-fallback с валидацией.
- ❌ Доверять только coverage из audit_content: coverage высокий (.93–.99) даже при сильном
  смещении, т.к. текст не теряется, а переезжает. Смотреть per-section ratio, не coverage.

## Files changed / created
- `app/services/toc_parser.py` — Mechanism A (подавление break на разделителе) + length-guard 250. APPLIED.
- `app/services/toc_validate.py` — NEW: score_toc/is_valid/CJK/formal/grounding. СОЗДАН, НЕ интегрирован.
- `scripts/audit_content.py` — NEW: аудит XML↔PDF по per-section ratio.
- `scripts/fitz_blindness.py` — NEW: карта слепоты fitz по страницам.
- `scripts/render_pages.py` — NEW: рендер страниц PDF в PNG (.audit_pages/).
- `scripts/toc_regression_check.py` — NEW: счётчик эвристического ToC vs старый nav.

## Bugs found → 
- `bugs/bug_2026-05-28_part-divider-absorbs-chapter-body.md` (open) — смещение контента.
- `bugs/bug_2026-05-29_toc-subsections-dropped.md` (A fixed, B → LLM-fallback).
## Insights found →
- `insights/insight_2026-05-29_content-audit-method.md` — методика аудита, fitz-vs-fitz ограничение.

## End state
Branch `llm-toc-fallback`.
- **Mechanism A** зафиксирован: Release It! ToC 34→153 (все подразделы), ноль регрессий.
- **Умный LLM/OCR fallback РЕАЛИЗОВАН и LIVE-проверен** (qwen2.5-7b):
  - Розенсон: heuristic 20 → **llm 77** (71 подраздел), grounding 1.0, 0 CJK — выбран LLM.
  - Клейнман: heuristic 41 → **llm 57** (54 подраздела), grounding 1.0, 0 CJK — выбран LLM.
  - Полные книги (Виды UI 15, parallelnoe 248, Release It! 153) → остаются `heuristic`, БЕЗ вызова
    моделей (триггер не срабатывает). Ноль регрессий.
  - 260 юнит-тестов зелёные.
- Хардненинг по ходу live-проверки: salvage-парсер JSON (`_parse_toc_items` — первый прогон Розенсона
  падал на json.loads целиком из-за обрезки по токенам), `TOC_MAX_TOKENS=3000`, кламп уровня LLM
  до 1..3 (LLM выдавал level 4 → ошибочно заваливал идеальный ToC на формальной проверке).
- Файлы: toc_validate.py (NEW), toc_builder.py (fallback rework + helpers), llm_engine.py
  (extract_toc_json: extra_instruction + salvage + TOC_MAX_TOKENS), toc_parser.py (length-guard 250).
  Тесты: test_toc_validate.py (NEW), test_toc_builder.py (+_looks_incomplete), test_llm_engine.py
  (+_parse_toc_items). scripts/: toc_live_check.py, toc_debug.py (NEW).

## architecture.md updated
Diagram (toc_validate в ветке toc_builder), новый компонент toc_validate.py, обновлены toc_builder
(smart fallback + `_looks_incomplete`/`_select_best`) и llm_engine (salvage `_parse_toc_items`),
data flow шаг 5, cross-cutting («never ship unvalidated LLM ToC», «models run sequentially»),
Change History (5 записей сессии 004).

## Класс 2 — смещение контента (СДЕЛАНО, измерено)
Метод: `scripts/mapping_audit.py` (build_toc + map_sequence + нарезка, БЕЗ LLM-clean — лёгкое
измерение смещения). Сначала измерили (по решению пользователя «сначала измерить»).
- **Release It!**: фикс ToC сам разложил — подразделы 2.1–2.5 стали точками разреза. «Часть I»
  30611→4090, глава 2 распределена (ratio ~1.0). Mapping не трогали.
- **Do Good** (ToC только главы, спасать нечем): два механизма + два фикса в mapping_pipeline:
  - **Fix A** — `_restored_after_rescue_fail`: не восстанавливать дальний page-distance-реверт если
    есть страница → отдать page_cut. Чинит Главы 1/3/5/7 + copyright в «Об авторе».
  - **Fix B** — `_revert_position_clusters`: ≥3 подряд секций втиснуты в ≤4000 знаков при разбросе
    оценок ≥15000 → реверт в page_cut (список глав/задник). Чинит Главы 8–12.
  Итог Do Good: Глава 6 ×4.56→1.12, Глава 7 →1.22, Главы 8–12 0.78–1.13. Регрессий нет
  (parallelnoe 248 / Массель: 0 ложных cluster-revert; A ограничен page-distance-ревертами).
  264 теста. Файлы: mapping_pipeline.py (+CLUSTER_* константы), test_mapping_pipeline.py (+4),
  scripts/mapping_audit.py (NEW).
- architecture.md обновлён (mapping non-obvious A/B, cascade levels 8-9, Change History).

## ДАЛЕЕ
- Прогнать полный пайплайн на всех 11 книгах с правками сессии 004 (ToC + mapping) → обновить
  baseline test_v2 + corpus-аудит. Нужен LM Studio.
- Мелочи: «Об авторе» Do Good (кривая страница в ToC, ratio 0.16); parallelnoe приложение D
  (embedding_rescue в плотном std::-справочнике — пред-существующее, не от Класса 2).
- Опционально: ctx LM Studio → ~24K + TOC_MAX_TOKENS ~6000 (для ToC > ~85 пунктов).
