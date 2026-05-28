# INSIGHT: How to audit XML content against reality (and why fitz-vs-fitz is not enough)
_Filed: 2026-05-29 session 004_
_Category: workflow_

## The finding
Comparing XML `<content>` against `fitz.get_text()` of the PDF is **circular for the scan class**:
the pipeline already uses fitz under the hood, so if fitz can't see text (scans, large typography,
text-as-image), both sides agree on "nothing" and coverage falsely reads ~95%.

Two-tier method that actually works:

**Tier A — text-layer audit (`scripts/audit_content.py`)**: per-section
`ratio = content_len / expected_page_range_len`, using a ToC→PDF page offset estimated from
anchor sections. Flags EMPTY / TRUNCATED (ratio<<1) / BLOATED (ratio>>1) / LEAK (content starts
with another section's title) / DUP. This catches **content misplacement** — text that fitz reads
fine but the pipeline files under the wrong section. This is the dominant real bug in the corpus.

**Tier B — vision ground-truth (`scripts/fitz_blindness.py` + `scripts/render_pages.py`)**:
blindness map flags pages with low extractable text but images/large draw area (SCAN/SPARSE).
Then render those pages to PNG and READ them visually to see what is actually printed.

## Key empirical result (this is the surprising part)
In design/creative books the SCAN/SPARSE pages are almost all **full-page illustrations, photos,
or artwork with no body prose** — NOT lost text:
- Do Good Design p.74 = McDonald's "Willkommen in Dachau" photo; p.115 = Marlboro wall ad. Captions only.
- Иглмен p.139 = full-page abstract painting (txt=0).

So a SCAN flag does NOT imply lost content. Vision is required to distinguish "lost prose" from
"legitimate illustration". The genuinely scanned book (0e6e53b) is handled by the OCR path, not fitz.

**Conclusion:** the widespread, real defect is NOT scan-blindness but text-layer **misplacement**
(divider/neighbour section absorbs a chapter's body). Detect it with per-section ratio, not coverage.

## Evidence / corpus-wide audit (coverage / empty / trunc / bloat / leak / dup)
| Book | cov | empty | trunc | bloat | leak | dup |
|------|-----|-------|-------|-------|------|-----|
| 0e6e53b (OCR) | .946 | 0 | 2 | 0 | 0 | 0 |
| Release It! | .972 | 2 | 5 | 0 | 1 | 0 |
| Do Good Design | .93 | 1 | 9 | 1 | 0 | 0 |
| Виды UI | .978 | 0 | 0 | 0 | 0 | 0 |
| MIL-STD | .944 | 92 | 36 | 1 | 0 | 2 |
| Розенсон | .973 | 4 | 4 | 2 | 1 | 0 |
| Кениг | .959 | 1 | 1 | 1 | 0 | 0 |
| Клейнман | .98 | 0 | 1 | 0 | 0 | 0 |
| Иглмен | .955 | 0 | 7 | 3 | 0 | 0 |
| Массель | 1.01 | 3 | 0 | 0 | 0 | 0 |
| parallelnoe | .973 | 4 | 13 | 0 | 0 | 15 |

High coverage + many TRUNCATED/BLOATED = text present but mis-attributed. See
[[bug_2026-05-28_part-divider-absorbs-chapter-body]].

## Applies when
Auditing whether parsed section content matches the source document. Use Tier A for the bulk signal,
Tier B (vision) to disambiguate flagged image-heavy pages.

## Does NOT apply when
- The book is a true scan (no text layer) — then Tier A's PDF side is empty; rely on OCR + vision.
- Tiny offset-anchor count (offset_agree low, e.g. Розенсон 0.5, 0e6e53b 0.67) → per-section ratios
  are less reliable; treat those books' ratios as hints, confirm with vision.
