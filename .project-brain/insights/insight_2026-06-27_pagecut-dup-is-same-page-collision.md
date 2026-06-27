# Insight — page_cut content duplication is a SAME-PAGE position collision

_Filed: 2026-06-27 (session 011). Provenance: DBG_DUP instrumentation on 12_100229 +
tests_v13 verification._

## Mechanism
Content is sliced as `full_text[section.end_idx : nearest start_idx > end_idx]`
(pdf_parser_neural). Every page_cut section sets `start_idx = end_idx =
_estimate_position_from_page(page)`. So TWO page_cut sections on the SAME ToC page get
the IDENTICAL estimate → identical end_idx → identical slice window → byte-identical
content. Corpus-wide this produced ~50 duplicate sections (12_100229: 28; MIL-STD: 10;
Розенсон: 5; parallelnoe: 3).

## Fix (session 011)
Group page_cut sections by page; for a page with G>1 sections, stagger them across the
page width: `base + g*(full_text_len/total_pages)/G`. G==1 → exact linear estimate
(v6 preserved). Deterministic, local, no cross-section coupling → no cascade.
Result: corpus duplicates 50 → 4. Real-count unchanged but honest.

## What it does NOT fix
A second, NON-deterministic duplication: a page_cut section whose linear estimate
happens to land on an EXACT section's content window. This varies run-to-run with
LLM/embedding ToC nondeterminism (residual 4 dups). Lower priority; not same-page.

## Caveat
Staggering splits a same-page group's window, so each section gets a SMALLER slice.
Occasionally a slice falls below the 100-char "real" threshold (digital-design −1).
Acceptable: distinct (if short) content beats identical duplicates. The real-count
metric counted duplicates as real before — removing them is honest, not a regression.

Related: the metric-trap theme [[insight_2026-06-27_v6-linear-best-on-AI-ordering-guard-cascades]].
