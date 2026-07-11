# INSIGHT: Approximate positions (page_cut) must not anchor running_max in out-of-order check
_Filed: 2026-05-28 session 001_
_Category: architecture_

## The finding
`_verify_and_correct_order` maintains `running_max` — the highest `start_idx` seen so far —
to detect out-of-order sections. If a section appears at position X < running_max - 1000 AND
uses a rescue strategy (embedding, LLM, page_hint), it gets reverted as out-of-order.

`page_cut` sets positions via `_estimate_position_from_page` which is a LINEAR INTERPOLATION:
`(page-1) / total_pages * full_text_len`. Two sections at the same page get the same estimate.
Two sections at page N and N+1 get estimates that may straddle where embedding/page_hint actually
found adjacent sections.

**If page_cut updates running_max**, it pushes running_max to an approximate value that may be
HIGHER than where the next text-found sections actually land → those sections get incorrectly
reverted as out-of-order.

## How we found it
First page_cut implementation: reverted_out_of_order went from 0 to 4.
Example: "Часть I." page_cut at est~54K → running_max=54K. "2. Исключение" page_hint found
at ~50K → 50K < 54K-1000 → reverted. ("2. Исключение" is a real well-matched section!)

## Evidence / experiment
| page_cut running_max behavior | reverted_out_of_order | real_content_sections |
|-------------------------------|----------------------|----------------------|
| Updates running_max (first impl) | 4 | 27 |
| Skips running_max update | 0 | 31 |

## Action taken
In `_verify_and_correct_order`, out-of-order loop:
```python
if strat == 'page_cut':
    continue  # skip: approximate position, don't update running_max, don't revert
```
page_cut sections are invisible to the ordering logic. They provide content positions
but don't influence the verification cascade for adjacent sections.

## Applies when
Any "approximate position" strategy (not found by text search, position is an estimate).
General rule: only TEXT-FOUND positions should anchor running_max.

## Does NOT apply when
Positions derived from actual text search (exact, tokenized, page_hint, fuzzy, embedding) —
those ARE reliable enough to anchor running_max and participate in order checking.
