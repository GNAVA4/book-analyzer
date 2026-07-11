# INSIGHT: page_cut must run AFTER the final _verify_and_correct_order, not before
_Filed: 2026-05-28 session 002_
_Category: architecture_

## The finding
`page_cut` was placed BEFORE the final `_verify_and_correct_order` call in `map_sequence`.
This caused sections found by rescue (embedding/LLM/page_hint) and then reverted as
`reverted_out_of_order` by the FINAL verify to end up as `conf=0.00` / empty content.

The sequence that breaks it:
1. Section is in `not_found` after EARLY verify
2. Rescue (e.g. embedding_rescue) finds it at position X → `start_idx = X`
3. page_cut runs: `start_idx != -1` → SKIPS the section
4. FINAL verify: position X is out-of-order relative to adjacent sections → reverts → `start_idx = -1`
5. Section stays at `conf=0.00` — page_cut won't run again

## How we found it
Иглмен: 2 sections ("Не склеивайте детали" p=143, "Не боитесь рисковать" p=180) remained at
`conf=0.00` even after page_cut was implemented. Stats showed `reverted_out_of_order: 2`.
Moving page_cut after the final verify fixed both → `real_content_sections: 22/22`.

## Evidence / experiment
| page_cut placement | real_content_sections | reverted_out_of_order | notes |
|--------------------|----------------------|----------------------|-------|
| Before final verify (old) | 20/22 | 2 | Иглмен |
| After final verify (fixed) | 22/22 | 0 | Иглмен |

Also confirmed Release It! unchanged: 31/34 (not affected by the move).

## Action taken
In `mapping_pipeline.py::map_sequence`: moved the page_cut block to run AFTER
the final `_verify_and_correct_order` call. Updated comment to explain WHY.

## Applies when
Any section that:
- Is found by a rescue strategy (embedding, LLM, page_hint)
- Gets placed at a position the final verify considers out-of-order
- Has a known page number in the ToC

These sections end up at `start_idx=-1` AFTER the final verify. page_cut is the
correct fallback — their headings are absent from body text, any position is approximate.

## Does NOT apply when
- Sections that were ALREADY at `start_idx=-1` before rescue (title absent from body) →
  these were correctly caught by the OLD page_cut placement too
- page_cut is excluded from running_max and out-of-order checks in the final verify,
  so moving it after the final verify does NOT create a loop risk
