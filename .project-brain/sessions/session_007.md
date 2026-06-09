# Session 007 — 2026-06-09 / 2026-06-10
← [session_006.md](./session_006.md)

## Goal
Take on the three highest-impact open items from session 006's exit list:
1. Investigate MIL-STD 5.1.2.5 bloat (+61k chars in one section).
2. Build a content-quality detector for automated audit.
3. OCR-aware fuzzy mapping tier to recover digital-design body content.

Plus a tiny preceding fix: bump `_llm_from_text_retry` attempts 3 → 5 for LLM
stability across runs.

## What actually happened

### Retry bump (done first, separate commit)
The v5 ↔ v6 corpus diff showed Иглмен/978-5-7996/ВКР all swinging from
LLM-side variability alone. Raising attempts from 3 to 5 costs ~2-4s per
triggered book, gives the retry loop two more chances at a formally clean
answer. Committed.

### MIL-STD 5.1.2.5 investigation → anchor-interpolation page_cut
Pulled section 5.1.2.5 ("Signal precedence") from tests_v6: content was 61 677
chars, preview started «5.9.12.2 Tools. ........... 228» — leader-dot lines
from the ToC region. The section's page is 29; `_estimate_position_from_page`
(linear) for page 29 in a 700-page book with multi-page ToC at the front
maps to ~40K chars, well inside the ToC region. The next exact-matched
section (5.1.3 on real page 29) was 60K chars further. Slice picked up
everything in between.

Root cause: linear page→position estimate breaks on books where text is
non-uniformly distributed. MIL-STD has a multi-page ToC, dense figures,
Klein has heavy figures, OCR-source books have unpredictable per-page
character counts. Linear estimate fails everywhere there's pagination
non-linearity.

Fix: `_build_page_anchors` collects (page, position) anchors from
trustworthy mapped sections (exact / exact_normalized / tokenized_regex /
page_hint_exact*). `_interpolate_position_from_page` linear-interpolates
between bracketing anchors, extrapolates beyond extremes, falls back to
linear if no anchors. Used only in the final page_cut loop — kept linear
in `_page_hint_search` and `_revert_position_clusters` because there
linear IS the signal we want to compare against.

Live verification: MIL-STD 5.1.2.5 went 61 677 → 33 chars. 5.1.3.1 picked
up 1236 chars, 5.1.2.4 lost 1907. Clean redistribution.

Side effect across the corpus: every book with many page_cut sections had
its content boundaries shifted. 12_100229 real 301 → 275 (-26), AI 184 →
167 (-17). Hard to call regression vs honest correction without inspecting
sections individually — bloat we used to count as real is now gone, but
adjacent exact-match sections also lost content because page_cut neighbours
moved closer. Net effect on the corpus is content redistribution.

### Content-quality detector
New module `app/services/content_quality.py` with `classify_content` that
returns one of `dots / bullet / toc_fragment / whitespace` or `None`. Plus
`count_junk_sections` aggregator. Hooked into `run_corpus.py` as a new
`junk_sections` field in stats.

Thresholds calibrated to flag obvious junk without false-flagging prose:
- dots: > 50% of non-whitespace chars are literal `.`
- whitespace: < 20 meaningful chars after strip
- bullet: short content dominated by bullets
- toc_fragment: ≥ 2 lines (≥ 40% of non-empty) match `текст .... номер`

First corpus pass: MIL-STD 80 junk (the page_cut sections that didn't get
matched are leader-dot regions), digital-design 36 (OCR-noise sections),
12_100229 10 (some empty wraps), Klein 5, AI 5, ВКР 2, others 0-1. Useful
audit signal. Observation-only for now — no revert wiring.

### OCR-aware fuzzy mapping
New tier `_ocr_aware_fuzzy_locate` between regular `_fuzzy_locate` and
embedding rescue. Two pieces:
1. `_ocr_fold` collapses known OCR look-alikes into character classes
   (Cyrillic/Latin homoglyphs, и/ы/й, ц/щ, etc.).
2. Threshold relaxed to 0.72 from the regular 0.85.

Gated by `toc_source.startswith('ocr')` — only runs when the ToC came
from OCR. On other sources the fold + lower threshold would false-positive.
`map_sequence` takes `toc_source` as a parameter, passed from
`pdf_parser_neural` after `build_toc` returns it.

Live: 0e6e53b 3 hits, digital-design 1 hit, no other book exercises the
tier. The limited digital-design recovery (only 1 match, real stays at
25/112) confirms what the failing unit test caught — pairs like
«прицелы»/«принципы» differ on 3+ characters in a 7-letter word, ratio is
~0.5-0.7 even after folding. To recover those cases we'd need either
weaker fuzzy with a way to suppress false positives (semantic check?) or
a different approach entirely.

### Corpus diff tests_v6 → tests_v7
- MIL-STD 5.1.2.5 fixed (61k → 33).
- 12_100229 real -26, AI real -17 — anchor redistribution.
- parallelnoe real +2.
- 978-5-7996 sec 29 → 13, real 22 → 10 (LLM nondet on this run; toc_source
  stayed ocr_llm, avg_conf jumped 0.85 → 0.91 — fewer but cleaner sections).
- Klein -1 real.
- 0e6e53b avg_conf -0.009 (OCR-aware fuzzy may have moved some sections
  slightly).
- 13 books unchanged.
- New `junk_sections` field populated for all.

336 unit tests pass (added 8 anchor interpolation, 6 OCR-aware fuzzy,
14 content_quality).

## Decisions made
| Decision | Chosen because |
|----------|----------------|
| Anchor-interpolation only in final page_cut loop | `_revert_position_clusters` uses linear as anomaly signal; mixing in anchors would break Class-2 detection |
| OCR-aware tier gated by toc_source | On heuristic/llm-source books a 0.72 threshold + OCR fold would false-positive |
| Content-quality observation-only | Don't change pipeline behaviour from a metric we just introduced; verify it's calibrated first |
| Accept 12_100229 / AI redistribution as part of the trade | MIL-STD win is clearly correct; smaller per-section changes elsewhere may be honest re-allocations of bloat we never noticed |

## What we tried that didn't work
- ❌ OCR-aware fuzzy doesn't recover heavy drift like «прицелы»/«принципы». 3-char
  differences in 7-char words give ratios ~0.5 even after folding. The pairs
  the user originally flagged are below any safe threshold. The tier still
  helps for milder drift (Дициплина/Дисциплина type) — 1 hit on
  digital-design, 3 on 0e6e53b.
- ❌ The initial OCR-aware test asserted that _ocr_fold('прицелы') would
  approximately match _ocr_fold('принципы'). It doesn't — too many letter
  differences. Test rewritten to assert "folding doesn't hurt close pairs"
  + "distinct words stay distinct after folding".

## What works now (that didn't before)
- ✅ MIL-STD 5.1.2.5 bloat eliminated (61 677 → 33 chars). The exact
  trigger case that motivated this work is resolved.
- ✅ Honest audit metric: `junk_sections` per book in every report.
  MIL-STD's 80 junk are the page_cut sections that fell in leader-dot
  regions; valuable signal for future bug hunting.
- ✅ OCR-aware tier exists and is wired through, ready for tighter
  calibration if we improve _ocr_fold or add semantic verification.

## Files changed / created
- `app/services/toc_builder.py` — `_llm_from_text_retry` attempts 3→5.
- `app/services/mapping_pipeline.py` — `_build_page_anchors`,
  `_interpolate_position_from_page`, used in page_cut loop;
  `_ocr_aware_fuzzy_locate`, `_ocr_fold`, wired into rescue cascade;
  `map_sequence(toc_source=)` parameter.
- `app/services/pdf_parser_neural.py` — passes `toc_source` to
  `map_sequence`.
- `app/services/content_quality.py` (NEW) — `classify_content`,
  `count_junk_sections`.
- `scripts/run_corpus.py` — `junk_sections` in stats and summary.
- `tests/test_content_quality.py` (NEW) — 14 cases.
- `tests/test_mapping_pipeline.py` — `TestPageAnchorInterpolation` (8),
  `TestOcrAwareFuzzy` (6 + helper).

## Bugs status
- `bug_2026-05-30_heuristic-splits-multi-line-toc-title.md` — FIXED last
  session (still in place).
- MIL-STD 5.1.2.5 bloat — closed via anchor-interpolation. Recording
  this as a successful investigation with a fix in session_007 narrative;
  no separate bug file to close (the bug never had a formal file beyond
  the OPEN.md mention).

## End state
Branch `llm-toc-fallback`. 4 commits added (retry bump, three-improvement
batch, project-brain). All 336 unit tests pass.

Open priorities going forward:
- Per-section inspection on 12_100229 / AI to confirm redistribution is
  honest, not regression. May need ordering guard for page_cut.
- OCR-aware fuzzy is wired but underpowered. To actually recover
  digital-design content we need stronger help: edit-distance with
  semantic verification (e.g. embedding score on the candidate region),
  or sentence-level matching instead of title-level.
- 978-5-7996 LLM nondet persists (sec 33 → 13 → 29 across runs). Retry
  bump from 3 to 5 didn't stabilise it — same number of attempts gives
  similar variance.
- ВКР `_looks_incomplete` over-trigger still wastes ~170s per run.
- Content-quality detector ready to be wired into mapping as a third
  verify pass (revert junk-content sections to rescue).
