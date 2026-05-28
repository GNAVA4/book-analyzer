# Session 003 — 2026-05-28
← [session_002.md](./session_002.md)

## Goal
Прогнать все 11 тестовых книг, сохранить XML в test_v2/, проанализировать что нашлось.

## What actually happened
Запустили все 11 книг. Обнаружили что run_pipeline.py сохраняет только XML без статистики —
добавили OUT_DIR env var и сохранение _report.json рядом с каждым XML.
Снова столкнулись с двумя uvicorn на одном порту (рекуррентная проблема).
Скрипт был изменён уже после запуска фонового процесса → JSON-отчёты не сохранились в первом
прогоне → сгенерировали их постфактум из XML + статистики консольного лога.
Написали scripts/analyze_xml.py для автономного анализа XML-файлов.
Выяснили: Claude может читать PDF визуально (fitz рендер страницы → PNG → Read), что полезно для
верификации контента при анализе, но дорого для промышленного использования.

## Decisions made
| Decision | Chosen because |
|----------|----------------|
| OUT_DIR env var в run_pipeline.py | Гибкость папки вывода без правки кода |
| _report.json рядом с XML | Статистика и preview контента без открытия XML |
| Claude vision только для spot-check | Дорого для полного прогона, точно для разовых проверок |

## What we tried that didn't work
- ❌ Изменение run_pipeline.py во время работы фонового bash-процесса не влияет на запущенный процесс —
  Python читает файл один раз при старте. Нужно менять ДО запуска или перезапускать.

## What works now (that didn't before)
- test_v2/ — 11 книг × (xml + _report.json) — полный baseline
- run_pipeline.py: OUT_DIR env var + автосохранение _report.json
- scripts/analyze_xml.py — автономный анализатор качества XML

## Files changed
- `scripts/run_pipeline.py` — OUT_DIR env var, сохранение _report.json
- `scripts/analyze_xml.py` — NEW: анализатор качества XML (CJK, ToC-like, short sections)
- `test_v2/` — NEW: 11 × xml + 11 × _report.json

## Full run results
| Книга | Real/Total | avg_conf | Особенности |
|-------|-----------|----------|-------------|
| 0e6e53b | 35/35 ✅ | 0.853 | OCR книга, 8 embedding rescues |
| Виды UI | 15/15 ✅ | 1.000 | Идеально, algo only |
| Клейнман | 40/40 ✅ | 1.000 | Идеально, algo only |
| Иглмен | 22/22 ✅ | 0.694 | 6 page_cut, 13 low_conf |
| Кениг | 19/20 | 0.935 | 1 page_cut (Содержание) |
| Do Good Design | 16/18 | 0.726 | 4 page_cut, copyright spam в "Об авторе" |
| Release It! | 31/34 | 0.716 | 8 page_cut, 4 embedding rescues |
| Розенсон | 16/20 | 0.833 | 4 not-found, 3 embedding rescues |
| Массель | 20/23 | 0.996 | 3 not-found, 1 page_hint rescue |
| parallelnoe | 244/248 | 0.751 | 21 page_cut (приложения/справочник), 89 low_conf |
| MIL-STD (EN) | 593/747 | 0.986 | 154 not-found — причина неясна |

## Bugs found
_(none new)_

## Insights found
- `insights/insight_2026-05-28_page-cut-must-run-after-final-verify.md` — (уже был из сессии 002)
- Claude vision via fitz render — точный OCR декоративной типографики, но дорого для прод.

## End state
Branch `llm-toc-fallback`.
test_v2/ baseline готов. Content quality audit — следующая задача.
MIL-STD 154 missing — открытый вопрос, требует расследования.
