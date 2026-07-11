# INSIGHT: CJK contamination reaches XML through fast_clean_chunk path, not just LLM path
_Filed: 2026-05-28 session 001_
_Category: architecture_

## The finding
glm-ocr substitutes Chinese characters (动机, 语, 文) for blurry/damaged Cyrillic glyphs.
The LLM-clean path has a CJK-aware retry that handles this — BUT sections with `confidence ≥ 0.85`
go through `fast_clean_chunk` (algorithmic, no LLM) which does NOT remove CJK.

**Result:** High-confidence OCR-derived sections can contain Chinese characters in final XML.
In `0e6e53b` before fix: 49 CJK characters across multiple sections.

## How we found it
After fixing the OCR cp1251 crash, 0e6e53b started producing XML. Inspection found Chinese
characters in section content despite sections having confidence ≥ 0.85 (exact_normalized matches).

## Evidence / experiment
| State | CJK chars in 0e6e53b XML |
|-------|--------------------------|
| Before fix (after OCR crash fix) | 49 CJK characters |
| After scrub_foreign_script in post-loop | 0 CJK characters |

## Action taken
Added `scrub_foreign_script(title)` and `scrub_foreign_script(content)` to EVERY `final_node`
in `pdf_parser_neural.py` AFTER the clean step, regardless of which path was taken.
Also applied to `sequence` titles (NavigationTable items).

The key design choice: scrub lives in `pdf_parser_neural.py` post-loop, NOT inside
`fast_clean_chunk`. This is intentional — see "Does NOT apply when" below.

## Applies when
- Any book processed with glm-ocr (damaged/scanned PDF)
- Any section regardless of confidence level
- After all other processing is done (it's the last step before XML assembly)

## Does NOT apply when
- DO NOT move scrub_foreign_script into fast_clean_chunk. fast_clean_chunk is used for ALL books
  including ones with intentional CJK content (a Chinese language textbook for example).
  The scrub in pdf_parser_neural.py is the right place because the decision to scrub is
  context-dependent (OCR artifact vs intentional content).
