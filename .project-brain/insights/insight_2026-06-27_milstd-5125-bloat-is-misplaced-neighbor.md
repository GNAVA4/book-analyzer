# Insight — MIL-STD 5.1.2.5 bloat is caused by a mis-placed NEIGHBOUR (5.1.3), not by page_cut

_Filed: 2026-06-27 (session 010). Provenance: tests_v12 + temp debug print in the
page_cut local-bracket loop._

## Symptom
MIL-STD 5.1.2.5 "Signal precedence" (page 29, page_cut) absorbs 61 677 chars; its
content preview starts with a front-matter leader-dot ToC line ("5.9.12.2 Tools. ....").

## What it is NOT
Not a simple front-matter linear underestimate that a local backward-anchor could fix.
The session-010 local bracket (interpolate a tightly-bracketed page_cut run between its
nearest strong-match neighbours, ToC page span ≤ 5) was built to fix exactly this, and
it correctly DID NOT fire here.

## Actual root (observed positions)
- prior bound 5.1.2.3 (p27, exact) @ pos **250640**
- next bound  5.1.3   (p29, exact) @ pos **218308**  ← BEFORE 5.1.2.3

5.1.3 "Computer controls" is a bare heading (content ". \n5.1.3.1", 10 chars). It — and
5.1.3.1 — match at pos ~218k, i.e. EARLIER in the char stream than 5.1.2.3 (250k) which
precedes them in document order. A position-ORDER INVERSION. The local bracket's
`next_pos > prior_pos` guard correctly rejects the inverted bound, so 5.1.2.5 falls back
to its (early, front-matter) linear estimate → 61 677.

## Consequence for design
- Fixing 5.1.2.5 requires fixing WHY 5.1.3/5.1.3.1 (page 29) land at 218k before 5.1.2.3
  (page 27, 250k) — a matching-occurrence-selection problem (likely a front-matter ToC vs
  body occurrence, or PyMuPDF extraction order), NOT a page_cut estimation problem.
- Any page_cut-side anchoring is blocked by the inverted neighbour; don't keep adding
  page_cut machinery for this.
- Decision (session 010): accept the 5.1.2.5 bloat (1 section, real-count unaffected),
  keep clean v6 linear page_cut. Revisit as a separate matching task if it matters.

Related: [[insight_2026-06-27_v6-linear-best-on-AI-ordering-guard-cascades]].
