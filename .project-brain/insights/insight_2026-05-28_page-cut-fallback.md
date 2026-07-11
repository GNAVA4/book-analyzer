# INSIGHT: page_cut fallback for section headings absent from body text
_Filed: 2026-05-28 session 001_
_Category: architecture_

## The finding
Some section headings in PDFs exist ONLY in the Table of Contents as plain text. In the body,
the heading is rendered as large/decorative typography that PyMuPDF extracts as nothing (empty
string or not at all). For these sections ALL text-based rescue strategies fail — not because
the rescue logic is wrong, but because the target text is physically absent.

The fix: after all rescue tiers, if a section still has `start_idx=-1` but has a known `page`,
estimate the position from the page number and set `start_idx = end_idx = estimated_position`.
Content is then sliced from that point to the next section's start. This is the `page_cut` strategy.

## How we found it
Release It! regression: 7 sections showed conf=0.00 after session 001 changes.
Ran diagnostic script checking word presence in the rescue window for each failing section:
- "Благодарности": hits=0/1 in window (word absent from body)
- "1.2. Важность интуиции": hits=0/2 (both words absent)
- "Часть I. Стабильность": hits=1/2 (only common word "часть" present)

Confirmed: `reverted_in_toc: 7` (correctly found in ToC), rescue couldn't find them in body.

## Evidence / experiment
| Approach | Result | Notes |
|----------|--------|-------|
| All rescue tiers (page_hint, fuzzy, embedding, LLM) | conf=0.00, empty content | Title absent from body |
| page_cut (estimate position from page#) | 31/34 sections with content | +6 sections vs before |
| page_cut without running_max exclusion | reverted_out_of_order: 4 | page_cut positions disrupted ordering |
| page_cut + running_max exclusion | reverted_out_of_order: 0 | Correct |

## Action taken
- `mapping_pipeline.py::map_sequence`: page_cut fallback added as last step before final verify
- `mapping_pipeline.py::_verify_and_correct_order`: page_cut excluded from running_max + out-of-order revert
- `pdf_parser_neural.py`: page_cut bypasses `fix_chapter_boundary`, uses `fast_clean_chunk` directly
- confidence=0.30 signals "positional estimate, not title match"

## Applies when
- Section heading is rendered as large/decorative typography (chapter dividers, part headers, front-matter sections)
- After ALL text-based rescue strategies return nothing
- Section has a known page number in the ToC

## Does NOT apply when
- Section heading IS present in body text but in a different form — that's a rescue problem,
  not a page_cut problem (fuzzy/embedding should handle it)
- Book has extremely inaccurate page numbers in ToC — page_cut position would be wrong
  (but still better than empty content)
- Section has no page number — page_cut can't estimate position, skips silently
