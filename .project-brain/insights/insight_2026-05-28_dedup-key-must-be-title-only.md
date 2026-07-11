# INSIGHT: ToC dedup key must be title-only (not title+page) to catch cross-source duplicates
_Filed: 2026-05-28 session 001_
_Category: architecture_

## The finding
When two ToC extraction methods (e.g. heuristic + LLM) both find the same chapter, they often
produce the same title but with slightly different page estimates. Old dedup key `(title.lower(), page)`
considered these DIFFERENT entries → both survived into sequence → duplicate section in XML.

Example in Массель: "2. ПРИНЦИПЫ" (heuristic, p.18) + "2.Принципы" (LLM, p.18) → two entries
with same page → merged correctly. But "2. ПРИНЦИПЫ" (p.18) + "2.Принципы" (p.17) → different
keys → both survive → duplicate section "2. ПРИНЦИПЫ" in NavigationTable.

## How we found it
Массель XML had duplicate entries in NavigationTable. Traced to both heuristic and LLM finding
the same chapter with 1-page difference in page estimate.

## Evidence / experiment
| Dedup key | Массель duplicate | Notes |
|-----------|-------------------|-------|
| (title.lower(), page) | Yes — two "2. ПРИНЦИПЫ" | Old behavior |
| title.lower() only | No duplicate | Fixed behavior |

## Action taken
Changed dedup key in `toc_builder.py::_dedup_and_order` to `title.lower()` only.
When collision: keep entry with larger page number (except when one has `page=None` — keep the one with page).
Applied on ALL return paths, not just at function end.

## Applies when
- Any book where two ToC extraction methods run (heuristic + LLM, or heuristic + OCR+heuristic)
- Page estimates from LLM are often ±1-3 pages from heuristic estimates — normal variation

## Does NOT apply when
- A book legitimately has two chapters with the same title at different pages
  (e.g. "Заключение" in Part I and "Заключение" in Part II). This is a known tradeoff — accepted
  because cross-source duplicates are far more common than same-name sections in one book.
  If this becomes a problem: add level to dedup key (title.lower() + level).
