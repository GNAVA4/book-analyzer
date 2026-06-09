# INSIGHT: «Empty» chapter shells in the XML are valid for hierarchical books
_Filed: 2026-05-30 session 006_
_Category: pattern_

## The finding
When a chapter has the form `N. Chapter Title` immediately followed by `N.1 Subsection Title`
in the body with no preamble paragraph between, the chapter's `content_len` will be 0 in the
report. This is NOT a content-loss bug — the chapter literally has no content of its own;
everything is in the subsections.

1332 demonstrates this perfectly: 10 chapter (L1) sections with `content_len = 0`, but every
one of their L2 subsections has substantial content (1.1=9099, 1.2=4543, 1.3=10129 chars,
etc.). The XML is a correct representation of the book's structure.

## How we found it
Initially listed in `bug_2026-05-30_heuristic-loses-chapter-title-after-number` as a count
of 10 "bad" empty sections in 1332. Closer look at the report showed full chapter titles
(not just numbers) and substantial subsection content under each. Confirmed by directly
running `map_sequence` on the heuristic ToC and watching `start_idx(N+1) - end_idx(N) ≈ 4`
for every L1 chapter — a tiny gap (a few line breaks), no preamble.

## Action taken
- Closed the misdiagnosed bug.
- Don't change pipeline behaviour. The XML is correct.
- The `real_content_sections` metric in `_report.json` is the FALSE signal — it counts
  shell chapters as "empty" without inspecting whether subsections cover the content.

## Applies when
- Book has L1 chapters with directly-attached L2 subsections (academic textbooks like 1332,
  bookфизика's deeper chapters, etc.).
- Chapter has no preamble between its title and the first subsection.

## Does NOT apply when
- Subsections of the empty chapter ALSO have `content_len = 0` (then content really IS lost —
  this is the 12_100229 case: §1.1 empty AND its peers/parent both empty, no children with
  content). See [[bug_2026-05-30_heuristic-splits-multi-line-toc-title]].
- The chapter title doesn't have numbered subsections in the ToC at all (then the chapter
  SHOULD have content of its own).

## Metric refinement (suggestion, not yet implemented)
Add `effective_real_sections` to `_report.json`: count a section as real-content if
EITHER its own `content_len > 100` OR it has at least one descendant section with
`content_len > 100`. This would correctly classify 1332 chapter shells as "covered by
children" instead of "empty".

Until that's in: when auditing a corpus run, always check whether suspiciously-empty L1
chapters have content-bearing L2 children before reporting as a defect.
