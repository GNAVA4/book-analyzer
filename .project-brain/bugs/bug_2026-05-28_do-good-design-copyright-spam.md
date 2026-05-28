# BUG: Do Good Design — "Об авторе" section contains copyright/legal text
_Filed: 2026-05-28 session 001 | Status: open_

## Symptom
Section "Об авторе" in Do Good Design XML contains text from a "Books.Ru" copyright/legal page
instead of the actual author bio. The section exists in NavigationTable, has non-zero confidence,
but the content is wrong.

## To reproduce
1. Process `7701_b_Do_Good_Design_...pdf`
2. Open XML, find `<section title="Об авторе">`
3. Content contains copyright/legal text ("Books.Ru", "правообладатель", etc.) instead of author bio

## Root cause
1. `find_real_indices` finds "Об авторе" with exact match (conf=1.0) in a zone that overlaps
   a "Books.Ru" copyright notice page.
2. `_verify_and_correct_order` page-distance check: match position is far from expected page
   → saves to `_backup`, sets `start_idx=-1`, marks `reverted_page_distance`.
3. Rescue (page-hint/fuzzy/embedding/LLM) fails to find a better location — actual "Об авторе"
   text either doesn't exist in extracted text (image-based page) or is not in rescue window.
4. `_restored_after_rescue_fail`: no better result found → restores backup with conf×0.7.
5. Content sliced from the copyright-zone position → contains legal text.

## What we tried (that didn't work)
- ❌ Nothing tried yet — bug identified, not investigated further

## Fix
Not yet applied. Options:

**Option A** — `_looks_like_legal_content` check before restore:
Before `_restored_after_rescue_fail` restores a backup, preview content at that position and
check for copyright patterns ("Books.Ru", "©", "правообладатель", ISBN patterns). If detected
→ don't restore, leave as not_found with empty content.

**Option B** — Lower restore confidence further:
conf×0.7 is still high enough to get content. Change to conf×0.3 to signal low reliability.
Downstream: sections with conf<0.3 could be flagged in UI.

**Option C** — Strip copyright blurbs from full_text during preprocessing:
Remove known publisher boilerplate from full_text before any mapping. Risk: might remove
legitimate mentions of these patterns in body text.

## Why this happened / how to prevent
`_restored_after_rescue_fail` is a "better something than nothing" heuristic. For sections
where the only found position is in spam/legal zones, "nothing" (empty content) is actually
better than wrong content.

## Affected files
- `app/services/mapping_pipeline.py` — `_restored_after_rescue_fail` logic (fix target)
