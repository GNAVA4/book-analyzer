# BUG: OCR handling drops parseable pages on glm-ocr 400 ("Failed to parse input")
_Filed: 2026-05-29 session 004 | Status: code change applied, AWAITING live verification_
_Code applied: 2026-05-30 session 005 — needs live re-run before marking FIXED_

## Code change (applied — unit-tested only)
The 400 error body itself contains the OCR'd text (`{'error': 'Failed to parse input at pos N:
\n<page_text>'}`) — the failure is in response parsing, not in OCR. Now in `ocr_document` we
catch `BadRequestError` separately and:
1. Extract the embedded text via `_extract_text_from_ocr_error` (regex on body['error'] / str(e)).
2. If extraction fails AND PyMuPDF has any text layer for the page, fall back to it.
3. Otherwise log and continue with empty text (old behaviour).

Files: `app/services/ocr_engine.py` (+`_extract_text_from_ocr_error`, BadRequestError branch).
Tests: `tests/test_ocr_engine.py::TestExtractTextFromOcrError` (6 cases). 281 unit tests pass.

## Verification still needed (do NOT mark FIXED until done)
- Re-run 0e6e53b through OCR. Old behaviour: pp.25/120/137/174 silently lost.
  Expected: should log "OCR page N: recovered N chars from 400" and content appears in the XML.
- Confirm no regression on books that DON'T hit the 400 path (most of the corpus) — they should
  process identically to before.
- The body['error'] format assumption is empirical (one observed example). If the real error shape
  differs slightly (different quoting, different key name), recovery returns empty and we silently
  fall through — verify on the actual error to make sure the regex matches.

## Symptom
During the tests_v3 run, 0e6e53b lost content from pages 25, 120, 137, 174 — glm-ocr returned
HTTP 400 `{'error': 'Failed to parse input at pos 0: …'}` for those pages, so their text is missing
from the OCR output (and thus from the XML). The USER confirmed the text on those pages is fine /
readable — so this is OUR handling bug, not unreadable pages.

Example error:
```
OCR page 25 failed: Error code: 400 - {'error': 'Failed to parse input at pos 0:
\n8-e правило\nДелегируйте!\n\nВы должны знать, какую работу можете выполнить сами…'}
```
The 400 body actually CONTAINS the OCR'd text (the model produced output but the API/our request
framing rejected it at parse pos 0 — looks like the model's response or our message payload tripped a
JSON/parse path).

## To reproduce
Re-run 0e6e53b through OCR; observe "OCR page N failed: Error code: 400" for pp.25/120/137/174.

## Root cause (hypothesis)
glm-ocr / LM Studio returns 400 "Failed to parse input at pos 0" for certain pages. The error text
embeds the page content, suggesting the failure is in request/response parsing (possibly an
oversized or specially-charactered image payload, or a response the OpenAI client can't parse), not
a genuine OCR failure. Our `ocr_engine` treats the 400 as a hard per-page failure and skips the page
(no retry / no fallback), losing the content.

## Fix (directions)
- Add retry/fallback in `ocr_engine` for per-page 400s: retry once (e.g. lower image DPI / re-encode),
  and/or fall back to the PyMuPDF text for that page if available.
- Investigate the 400 itself: is it payload size, image encoding, or a response-parse issue in the
  client path? The error embedding the text hints the model produced output — capture and use it.

## Affected files
- `app/services/ocr_engine.py` — per-page error handling (`OCR_PAGE_TIMEOUT_SEC` area, the 400 path).

## Notes
- Low-severity for 0e6e53b overall (35/35 sections still produced), but it's silent content loss on
  good pages — should be made robust.
