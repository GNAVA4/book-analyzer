# Session 009 — 2026-06-27
← [session_008.md](./session_008.md)

## Goal
1. Add per-section character count to the XML tree.
2. Full corpus run → tests_v9, diff vs v7 to see what session-008's fixes
   (bracketing-only + ordering guard) actually did.
3. User leaning toward removing bracketing-only ("сильно деградирует качество") —
   verify before acting.

## What changed (code)
- `app/services/xml_builder.py` — `create_element` now emits a `chars` attribute on
  every `<section>` = length of that section's OWN content (matches what lands in
  `<content>`; `0` when empty). Surgical, additive. 14/14 xml unit-tests green.
  Sanity-checked: `chars="123"` for 123-char content, `chars="0"` for empty.

## Corpus run
- Full 19-book run → tests_v9 (working tree: bracketing-only + ordering guard).
  First attempt (background shell bqhf5hldm) was interrupted between sessions after
  9 books. Resumed remaining 10 via scratchpad `resume_v9.py` (skips books that
  already have an xml). All 19 complete.
- LM Studio confirmed up (qwen2.5-7b-instruct, glm-ocr, embeddings).

## Diff v7 → v9 (per-book report.json)
Aggregate: **+33 real / +31 effective.** Looks like a win. Only nominal regression:
Иглмен −1. Gains: 978-5-7996 +11, digital-design +11, MIL-STD +4, 12_100229 +4,
AI +2, Кениг/Клейнман +1.

## The aggregate is a METRIC TRAP — confirmed by content inspection
Using the new `chars` / report content_len, inspected the actual section content:

**AI:** 30 distinct back-matter sections (23.5 … "Online Help") in v9 all contain the
**byte-identical alphabetical-index blob** (20385 chars each):
`', 25, 912, 912–919, sphex wasp...'`. Not recovery — duplication.

**12_100229:** "проектирования" and "характеристик устройства" both = identical
44957-char block (duplicate). "Введение в язык VHDL-AMS" collapsed 35713 → 906
(lost real content to a bibliography fragment).

## Decisive 3-way comparison (v6 / v7 / v9) on AI back-matter
| Section | v6 plain linear | v7 extrapolation | v9 bracketing+guard |
|---|---|---|---|
| 24.1 Image Formation | 17939 real | 514 index frag | 20385 dup index |
| 25.2 Robot Hardware | 14955 real | 429 index frag | 20385 dup index |
| 26.2 Strong AI | 23819 real | 673 index frag | 20385 dup index |
| **AI real** | **184** | 167 | 169 |

**v6 (plain linear, no anchor-interpolation) is the most correct for AI.** Both
anchor-interpolation variants break the back-matter into index garbage.

MIL-STD 5.1.2.5 (the target case) shows the opposite — anchor-interpolation helps:
| | v6 | v7 | v9 |
|---|---|---|---|
| MIL-STD real | 594 | 594 | 598 |
| 5.1.2.5 len | 61677 bloat | 33 stripped | **7999 plausible** |

## Root cause of v9's AI duplication = ORDERING GUARD, not bracketing-only
"23.5 Speech Recognition" mis-matched `exact_normalized` to its literal occurrence
inside the alphabetical index. The ordering guard then floored every subsequent
page_cut position to `>= 23.5.end`, cascading 24.1…27.4 into the index → identical
blobs. Removing bracketing-only would NOT fix AI (past-anchor sections already fall
to linear; the cascade is the guard) and WOULD re-break MIL-STD via extrapolation.

## Decision pending (user's)
The tension is real: anchor-interpolation helps MIL-STD, hurts AI. Options recorded
in [[insight_2026-06-27_v6-linear-best-on-AI-ordering-guard-cascades]]:
- Fix the ordering-guard cascade (don't floor on low-trust/back-matter anchors).
- Detect/refuse implausible index-shaped matches as anchors.
- Option D: narrow anchor-interpolation to implausible-bloat zones only.
- Revert anchor-interpolation toward v6 linear, accept MIL-STD 5.1.2.5 bloat.

## Evidence
- tests_v9 (19 books) written with `chars` attribute.
- Per-section content previews captured (AI 30× identical index; 12_100229 dup).
- v6/v7/v9 side-by-side on AI back-matter + MIL-STD 5.1.2.5.

## NOT done / awaiting direction
- No mapping-logic change made this session (would change settled session-007 decision
  + needs another expensive corpus run — user's call).
- Иглмен −1 not investigated (small).

## Continued — ordering-guard fix + tests_v10 verification

User chose "fix ordering guard". Implemented + verified.

### Code change (mapping_pipeline.py)
- New constant `ORDERING_FLOOR_MAX_ADVANCE_PAGES = 25` (documented, tunable).
- page_cut ordering-guard floor now applies ONLY if it advances the section forward
  by ≤ 25 pages of text from its own page-estimate. A larger advance means the
  floor source is mis-placed (matched into back-matter) → floor skipped, section
  stays on its linear estimate (v6 behaviour). New counter `skipped_floor` in log.
- Test added: `test_floor_skipped_when_prior_is_misplaced_far_forward`. 68/68 pass
  (both original 12_100229 guard tests still green).

### tests_v10 verification (full corpus, content-inspected, not just counts)
- ✅ **AI cascade FIXED.** ch.24–27 back-matter: v9 had 31 sections sharing identical
  20385-char index blob; v10 has the v6 content exactly (24.1=17939, 25.2=14955,
  26.2=23819 real prose). Log: AI "(1 floors skipped: source mis-placed)".
- ✅ **MIL-STD 5.1.2.5 preserved** = 7999 (v9 and v10 identical).
- ✅ **12_100229 dup FIXED.** "Введение в язык VHDL-AMS" 906→35713 (recovered);
  "характеристик устройства" 44957(dup)→9244. v10 has far fewer duplicates than v6
  (v6 had len 837 × 13 sections; v10 max dup ×3). v10 cleaner than v6 here.

### Honest v6/v9/v10 totals (real_content_sections)
v6=2499, v9=2478 (inflated by dup garbage), v10=2467.
- v10 vs v9 −11 = mostly REMOVING digital-design's 11 dup-garbage sections (36→25=v6).
- v10 vs v6 −32, but breakdown matters:
  - 12_100229 −22 vs v6 is NOT a real regression (v6 inflated by 837×13 dups; v10 cleaner).
  - MIL-STD +4, Кениг +1, parallelnoe +2 (gains).
  - **AI −15 IS a real regression** (ch.19–22 anchor poisoning, content-verified).

### Remaining real problem: AI ch.19–22 anchor poisoning
19.6, 20.1–20.4, 21.1–21.7, 22.1–22.2 are page_cut in BOTH v6 and v10, but v10's
position estimate uses anchor-interpolation poisoned by a mis-placed trusted match
(matched into the index), projecting these pages into the back-matter (index
fragments of 10–55 chars vs v6's 12k–30k real prose). Same root cause as the floor
cascade, different channel (anchor path vs floor path). Floor-cap fixed the floor
channel only.

### Decision (user chose B): revert anchor-interpolation to v6 plain linear
User chose B over A/C: remove the whole anchor-interpolation + ordering-guard
machinery; page_cut returns to pure `_estimate_position_from_page` (v6). MIL-STD
5.1.2.5 bloat to be handled later by a separate narrow bloat detector.

### Revert executed (mapping_pipeline.py + tests)
- page_cut loop restored to v6 pure-linear (matches `git show 151726e~1`).
- Removed dead `_build_page_anchors`, `_interpolate_position_from_page`,
  `_TRUSTED_FOR_ANCHOR`, `ORDERING_FLOOR_MAX_ADVANCE_PAGES` (left a breadcrumb comment).
- Tests: dropped `TestPageAnchorInterpolation` + `TestPageCutOrderingGuard`; added
  `TestPageCutLinear` (linear estimate + misplaced-prior-does-not-drag). Full suite
  **330 passed**.
- `chars` XML attribute KEPT (orthogonal, useful for content audits).

### tests_v11 verification — RUNNING (bg bh2zc0s5q)
Expectations: AI back ~184 (no cascade/index dups), 12_100229 ~ v6, MIL-STD 5.1.2.5
reverts to 61677 bloat (the regression we now own to fix narrowly).

### NEXT (separate focused step): MIL-STD 5.1.2.5 narrow bloat detector
5.1.2.5 absorbed +61k via content slicing `full_text[end_idx : nearest later start]`
when following sections weren't located. Design a targeted detector (section content
>> plausible page-span size ⇒ cap/redistribute) WITHOUT the global anchor machinery.
Needs its own design + verification. Do NOT reintroduce anchor-interpolation.

## Insights filed
- [[insight_2026-06-27_v6-linear-best-on-AI-ordering-guard-cascades]] (root cause +
  ordering-guard floor cascade; anchor-poisoning is the same mechanism via a 2nd channel)
