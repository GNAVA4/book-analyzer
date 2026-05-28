# ADR 003: Smart LLM/OCR ToC fallback with validation (instead of patching the heuristic)

**Date:** 2026-05-29 session 004
**Status:** decided + IMPLEMENTED & live-verified (Розенсон 20→77 llm, Клейнман 41→57 llm; complete
books untouched; 260 tests pass). Hardening added: JSON salvage parser, TOC_MAX_TOKENS=3000, level
clamp 1..3. LM Studio ctx can be raised to ~24K (user offered) → then bump TOC_MAX_TOKENS to ~6000.

## Context
The heuristic ToC parser drops subsections on books with messy ToC layouts (Розенсон: non-numbered
subsection titles, page on the next line, titles wrapped across 2–3 lines, recurring subsections).
Multiple attempts to patch the streaming heuristic each fixed one book while regressing another
(see [[bug_2026-05-29_toc-subsections-dropped]] — ungated vs misses==0 gate tradeoff). The streaming
line-by-line parser is fundamentally ill-suited to these layouts.

## Options considered
| Option | Pros | Cons |
|--------|------|------|
| Keep patching heuristic (density gate, wrapped-title accumulation) | no models | whack-a-mole; fragile; one book traded for another |
| Route incomplete books to LLM ToC | robust to layout | LLM hallucinates; needs validation |
| Smart LLM/OCR fallback + validation | quality, anti-hallucination, multi-source | more code, model latency |

## Decision
Build a **smart fallback** triggered only when the heuristic is detectably incomplete:
1. **Incompleteness trigger** — algorithmic, NO models: count ToC-entry-like lines in raw text vs
   heuristic item count; `ratio > 1.5` ⇒ incomplete. (Розенсон 4.14, Клейнман 1.76 flagged;
   Release It! 0.99, Виды, parallelnoe, Массель, Do Good, MIL-STD not flagged.)
2. **Sources** (sequential, GPU-safe): heuristic, LLM-from-text (with retry), OCR+LLM (escalation).
3. **Validation** (`toc_validate.py`): CJK rejection (reuse `has_foreign_script`), formal checks
   (titles, monotonic pages w/ one reset allowed, dups, levels), and embedding **grounding** —
   each title must occur in the book text (lexical-first, embedder for the rest). `grounding ≥ 0.8`.
4. **Selection**: best VALID candidate by grounded-item count; if none valid → keep heuristic
   (never ship unvalidated LLM output).

## Consequences
- ✅ Books with already-good heuristic ToC are untouched (trigger doesn't fire) — zero regression.
- ✅ Anti-hallucination is built in (grounding + CJK + formal), per user requirement quality>speed.
- ⚠️ Adds model latency on incomplete books; requires LM Studio (qwen2.5-7b -c 8192 + embedder +
  glm-ocr), loaded ONE AT A TIME (GPU ≤90% landmine).
- ⚠️ New module `toc_validate.py` + reworked fallback in `build_toc`.

## Revisit when
- If grounding≥0.8 proves too strict/lax on real books, tune `GROUNDING_MIN` / `LEXICAL_TOKEN_HIT`.
- If the incompleteness ratio (1.5) mis-triggers on a new book, recalibrate.

Plan file: `C:\Users\rusla\.claude\plans\sparkling-brewing-pillow.md`
