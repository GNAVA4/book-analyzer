# INSIGHT: Windows cp1251 + U+FFFD crashes print() silently killing OCR pipeline
_Filed: 2026-05-28 session 001_
_Category: tooling_

## The finding
On Windows with cp1251 console encoding, `print()` with U+FFFD (Unicode replacement char `â`)
raises `UnicodeEncodeError`. glm-ocr inserts U+FFFD when it can't decode a glyph. The error
propagates up, OCR loop aborts, `ocr_text` returns None/truncated, and the book is reported
"unreadable" — even if 95% of pages OCR correctly.

**This caused `0e6e53b` to always return empty XML.**

## How we found it
User reported book `0e6e53b` always returned "Документ нечитаем" despite the file being valid.
We traced the OCR loop and found it always failed on page 22 (a damaged page with glyphs the
model couldn't recognize → U+FFFD in output → print crash → exception swallowed → None returned).

## Evidence / experiment
| Approach | Result | Notes |
|----------|--------|-------|
| Without fix: run 0e6e53b | Empty XML every time | Fails at page 22 |
| With _safe_print + stdout.reconfigure | 35 sections, 34 with content | Fully working |

## Action taken
1. Added `_safe_print()` in `ocr_engine.py` — wraps print with UnicodeEncodeError catch +
   re-encodes with `errors='replace'`
2. Added `sys.stdout.reconfigure(encoding='utf-8', errors='replace')` in `app/main.py`

## Applies when
- Any code that prints LLM/OCR output on Windows
- Any pipeline that handles documents with damaged glyphs (scanned books, bad fonts, etc.)
- Any model output that may contain U+FFFD, emoji, or other non-cp1251 characters

## Does NOT apply when
- Running on Linux/macOS (default encoding is UTF-8, U+FFFD prints fine)
- Logging to file (file encoding can be explicitly set to UTF-8)
- Web/API output (not going through Windows console)
