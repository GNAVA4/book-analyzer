# INSIGHT: Extrapolation past anchored positions is dangerous; interpolate only between brackets
_Filed: 2026-06-10 session 008_
_Category: pattern_

## The finding
When computing page→position estimates from known (page, position) anchors,
interpolation BETWEEN anchors is safe and accurate. Extrapolation PAST the
first or last anchor is unsafe and can place sections far from where they
belong.

Concrete case: AI.pdf has anchors clustered in the body of the book. Late
chapters (19.x at page 797) sat past the last anchor. The session 007
implementation extrapolated forward using `(remaining_text /
remaining_pages) × delta_pages`, which assumed the same character-per-page
rate would hold. It did not — pages near the end of AI.pdf are denser
(index, bibliography are tightly packed). Each extrapolated section landed
deeper into the back-matter index. 16 sections lost ~80k chars of real
content.

## How we found it
Session 007 corpus run reported AI real 184→167. I framed it charitably as
"redistribution". User pushed back. Direct XML inspection: chapter 19.6
went from 14 938 chars of bibliography prose to 27 chars of index junk
("P., 760, 1064, 1073\\ninfer"). The lost content existed in v6, was
replaced by index fragment in v7.

## Evidence
| Anchor strategy | AI real | 12_100229 real | MIL-STD 5.1.2.5 |
|---|---|---|---|
| v6: linear estimate | 184 | 301 | 61 677 chars (bloat) |
| v7: anchor-interp with extrapolation | 167 | 275 | 33 chars (fixed) |
| v8 (proposed): bracketing only + ordering guard | 167 (same as v7) | 279 (slight improve) | 33 (still fixed) |

AI doesn't recover even with ordering guard because its lost sections never
violated ordering — they violated reasonability by being past the anchor
range. Only the bracketing-only restriction recovers them.

## Action taken
Restricted interpolation to bracketing case in `_interpolate_position_from_page`.
Falls back to `_estimate_position_from_page` (linear) when only one side has
anchors or none does. Added ordering guard separately to address the
mid-content truncation in 12_100229.

## Applies when
Any position estimator built from samples: page→position, time→event,
spatial→coordinate. Trust interpolation between observed samples, do not
trust extrapolation past them. Whenever the requested input is outside the
sampled range, return the prior best-guess (linear or otherwise) instead of
projecting samples forward.

## Does NOT apply when
The function being approximated is genuinely known to extend linearly past
the sample range (rare in document text-position mapping; common in
calibrated sensors).
