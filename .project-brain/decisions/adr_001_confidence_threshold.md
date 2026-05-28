# ADR 001: CONFIDENCE_THRESHOLD = 0.85

**Date:** 2026-05-28  
**Status:** Accepted

## Decision
Sections with confidence ≥ 0.85 use `fast_clean_chunk` (algorithmic).
Sections with confidence < 0.85 use LLM boundary fix + LLM clean.

## Options considered
| Option | Value | Tradeoff |
|--------|-------|----------|
| 0.80 | Previous value | More sections through fast_clean, some with minor artifacts |
| **0.85** | **Current** | **Exact + tokenized_regex + exact_normalized → algo; rescue → LLM** |
| 0.90 | Too high | LLM clean called for tokenized_regex matches which are reliable enough |

## Why 0.85
- `exact` (1.0) and `tokenized_regex` (0.85) are reliable enough for algorithmic clean
- `exact_normalized` (1.0) added this session — also reliable
- `page_hint_tokenized` (0.75) and all rescue strategies (0.40–0.70) benefit from LLM clean
- LLM clean returns original text 30–40% of the time due to context errors — not worth calling on reliable matches

## Consequence
LLM clean is NOT called for sections found via exact/tokenized/exact_normalized.
Any OCR artifacts in those sections must be handled by `fast_clean_chunk` + `scrub_foreign_script`.
