# INSIGHT: Don't soften regressions with "redistribution"; verify per section
_Filed: 2026-06-10 session 008_
_Category: workflow_

## The finding
When a corpus-wide change moves content boundaries, the per-section
content_len numbers shift. It is tempting to interpret those shifts as
"redistribution" — perhaps the loss in one section corresponds to a gain
elsewhere, and the change is overall correct. This is exactly the trap that
the OLD coverage metric set in session 004 (Розенсон cov 0.986 while
sections held «•» / dots): aggregate numbers can stay nice while individual
sections are wrong.

In session 007, I shipped anchor-interpolation and noted in the commit
message that "Whether this is a regression or a more honest measurement
isn't decidable from the metric alone". That framing let me ship without
opening any of the XMLs. The user pushed back, and direct inspection
confirmed real content loss: AI 19.6 went from 14 938 chars of correct
bibliography text to 27 chars of back-index junk.

The right default is: regression until proven otherwise. The cost of
inspecting a few lost sections in the XML is half an hour. The cost of
shipping a regression and discovering it later is much higher.

## How to apply
Whenever a change shifts content_len numbers in the corpus diff, and at
least one book lost ≥5 sections of real content:

1. Open at least three lost-real sections in the new XML and the prior XML.
2. For each, read the content. Categorize:
   - Bloat removed (previous content was junk: ToC fragment, leader-dots,
     wrong index region) — the change is honest.
   - Real content cut (previous content was correct prose, new content is
     truncated mid-word, or replaced by index/header/footer text) — the
     change is a regression.
3. If any case falls in the "regression" bucket, do not ship. Either revert,
   gate, or shrink the scope of the change.

## Applies when
Any pipeline change that touches mapping, slicing, or position estimation.

## Does NOT apply when
Pure additive changes (new fields in report, new metric, new validation
that flags but doesn't act). Those can ship without per-section
verification.
