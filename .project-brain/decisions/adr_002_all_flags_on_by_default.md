# ADR 002: All pipeline flags ON by default

**Date:** 2026-05-28  
**Status:** Accepted

## Decision
`parse_pdf_neural` defaults: `deep_scan=True, use_ocr=True, llm_expand=True, validate_toc_ocr=True`
UI checkboxes: all checked by default.

## Rationale
User: "нужно включить все по умолчанию всегда, в полном наборе всех возможностей быть, иначе зачем добавлять это было"

If a feature exists, it should be used. Partial pipelines produce worse results and create confusion
about why one book worked and another didn't.

## Consequences
- Every upload runs the full cascade including OCR and deep_scan
- OCR is only triggered when document is actually unreadable (check_document_readability gate)
- deep_scan is only triggered when ToC extraction yields ≤ 3 sections (gate in toc_builder.py)
- validate_toc_ocr adds ~1 minute per book but is informational only (doesn't block processing)
- Users who need speed can uncheck boxes in UI
