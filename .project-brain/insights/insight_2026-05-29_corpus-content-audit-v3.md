# INSIGHT: tests_v3 corpus content audit — ToC got rich, subsection MAPPING is the weak link
_Filed: 2026-05-29 session 004_
_Category: architecture | workflow_

## Context
Full pipeline re-run of all 12 books → `tests_v3/` (xml + _report.json) with session-004 changes
(ToC heuristic fix + smart LLM/OCR ToC fallback + mapping Fix A/B). Detailed content audit followed,
corrected by the USER's manual inspection of the XML (ground truth).

## The finding (corrected — see measurement lesson below)
The ToC EXTRACTION is now good (NavigationTable rich, all subsections). This HELPED books whose body
subsection-headings exist cleanly and match by exact text (Release It!: 31→126 real sections, content
distributed correctly). But it EXPOSED/created a content-MAPPING problem for books where:
1. body headings differ from ToC / are absent, OR the printed ToC has strong leader-dots/bullets →
   the new subsections match INSIDE the ToC region and get content = dots "...." / "•" / a ToC
   fragment instead of the real body (Розенсон, Кениг);
2. page numbers do NOT map linearly to text position → `page_cut` places sections on wrong/garbled
   positions (Клейнман — see [[bug_2026-05-29_kleinman-pagecut-regression]]).
Net: extraction improved, but **subsection content mapping** is now the bottleneck. For Розенсон/Кениг
the richer ToC may have made *content* worse (more sections to mis-map). See
[[bug_2026-05-29_subsection-maps-into-toc]].

## Per-book reality (user-confirmed where noted)
| Book | Verdict | Detail |
|------|---------|--------|
| Виды UI 15/15, Do Good 18/18, Release It! 126/129 | ✅ | correct |
| Массель 20/23 | ✅ | user: "действительно всё верно" |
| Иглмен 22/22 | ✅ | user: "всё нормально" |
| 0e6e53b 35/35 | ⚠️ our bug | text on pp.25/120/137/174 is fine, but our OCR handling dropped them (glm-ocr 400) → content lost. [[bug_2026-05-29_ocr-drops-parseable-pages]] |
| **Розенсон 77** | ❌ broken | sections get "•"/ToC fragments; real body lost/misplaced. cov 0.986 MASKED it |
| **Кениг 20** | ❌ broken | 192-page book, tiny tree; content = dots/garbage; preface garbage with NO illustrations → our bug, NOT a scan artifact |
| **ВКР 15** | ❌ broken + ToC bug | ВВЕДЕНИЕ/ЗАКЛЮЧЕНИЕ get ToC fragments; NavigationTable duplicates "ГЛАВА 1" at the END (empty page). [[bug_2026-05-29_vkr-toc-duplicate-chapter]] |
| **Клейнман 40/40** | ⚠️ REGRESSION | page_cut on unreliable page-est → garbled/misplaced; was cov 0.98 in test_v2, now 0.10 |
| MIL-STD 593/747 | ⚠️ | ~92 short clauses exact-matched but empty (dense standard) |
| parallelnoe 243/248 | ⚠️ | appendix D: embedding_rescue mis-locates dense std:: reference (pre-existing) |

## Measurement lesson (IMPORTANT — corrects [[insight_2026-05-29_content-audit-method]])
`coverage` and "real sections (>100 chars)" are MISLEADING for content quality:
- coverage stays HIGH when text is MISPLACED (not lost) — Розенсон cov 0.986 while half its sections
  hold "•"/fragments and neighbours are bloated.
- "real >100 chars" counts leader-dots, bullets, and ToC fragments as real content.
- The pipeline's own `_looks_like_toc_content` flagged 0 of these (it doesn't catch dotted/fragment
  subsection content) — so neither automated metric nor the in-ToC guard caught the breakage.
**Reliable signal = compare each section's content against what is REALLY on its pages.** That needs a
trustworthy page→position map (broken for these books) OR reading the actual pages (manual/vision).
For the next audit: build a per-section "is this the RIGHT content" check (e.g. does the section's body
start with its own heading / contain its title's keywords; flag dots/bullets/ToC-fragment bodies),
not just length/coverage.

## Action taken
Filed discrete bugs (links above). Next: fix mapping quality, priority TBD with user (Клейнман
regression is narrow; subsection-into-ToC is highest impact for Розенсон/Кениг).

## Applies / does NOT apply
- Applies: auditing whether parsed content matches reality after a ToC change.
- Does NOT apply: trusting coverage/real-count as a content-quality gate — they mask misplacement.
