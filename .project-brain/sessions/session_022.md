# Session 022 — 2026-10-18 — Qwen3.8-27B и сравнение эмбеддеров
← [session_021.md](./session_021.md)

## Session Intent
User: настроить qwen3.8 в LM Studio, сравнить по ресурсам и силе с qwen3.6; проверить, есть ли
эмбеддер лучше текущего.

## Что сделано
- Qwen3.8-27B: плотная 27B, Q4_K_M 15.66 ГБ (+mmproj 0.87) против 3.6-35B-A3B MoE (3B активных),
  Q4_K_M 19.71 ГБ. Загрузка падает — [[insight_2026-10-18_qwen38-needs-newer-runtime]].
  Подготовлен `scripts/llm_compare.py` (run MODEL / report A B): синтетика (вода/пересказ),
  7 пар (мысль/тема/повтор), 6 абзацев корпуса, без кэша, время и токены — для прогона после
  обновления движка.
- Эмбеддеры: `scripts/embed_compare.py`; скачаны qwen3-embedding-4b (2.5 ГБ) и bge-m3 (0.63 ГБ)
  через `lms get`. Результаты — [[insight_2026-10-18_embedder-comparison-no-gain]].

## Evidence
- server-log 2026-10-18: `missing tensor 'blk.64.ssm_conv1d.weight'`, block_count 65, nextn 1.
- embed_compare: таблица в insight; .cache/report/embed_compare.json.
- VRAM: база 1.7 ГБ; 0.6b ctx4096 → 5.4 (≈3.6), ctx2048 → 3.9 (≈2.2); 4B сверху ≈5.6 → 11.4 из 12.2
  (93%, правило ≤90% нарушено на ~1 мин, сразу выгружено).
- Внешние сравнения 3.8 vs 3.6: Artificial Analysis индекс 34 vs 18, скорость 41 vs 143 ток/с.

## Не сделано / на решение user
- Обновить LM Studio / движок через интерфейс → затем `llm_compare.py run qwen/qwen3.8-27b`.
- Эмбеддер оставить 0.6b; ctx 2048 экономит 1.4 ГБ VRAM (обрежет 2 длинные аннотации) — спросить.

## End state
Эмбеддер 0.6b загружен (ctx 4096), остальные выгружены. Закоммичено.
