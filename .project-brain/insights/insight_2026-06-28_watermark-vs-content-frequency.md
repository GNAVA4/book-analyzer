# Insight — separating per-page watermarks from repeated CONTENT by page-fraction

_Filed: 2026-06-28 (session 013)._

## The problem
Removing repeated header/footer/watermark lines by frequency is dangerous: a high repeat
count can be a watermark OR legitimate repeated content. Concrete corpus cases:
- TRUE watermarks (remove): «Библиотека БГУИР» 100% of pages, «MIL-STD-1472G» 100%,
  «Chapter» 82%.
- CONTENT that LOOKS repeated (must keep): parallelnoe «Объявление» 39% / «Результат» 32%
  (code-block labels); ВКР «Дальневосточный»/«Северо-Западный» 31% (federal-district names
  in regional-statistics tables). Deleting these = data loss.

## The separator
Express the count as a FRACTION OF PAGES (count / total_pages). True per-page watermarks
sit at 82–100%; repeated content sits at ≤39%. The 39%↔82% gap is clean and wide. Threshold
**0.60** removes watermarks with a 22pp margin and protects content with a 21pp margin.
A naive 0.30 threshold deletes the content terms — verified.

## Two implementation rules learned
1. **Match exact stripped LINES, not substrings.** A substring count of «Chapter» (940) or
   «Объявление» (260) overstates removal; as standalone lines they are far fewer. Line-exact
   matching is the safe unit and shrinks the removed set.
2. **Strip from section CONTENT, not from full_text before mapping.** Removing lines from
   full_text shortens it → page_cut linear positions (`(page-1)/total_pages * len`) shift →
   neighbours' slice windows move → broad churn (136 sections, real ones truncated). Keep
   full_text stable for mapping; strip the junk lines only from each section's sliced content.

## Not solved by this
Header noise with trailing FILL chars («…микросхемы______», «Гпава 4____») and short chapter
headers «Глава 9» (per-chapter, ~30×, <20 chars, <60% pages) survive — they need a
pattern/normalize step, not frequency. See session_013 "other problems".

Related: [[insight_2026-06-28_body-source-was-toc-ocr-not-body]] (metric-trap theme: cleaning
honestly lowers the real count when junk was padding sections above 100).
