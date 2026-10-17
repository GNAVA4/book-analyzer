# INSIGHT: Qwen3.8-27B не грузится в LM Studio с движком llama.cpp 2.14.0
_Filed: 2026-10-18 session 022_
_Category: tooling_

## The finding
`lms load qwen/qwen3.8-27b` → «Failed to load model»; в server-logs: `missing tensor
'blk.64.ssm_conv1d.weight'`. В GGUF block_count = 65 и `nextn_predict_layers = 1`: 65-й блок —
голова MTP (предсказание нескольких токенов), которую движок 2.14.0 считает обычным слоем.
Известная проблема (llama.cpp discussions/issues): нужен новый движок / бета LM Studio, либо
отдельный файл MTP. `lms runtime update --all` (stable и beta) — «up-to-date», `lms runtime get -l`
ничего не находит → обновлять через интерфейс LM Studio.

## Applies when
Любые модели qwen35 с MTP-слоем (Qwen3.8, *-MTP-GGUF).
