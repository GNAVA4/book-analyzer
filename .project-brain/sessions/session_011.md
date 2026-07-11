# Session 011 — 2026-06-27
← [session_010.md](./session_010.md)

## Goal
Corpus quality analysis (excl. MIL-STD) surfaced two real problem classes:
- **B** — page_cut content duplication (12_100229 had 28 sections with identical
  content; Розенсон 5; MIL-STD 10; ~50 corpus-wide).
- **A** — OCR drift in body (digital-design 22% real). A is proposed-only this session.
User asked: implement B, propose options for A.

## Fix B — same-page page_cut staggering (mapping_pipeline.py)
### Root cause (verified via temp DBG_DUP instrumentation on 12_100229)
Content slicing is `full_text[end_idx : nearest start_idx > end_idx]`. All page_cut
sections set `start_idx = end_idx = _estimate_position_from_page(page)`. Multiple
page_cut sections on the SAME page get the SAME estimate → SAME end_idx → SAME slice
window → BYTE-IDENTICAL content (e.g. 12_100229 "Усилители-регенераторы" /
"Мультиплексирование шины адреса", both page 307).

(A second, NON-deterministic mechanism exists — a page_cut landing on an exact
section's content — but it varies run-to-run with LLM/embedding nondeterminism;
not targeted.)

### Implementation
Final page_cut loop rewritten: group page_cut-eligible sections BY PAGE. For a page
with G>1 sections, stagger them across the page width
`base + g * (full_text_len/total_pages) / G` (g in ToC order). G==1 → exactly the
base linear estimate (v6 behaviour preserved — AI and others unchanged). Deterministic,
local, no cross-section position coupling → no cascade risk.

### Tests
+2: same-page sections staggered to distinct positions; single-per-page keeps linear.
Full suite **332 passed**.

### Verification — tests_v13 vs tests_v12 (real duplicates = identical content preview)
| book | dup v12→v13 | real v12→v13 |
|------|-------------|--------------|
| 12_100229 | **28 → 1** | 301 → 302 |
| MIL-STD | 10 → 1 | 595 → 596 |
| Розенсон | 5 → 0 | 74 → 74 |
| parallelnoe | 3 → 0 | 243 → 244 |
| ВКР | 1 → 0 | 12 → 12 |
| **corpus total** | **50 → 4** | 2500 → 2500 |

- Duplicates crushed 50→4 (residual 4 = the non-same-page mechanism: Кениг/Клейнман/
  12_100229/MIL-STD 1 each). Real-count net zero but now HONEST (distinct content
  instead of identical duplicates; staggered same-page sections each get a real slice).
- "Regressions": Иглмен −1 (total 22→21) and 978 −1 (total 27→26) = ToC nondeterminism
  (total changed), NOT B. digital-design −1 (total/pcut unchanged) IS B — a same-page
  split produced one <100 slice on an already-broken OCR book; negligible vs the win.

## Fix A — PROPOSED ONLY (OCR drift in body, digital-design)
Analysis: only 8/112 sections text-match (6 exact + 2 page_hint), 5 embedding_rescue,
94 page_cut. Early page_cut lands in front-matter ('Y 10010, USA', 'второе издание').
Two coupled subproblems: titles don't match OCR'd body (drift) + page_cut offset by
large front-matter. Solving the match fixes the offset too.

Options (full table in chat):
- A1 token-subset fuzzy (cheap, moderate)
- A2 embedding-localization sliding window (robust, costlier) — most powerful
- A3 search-in-window-between-text-anchors with low OCR-fuzzy threshold (best
  risk/reward; uses the 8 real matches as bounds, small window → low threshold safe;
  dodges both drift and page-offset; no blind position assignment → no cascade)
- A4 just lower existing `_ocr_aware_fuzzy` threshold + page-window (page-window is
  offset by front-matter on the very sections that fail → weak)
Recommended: A3 first, A2 to finish hard cases. AWAITING USER choice.

## Files
- `app/services/mapping_pipeline.py` — page_cut loop = by-page staggering.
- `app/services/pdf_parser_neural.py` — temp DBG_DUP added then removed (clean).
- `tests/test_mapping_pipeline.py` — +2 staggering tests.

## Insight filed
- [[insight_2026-06-27_pagecut-dup-is-same-page-collision]]
