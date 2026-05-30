# BUG: Heuristic emits chapter title as just the number («1.», «2.», «§ 1.4.») — name dropped
_Filed: 2026-05-30 session 005 | Status: open_

## Symptom
On some books the HeuristicParser produces ToC items where the title is literally just a
number with a period («1.», «§ 1.4.», «5. /», «6.») and an empty description. The
NavigationTable looks plausible but mapping produces empty content (exact match on «1.»
hits the very first occurrence of «1.» anywhere in the body — typically the page header
or first table — and the next item starts right after, leaving zero content).

Concrete (tests_v4):
- **1332**: 10 empty sections out of 61. All level 1, all titles match `^\d+\.\s*$`.
  All page numbers are valid (8, 17, 37, 42, 55, 73, 92, 114, 140, 157). The chapter
  NAMES exist in the PDF but didn't survive heuristic parsing.
- **12_100229**: 6 empty sections out of 328 (in addition to 15 tiny). Titles:
  «1.», «1.1.», «1.3.    -    », «5.  /», «6.».

## To reproduce
1. Books with ToC layouts where chapter number sits on one printed line and the chapter
   title sits on the NEXT line (multi-line ToC entries), or where chapter number is
   followed by special characters (« - », « / ») before the title.
2. Heuristic `item_pattern_start = ^(\d+)\s+([А-ЯA-Z].+)$` and `item_pattern` both
   require the title text to be on the SAME line as the number.
3. The `pending_title` accumulation logic doesn't kick in here — the «1.» line gets
   matched as `match_strict` (with empty title_part captured by greedy regex), committed
   immediately, and the title line that follows looks like prose, not a structure_start.

## Root cause (hypothesis)
Two-line ToC entries:
```
1.                                                                        8
   Введение в схемотехнику                                                 ← this gets lost
```
or with separator characters between number and title:
```
1.  - Введение                                                             8
```

`item_pattern` matches: title="1.", page="8". The «Введение...» line either:
- Gets dropped by garbage filter (`_is_garbage_toc_item` returns True for `^\d+$`-like
  items, but «1.» trims to a number-only token), OR
- Gets treated as prose and never appended via pending logic.

Need to trace by feeding a synthetic 1332-shaped ToC into HeuristicParser.

## Fix (directions)
- In `_add_node` (or before commit): if title parses to ONLY a numeric token («1.»,
  «§ 1.4.») and the prior line buffer has a non-numeric phrase, prepend / append it.
- Or: lookahead — when committing a numbers-only title with a known page, peek at next
  line; if it's a non-page-bearing alphabetic line, merge.
- Or: change `item_pattern` regex to be stricter — require at least N letters after the
  number prefix; if no letters present, treat the line as a number-only continuation.

## Affected files
- `app/services/toc_parser.py` — `HeuristicParser.parse_toc`, `item_pattern`/`item_pattern_start`,
  `_add_node`, possibly new lookahead logic.

## Detection tooling
- In `_report.json` count titles matching `^\d+(\.\d+)*\.?\s*$` (number-only):
  1332 → 10, 12_100229 → ~6. Healthy books: 0.

## Related: 12_100229 also has duplicate-by-split
For 12_100229 specifically, beyond the title-loss issue, ToC also produces near-duplicates:
«§1.1. Простейшие модели и система параметров логических элементов» (L2 p7) +
«Простейшие модели логических элементов» (L2 p7) — heuristic split a multi-clause ToC
line into two items. Same level, same page, both map to the same body span. Compounds the
mess. Possibly addressable with the same multi-line-merge fix.
