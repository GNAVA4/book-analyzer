# PROJECT CONTEXT — Book Analyzer
_Updated: 2026-10-17 session 015_

## What this project is
Этап 1 (готов, приемлемое качество): парсер книг PDF/DOCX/TXT → XML-дерево (оглавление + содержимое
разделов), каскад heuristic → LLM → OCR → эмбеддинги, всё локально через LM Studio.
Этап 2 (идёт, ветка `semantic-analysis`): семантический анализ текста — новизна относительно
корпуса и эталона, повторы, «вода», стилистические сигналы. Первый объект — статьи о миварах.
План: `plans/plan_001_semantic_novelty_analysis.md`, решения: `decisions/adr_004`.

## Stack
Python 3.11 (venv) + FastAPI + WebSocket + PyMuPDF; LM Studio 127.0.0.1:1234:
qwen2.5-7b-instruct (ctx 8192), glm-ocr, text-embedding-qwen3-embedding-0.6b.
Фронт: `static/index.html`. Сервер: `venv\Scripts\python.exe -m uvicorn app.main:app`.

## Current state
### Этап 1 — книги (ветка `llm-toc-fallback`, последнее изменение кода — session 013)
- Корпус 19 книг (tests_v14 — последний полный прогон): real 2578, effective 2630.
- Сделано и закоммичено: page_cut = чистая линейная оценка + разнос секций одной страницы (s11),
  выбор источника текста сравнением кандидатов `_pick_body_source` (s12), снятие постраничных
  водяных знаков из содержимого (s13).
- Открытые хвосты этапа 1 (12_100229 группы A–D, digital-design 46 page_cut, 978 недетерминизм,
  `_looks_incomplete` на ВКР) — в OPEN.md, приоритет ниже этапа 2.

### Этап 2 — статьи (ветка `semantic-analysis`, sessions 014–015, НЕ закоммичено)
- Корпус: 5 миварных статей (articles/) + 20 статей КиберЛенинки по CS/математике
  (articles_web/, префикс темы в имени; 3 — сканы). См. DATA.md.
- `app/services/article_parser.py` — парсер одиночной статьи без оглавления, алгоритмический
  (вёрстка PyMuPDF). Два режима шапки: по «Ключевым словам» и по НАЗВАНИЮ рядом с авторами
  (старые журналы). Отрезает хвост предыдущей и начало следующей статьи журнала, чинит cp1251 и
  разрядку, сканы → needs_ocr. Название верно 22/22 (с текстовым слоем). Выход — XML как у книг.
- `app/analysis/lexical.py` — почти дословные повторы (шинглы), самоцитирование.
- `app/analysis/semantic.py` — эмбеддинги (qwen3-embedding-0.6b, кэш .cache/), coverage /
  novelty leave-one-out / смысловые повторы внутри статьи; формулы исключаются.
  Темы различает (0.49 vs 0.36, 19/22). Главная находка: миварные статьи 2011/2013 пересказывают
  одни мысли (смысл 0.76–0.84 при дословном ~0) — [[insight_2026-10-17_paraphrased-self-repetition]].
- LM Studio работает (сервер запущен user'ом); доступна qwen3.6-35b-a3b (сильнее qwen2.5-7b).

## Decisions that affect ALL code
Этап 2 [ADR 004]: новизна = ДВА числа (покрытие эталона + «сверх эталона») [user]; единица —
атомарные утверждения; только русский [user]; только локальные модели [user]; детекция ИИ — не цель,
стилистические сигналы — нужны [user]; анализ — отдельный слой после парсинга [inferred];
статьи грузятся ПООДИНОЧКЕ, без архитектуры под сборник [user].

Этап 1 (действуют):
- CONFIDENCE_THRESHOLD=0.85 (ADR 001); все флаги ON (ADR 002); LLM-оглавление только после
  валидации (ADR 003); scrub_foreign_script на всех путях; LM Studio ctx 8192; модели по одной.
- page_cut — чистая линейная оценка (не возвращать интерполяцию по якорям — дважды откатывали).
- Предпочитать вхождение заголовка в прозе, а не в списке (`_find_first_nonlist`) — не откатывать.
- Водяные знаки снимаются из СОДЕРЖИМОГО секций, не из full_text (иначе едут позиции page_cut).
- Метрика `real>100` — ловушка; судить по содержимому, атрибуту `chars`, набору стратегий.

## Known landmines ⚠️
- **Правки кода через Bash: `\\b` превращается в backspace** → только Edit/Write
  ([[insight_2026-10-17_bash-tool-unescapes-backslash-b]]).
- **pytest целиком: `NO_PROXY='*'`** — системный прокси Windows (socks4) ломает импорт openai.
  HTTP к LM Studio из своего кода — httpx с `trust_env=False`.
- `requirements.txt` в кодировке UTF-16 — дописывать с сохранением кодировки.
- Новизна зависит от корпуса: статья без близких соседей выглядит «новой» — нужна пометка
  «нет опоры» (OPEN).
- GPU ≤ 90% (иначе пропадает дисплей) — модели по одной; cp1251-консоль → `_safe_print`.
- Не совмещать bash `&` и `run_in_background` — два процесса Python.
- Русский текст читать с UTF-8 stdout (моджибейк = ложные диагнозы).
- Мелкий кегль в статьях не выкидывать до разметки зон (аннотация бывает 7–8pt).

## Key file map
- `app/services/article_parser.py` — парсер статьи (этап 2)
- `app/analysis/lexical.py` — лексический слой анализа (этап 2)
- `app/analysis/semantic.py` — смысловой слой (эмбеддинги, coverage/novelty/повторы)
- `scripts/parse_articles.py [IN] [OUT]` — PDF статей → XML+JSON+сводка
- `scripts/analyze_articles.py [DIR]` — дословные повторы/самоцитирование
- `scripts/semantic_articles.py DIR... [--out f.json]` — смысловой отчёт (нужен LM Studio)
- `app/services/pdf_parser_neural.py` — координатор книжного пайплайна
- `app/services/toc_builder.py`, `mapping_pipeline.py`, `pdf_utils.py`, `xml_builder.py` — этап 1
- `scripts/run_corpus.py` — прогон корпуса книг (OUT_DIR)
- `tests/` — 366 тестов
