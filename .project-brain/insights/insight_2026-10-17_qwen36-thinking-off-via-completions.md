# INSIGHT: qwen3.6-35b-a3b в LM Studio — рассуждение выключается только закрытым <think> в /v1/completions
_Filed: 2026-10-17 session 017_
_Category: tooling_

## The finding
Думающая модель тратит весь max_tokens на рассуждение (2000 токенов на один абзац, content пуст).
LM Studio (chat completions) НЕ передаёт в шаблон ни одного выключателя: `/no_think` (user/system),
`chat_template_kwargs: {enable_thinking: false}`, `reasoning_effort: "low"`, `reasoning: {effort: "none"}`
— все проверены, эффекта нет.

Работает запрос в `/v1/completions` с готовым шаблоном ChatML, где блок рассуждения уже закрыт:

    <|im_start|>user\n{текст}<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n
    + stop: ["<|im_end|>"]

Ответ сразу: ~3 с / ~100 токенов вместо ~50 с / 2000 токенов. Качество разбора на утверждения
при этом хорошее; temperature 0 — детерминированно (3 повтора идентичны).

## Also
- Загрузка (12 ГБ GPU): сначала `lms unload --all` (указание user), затем `lms load
  qwen3.6-35b-a3b -c 4096` с настройками по умолчанию → GPU 75%, но свободной ОЗУ ~0.8 ГБ.
  После работы выгружать.
- Формат ответа: JSON-массив модель иногда сворачивает в один объект — для списков вердиктов
  надёжнее построчный формат («A1 -> B3 да»).

## Applies when
Любой вызов qwen3.x-думающих моделей через LM Studio, где нужен ответ без рассуждения.
Код: app/analysis/claims.py (ClaimsLLM.complete).
