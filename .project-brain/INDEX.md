# MEMORY INDEX — Book Analyzer
_Updated: 2026-10-18 session 024_

## By topic
| Topic | Files |
|-------|-------|
| этап 2: постановка, формула новизны | plans/plan_001, adr_004 |
| парсер статей | session_014, plan_001 (фаза 1a) |
| лексический анализ (повторы, самоцитирование) | session_014, plan_001 (фаза 1b-лекс) |
| смысловой анализ (эмбеддинги, новизна, самоповтор) | session_015, insight_2026-10-17_paraphrased-self-repetition, plan_001 (фаза 1b) |
| сервис: корпуса, тема, страница по абзацам | session_020, session_021 (дизайн, DOCX), adr_005, insight_2026-10-18_area-level-corpus-too-coarse-for-novelty |
| методика и формулы | docs/semantic_methods.md, session_017 |
| согласованность метрик, новизна | session_019, insight_2026-10-17_novelty-is-isolation-not-content |
| уровень утверждений (LLM), «вода» | session_017, insight_2026-10-17_qwen36-thinking-off-via-completions, app/analysis/claims.py |
| данные: статьи, КиберЛенинка, сканы | DATA.md, session_015 |
| модели: сравнение LLM и эмбеддеров | session_022, insight_2026-10-18_embedder-comparison-no-gain, insight_2026-10-18_qwen38-needs-newer-runtime |
| вода в сервисе, разметка | session_023, session_024 (результаты разметки), insight_2026-10-18_pytest-triggers-lmstudio-jit |
| инструменты / окружение | insight_2026-10-17_bash-tool-unescapes-backslash-b, insight_2026-05-28_ocr-cp1251-crash |
| оглавление книг (каскад, fallback) | adr_003, session_004–006, bug_2026-05-29_toc-subsections-dropped, bug_2026-05-30_llm-strips-hierarchical-numbering, bug_2026-05-30_heuristic-splits-multi-line-toc-title |
| сопоставление разделов, page_cut | session_001–002, 007–011, insight_*page-cut*, insight_2026-06-27_*, bug_2026-06-10_anchor-interpolation-extrapolation-into-index, bug_2026-05-28_part-divider-absorbs-chapter-body |
| источник текста тела | session_012, insight_2026-06-28_body-source-was-toc-ocr-not-body |
| водяные знаки / колонтитулы | session_013, insight_2026-06-28_watermark-vs-content-frequency |
| методика аудита, ловушки метрик | insight_2026-05-29_content-audit-method, insight_2026-05-29_corpus-content-audit-v3, insight_2026-06-10_content-loss-is-real-not-redistribution |
| OCR | bug_2026-05-29_ocr-drops-parseable-pages, insight_2026-05-28_cjk-in-fast-clean-path |

## Sessions
| # | Date | Topic |
|---|------|-------|
| 001 | 2026-05-28 | P0-фиксы: OCR cp1251, CJK, sticky lines, dedup, флаги ON |
| 002 | 2026-05-28 | page_cut после финальной проверки порядка |
| 003 | 2026-05-28 | прогон 11 книг → test_v2 |
| 004 | 2026-05-29 | аудит содержимого; умный LLM-fallback оглавления; Class-2; list-context |
| 005 | 2026-05-30 | fuzzy-дедуп ВКР; OCR 400 recovery; 19 книг → tests_v4 |
| 006 | 2026-05-30 | префиксы нумерации в LLM-оглавлении; склейка переносов заголовков; effective_real |
| 007 | 2026-06-10 | anchor-interpolation page_cut (позже откачено); content_quality; OCR-aware fuzzy |
| 008 | 2026-06-10 | разбор регрессии anchor-interpolation |
| 009 | 2026-06-27 | атрибут chars; откат к линейному page_cut |
| 010 | 2026-06-27 | локальный backward-anchor (откачен); MIL-STD 5.1.2.5 принят |
| 011 | 2026-06-27 | разнос page_cut одной страницы (дубли 50→4) |
| 012 | 2026-06-28 | `_pick_body_source` — системный фикс источника текста |
| 013 | 2026-06-28 | водяные знаки из содержимого секций |
| 014 | 2026-10-17 | этап 2: парсер статей + лексический слой (ветка semantic-analysis) |
| 015 | 2026-10-17 | 20 статей КиберЛенинки; парсер для старых журналов; смысловой слой |
| 016 | 2026-10-17 | коммит; +20 статей КИМ; язык статьи; синтетическая проверка; отчёт-дашборд |
| 017 | 2026-10-17 | методика с формулами (docs/semantic_methods.md); уровень утверждений на qwen3.6-35b-a3b |
| 018 | 2026-10-17 | утверждения по половине корпуса (18 статей); «вода» по статьям в отчёте |
| 019 | 2026-10-17 | проверка метрик на противоречия: новизна ≈ одиночество, не связана с водой |
| 020 | 2026-10-18 | сервис: именованные корпуса (SQLite), тема, анализ по абзацам, /article.html; 145 статей КиберЛенинки; парсер — колонки |
| 021 | 2026-10-18 | редизайн страницы сервиса; DOCX; ВКР/отчёт о НИР в корпусе; оглавления и списки в парсере |
| 022 | 2026-10-18 | Qwen3.8 (не грузится: движок), сравнение эмбеддеров (0.6b остаётся) |
| 023 | 2026-10-18 | вода в сервисе; корпус ИИ/информатика/математика (9 журналов); страница разметки |
| 024 | 2026-10-18 | разметка воды user; колонки на 1-й странице; не-проза; reparse корпусов |

## Plans
| # | Title | Status |
|---|-------|--------|
| 001 | Семантический анализ: новизна, повторы, «вода», стилистика | 1a ✅, 1b-лекс ✅, 1b-эмб ✅ (v1), дальше синтетика |

## Decisions (ADRs)
| # | Title | Status |
|---|-------|--------|
| 001 | CONFIDENCE_THRESHOLD = 0.85 | active |
| 002 | Все флаги пайплайна ON по умолчанию | active |
| 003 | Умный LLM/OCR-fallback оглавления с валидацией | active |
| 004 | Этап 2 — семантический анализ отдельным слоем | active |
| 005 | Именованные корпуса, тема документа, сравнение внутри темы | active |

## Open bugs index
Открытых багов с файлами нет; остаточные проблемы этапа 1 — в OPEN.md.
Закрытые: release-it-regression, do-good-copyright, part-divider, kleinman, ocr-drops-pages,
subsection-into-toc (residual), toc-subsections-dropped, vkr-dup (residual), heuristic-loses-title
(misdiagnosed), heuristic-splits-title, llm-strips-numbering, anchor-interpolation (reverted).
