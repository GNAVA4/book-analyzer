# BUG: Heuristic splits a multi-line ToC title into 2-3 separate items
_Filed: 2026-05-30 session 006 | Status: FIXED (mapping-side post-process)_
_Fixed: 2026-05-30 session 006_

## Fix (applied)
Instead of changing the heuristic parser (risky), added a post-process pass in
`mapping_pipeline.merge_wrap_continuations` called from `pdf_parser_neural` after
`map_sequence`. It detects adjacent items where:
- gap between `end_idx[N]` and `start_idx[N+1]` ≤ 5 chars
- same level (heuristic level assignment matches)
- same page (when both have pages)
- both strategies are trustworthy (exact / exact_normalized / tokenized / page_hint_exact*)
- nxt title does NOT start with its own numerical prefix (digit, §, "Глава N", "Chapter N")

When detected, titles are joined and the wrap items removed from both `mapped` and
`sequence` (so NavigationTable also reflects the merge).

The own-prefix guard was added after the first corpus run, where MIL-STD lost 15
legitimate subsections because sequences like «5.11.1 Stairs…» immediately followed by
«5.11.1.1 General criteria» looked like wraps. The guard recognises 5.11.1.1 as a real
sub-item, not a continuation.

Live (tests_v6):
- 12_100229: 10 wrap-merges, sec 328→318. §1.1 and peers now have full content;
  effective_real 301→309.
- MIL-STD: own-prefix guard rejects all 15 false candidates; sec stays at 747.
- No other book in the corpus triggers wrap-merge (gap > 5 chars elsewhere).
- 302 unit tests pass (+10 for wrap-merge, including own-prefix rejection cases).

## Affected files
- `app/services/mapping_pipeline.py` — `merge_wrap_continuations`, `_is_wrap_continuation`,
  `_OWN_NUMERIC_PREFIX`, `_TRUSTED_FOR_WRAP`.
- `app/services/pdf_parser_neural.py` — call site after `map_sequence`.
- `tests/test_mapping_pipeline.py` — `TestMergeWrapContinuations` (10 cases).

---
_Original analysis below:_


## Symptom
On books where ToC titles wrap across 2-3 lines, `HeuristicParser` treats the continuation
line as a separate item. Mapping then exact-matches that bogus item in the body 1 character
after the parent's end — parent's `content_len` collapses to 1-2 chars (entire body absorbed
by the false sibling).

Concrete (tests_v5/12_100229):
- ToC text:
  ```
  §1.1. Простейшие модели и система                                            7
  параметров логических элементов                                              7
  Простейшие модели логических элементов                                       7
  ```
- Heuristic emits:
  - item N: «§1.1. Простейшие модели и система параметров логических элементов» L2 p7
  - item N+1: «Простейшие модели логических элементов» L2 p7   ← false sibling
- In body, the L1 chapter heading area has these lines literally next to each other:
  `§1.1. Простейшие модели и система\n параметров логических элементов\n Простейшие модели\n логических элементов\n Даже самые сложные…`
- `find_real_indices` matches §1.1 at `start=54686 end=54752`. Then "Простейшие модели
  логических элементов" matches at `start=54753` — 1 char after §1.1's end.
- §1.1's `content_len = 54753 - 54752 = 1`. §1.1 is empty in the XML. The false sibling
  takes everything that should be §1.1's body, then §1.2 matches further down and the
  false sibling gets clen ~2300.

12_100229 has 12 such empty sections (out of 328). Some are L1 chapters («Глава 1.», pattern
similar — multi-line in ToC), some are §1.x parags.

## To reproduce
1. Open `test/12_100229_1_85482.pdf`.
2. Run `run_corpus.py 12_100229`.
3. Inspect the report: 12 sections with `content_len = 0`, all `exact`/`tokenized_regex`/
   `exact_normalized` (high confidence) — so it's not a not-found problem.

## Root cause
`HeuristicParser.parse_toc` reads line by line. Multi-line titles get `pending_title` +
continuation merge logic via `_is_complete_section_name` / structure_start. But when:
- The first line ends without a page number (the page is on the LAST line of the wrap)
- The continuation line starts with a Cyrillic capital letter (looks like a new heading)

…then heuristic commits the first line as one item and starts the second line as a new one.
The page-backfill in `toc_to_linear_sequence` then puts the same page number on both.

In 12_100229 the §-notation breaks the usual heuristics: «параметров логических элементов»
on its own line LOOKS like a valid subsection name, and the parser has no way to tell it's
actually the wrapped tail of «§1.1. Простейшие модели и система …».

## Fix (directions — needs design)
Promising signals to detect a continuation line:
- BOTH the prior committed item AND the continuation line have the SAME assigned page
  number after `toc_to_linear_sequence` page-backfill — and the prior item has no leader-dot
  page marker on its source line. (i.e., the page came from a later line, suggesting wrap.)
- The continuation line is shorter than expected for a real heading (Russian convention:
  unnumbered subsections in §-books typically don't exist at all — every real subsection
  has a § or 1.1.1 prefix).
- In the body of the book, the continuation line appears IMMEDIATELY after the prior item's
  text (no paragraph break), confirming they're a single heading.

A simpler post-process at the mapping stage (after `find_real_indices`): if section N has
exact match and N+1 has exact match with `start_idx(N+1) - end_idx(N) < 5`, and BOTH have
the same page, merge them in the sequence as a single longer title. But this changes the
ToC after the fact and may surprise the user. Better to fix in the parser.

Alternative: don't pursue heuristic fix. Smart-LLM fallback already handles books like this
(if triggered). On 12_100229 the heuristic gives 328 items, ratio ~1.0, so fallback doesn't
fire. Lowering the trigger isn't great either (over-triggers everywhere).

## Affected files
- `app/services/toc_parser.py` — `HeuristicParser.parse_toc`, continuation logic.
- Possibly `app/services/toc_builder.py` — post-process merge for adjacent items with same
  page and 0-content suspicion.

## Detection tooling
- Diagnostic: scan report.json for items with `content_len < 5` and `exact`/`tokenized`
  strategy. Cross-check the previous and next items for same-page + tiny-distance pattern.

## Related
- Closes the misdiagnosis in [[bug_2026-05-30_heuristic-loses-chapter-title-after-number]].
- Same class as residual `bug_2026-05-29_subsection-maps-into-toc` (Кениг 3.Свет/4.Текстура)
  but a different mechanism — that was crammed non-bulleted clusters, this is wrap-line split.
