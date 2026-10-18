# INSIGHT: полный pytest дёргает LM Studio — во время LLM-прогона его не запускать
_Filed: 2026-10-18 session 023_
_Category: tooling_

## The finding
Тесты этапа 1 (сопоставление разделов: «Найди в тексте ниже, где начинается раздел…») шлют
запросы к http://127.0.0.1:1234 с моделью qwen2.5-7b-instruct. LM Studio JIT-загружает её
(TTL 60 мин) и эмбеддер поверх уже загруженной qwen3.6 → VRAM 10.9 ГБ (90%), qwen3.6 ответила
500, фоновая проверка «воды» упала после 4 абзацев.
Это же объясняет «qwen2.5-7b загружена, TTL 55m» в начале сессий — следы прогонов тестов.

## Rule
Во время фоновых LLM-задач запускать только тесты без LM Studio:
pytest tests/test_corpus.py tests/test_water_labels.py tests/test_article_parser.py
tests/test_article_docx.py tests/test_semantic.py tests/test_claims.py
Water-job теперь повторяет запрос до 3 раз с паузой 15 с (app/analysis/water.py).
