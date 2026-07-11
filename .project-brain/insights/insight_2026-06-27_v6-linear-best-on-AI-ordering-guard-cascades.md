# Insight — v6 plain-linear page_cut beats anchor-interpolation on AI; ordering guard cascades a single bad match into duplication

_Filed: 2026-06-27 (session 009). Provenance: full corpus v9 run + per-section content
inspection across tests_v6 / tests_v7 / tests_v9 using the new `chars` XML attribute._

## What was observed

The session-007 anchor-interpolation page_cut was meant to fix MIL-STD 5.1.2.5 bloat.
Comparing the SAME AI back-matter sections across three versions (content, not just count):

| Section | v6 (plain linear) | v7 (extrapolation) | v9 (bracketing-only + ordering guard) |
|---|---|---|---|
| 24.1 Image Formation | 17939, real prose | 514, index fragment | 20385, duplicated index blob |
| 25.2 Robot Hardware | 14955, real prose | 429, index fragment | 20385, duplicated index blob |
| 26.2 Strong AI | 23819, real prose | 673, index fragment | 20385, duplicated index blob |
| **AI real (count)** | **184** | 167 | 169 |

In v9, **30 distinct back-matter sections (23.5 … "Online Help") all contain the
byte-identical alphabetical-index blob** `', 25, 912, 912–919, sphex wasp...'`.

## Two findings

1. **Plain linear (v6) is the most correct mapping for AI back-matter.** Both forms of
   anchor-interpolation (v7 extrapolation, v9 bracketing-only) push those sections into
   the index. The aggregate `real_content_sections` count (184 → 167 → 169) actually
   ranks v6 highest — and content inspection confirms v6 is right, not just longer.

2. **The v9 duplication is caused by the ORDERING GUARD, not by bracketing-only.**
   Mechanism: section "23.5 Speech Recognition" mis-matched `exact_normalized` to its
   literal occurrence inside the alphabetical index (the title string appears there).
   The ordering guard then floors EVERY subsequent page_cut position to `>= 23.5.end`,
   dragging 24.1…27.4 all into the index region, where they slice the same span →
   30 identical blobs. A single bad anchor match cascades through the guard.

## Why this matters for the decision

- Removing **bracketing-only** will NOT recover AI (its past-anchor sections already
  fall back to linear; the damage is the ordering-guard cascade) and WILL re-break
  MIL-STD 5.1.2.5 via extrapolation (back to 33 chars).
- The real tension: anchor-interpolation **helps MIL-STD** (5.1.2.5: 61677 bloat → 7999
  plausible, real 594 → 598) but **hurts AI** (184 → 169). MIL-STD's fix happens in a
  BRACKETED region; AI's damage is past-anchor + ordering-guard cascade.

## Metric trap reconfirmed

v7→v9 aggregate was +33 real / +31 effective — looks like a win. Content inspection
shows it is largely duplicated index/bibliography blobs (AI 30×; 12_100229
"проектирования" and "характеристик устройства" both = identical 44957-char block;
"Введение в язык VHDL-AMS" collapsed 35713 → 906). Count masked misplacement. See
[[insight_2026-06-10_content-loss-is-real-not-redistribution]] and the audit-metric-trap
note in CONTEXT.

## Candidate directions (not yet decided)

- **Ordering guard: skip/clamp-cap when the prior anchor is itself a low-trust or
  back-matter match.** Prevents one bad index match from cascading.
- **Detect implausible back-matter matches** (title found only inside the index/index-shaped
  content) and refuse to anchor on them.
- **Option D (session 008): narrow anchor-interpolation to implausible-bloat zones only**
  (MIL-STD qualifies, AI does not) and use plain linear elsewhere.
- Revisit whether ordering guard should apply across page_cut runs at all when the
  flooring source is not a high-trust match.

Related: [[insight_2026-06-10_anchor-extrapolation-is-dangerous]],
[[bug_2026-06-10_anchor-interpolation-extrapolation-into-index]].
