# BUG: Subsection content maps into the ToC region (dots/bullets/fragments) — Розенсон, Кениг
_Filed: 2026-05-29 session 004 | Status: largely FIXED (list-context match preference); minor residual_
_Fixed: 2026-05-29 session 004_

## Fix (applied + measured, session 004)
Root: `find_real_indices` took the FIRST occurrence of a title at/after `current_pos`; for these books
that first occurrence is inside an in-body BULLETED summary / mini-contents (Розенсон) or leader-dot
region, so the section got "•"/dots content while the real body heading (later) was skipped.
Fix in `pdf_utils.py::_search_with_confidence`: prefer the first occurrence whose FOLLOWING text is
prose, not a list. Added `_is_list_context` (leading bullet `•·…`, leader-dots, or another bullet
within ~50 chars) + `_find_first_nonlist` / `_first_nonlist_match`; applied to the exact and tokenized
strategies. Falls back to the first occurrence if ALL are list-like (no behaviour change there).

### Result (scripts/mapping_audit.py)
- Розенсон: «Что такое дизайн?» «•»→real prose (1.30); «Вещь и Дизайн» «•»→«По поводу дизайна…» (1.80);
  «Вещь и Культура» «•»→real (0.91); «Культура и Цивилизация» BLOAT×3.79→real (1.23). Bodies recovered.
- Кениг: was dots/tiny tree → now most sections ratio ~1.0 (Предисловие/Об авторе/Введение/Части/
  главы 5,7–11/Список лит./Глоссарий all ≈1.0). «1. Основы» now real (0.36).
- No regression: Release It! (126/129) and Do Good identical flags; 269 unit tests pass (+5 new).

## Residual (smaller, separate)
- Кениг «3. Свет»/«4. Текстура»: two SHORT chapter titles still match crammed (~19 chars apart) in a
  figure-caption region WITHOUT bullets → `_is_list_context` misses it (no bullet/dots), and Fix B
  cluster-revert needs ≥3 sections + page-spread≥15000. «3. Свет» empty, «4. Текстура» bloats.
- A few part/chapter dividers (Часть I, Глава 1/2) match in a non-bulleted title cluster → empty (their
  content is correctly under subsections now, so low severity).
- Розенсон 2 minor TRUNC subsections.
Possible follow-up: detect non-bulleted "consecutive-title" clusters (run of crammed short matches)
and revert→rescue, OR relax Fix B for short-title crammed runs. Defer unless needed.

---
_Original analysis:_

## Symptom
After the session-004 ToC fix (NavigationTable now has all subsections), many subsection sections in
the XML contain WRONG content — leader dots, a "•" bullet, or a fragment of the table of contents —
instead of the real body. The real body is lost or absorbed by a neighbouring (bloated) section.
Book trees are far too small for the page count (Кениг: 192-page book, tiny tree).

Concrete (tests_v3), user-confirmed:
- Розенсон: `<section title="Что такое дизайн?">` content = `•`; `<section title="Вещь и Дизайн">`
  content = `•`; "Так в чем же состоит предмет дизайна?" content = a ToC fragment
  ("• Принцип изложения материала / Что такое дизайн? …") then jumps to another heading.
- Кениг: `<section title="1. Основы" conf=0.30>` content = thousands of leader dots ". . . .";
  "3. Свет" content = dots. The book is 192 pp but most body text never lands in any section.

## To reproduce
1. Run pipeline on Розенсон / Кениг (tests_v3 already has output).
2. Open XML: subsection `<content>` is "•", dots, or a ToC fragment; real body missing.

## Root cause (analysis, not yet code-confirmed)
The richer ToC adds many subsections whose titles either (a) are NOT present as clean headings in the
body, or (b) appear in the printed ToC followed by strong leader-dots/bullets. During mapping these
titles match INSIDE the ToC page region, so the content slice = dots/bullet/fragment. The guards that
should catch this do NOT:
- `_looks_like_toc_content` (Проверка 1 in `_verify_and_correct_order`) flagged 0 of these — its
  detection (dotted-leader lines in the first 800 chars after end_idx) misses these cases (content is
  a single "•", or fast_clean already stripped dots, or the fragment isn't dotted enough).
- page-distance/out-of-order reverts target only certain strategies; many of these are conf=1.0 exact
  matches that survive.
Result: bad content survives; the real body is either unsliced or absorbed by a bloated neighbour
(coverage stays high — misplacement, not loss). See [[insight_2026-05-29_corpus-content-audit-v3]].

## What we tried (that didn't work)
- ❌ Trusting coverage / "real >100 chars" — masks this entirely (Розенсон cov 0.986).
- ❌ Pipeline's own `_looks_like_toc_content` — does not catch "•"/single-fragment/clean-stripped content.

## Fix (directions — needs design)
- Strengthen the "matched in ToC" / junk-content detector: flag a section whose body is (a) a lone
  bullet/punctuation, (b) dominated by leader dots even after cleaning, or (c) repeats other ToC
  titles — and revert → rescue/page_cut.
- Improve subsection body matching: a subsection title that only matches in the ToC region should be
  rescued by position relative to its parent chapter (which IS found), not left with ToC junk.
- Consider: when parent chapter is found by exact match in body, constrain subsection search to the
  window AFTER the parent's body position (avoid matching back in the ToC).

## Affected files
- `app/services/mapping_pipeline.py` — `_verify_and_correct_order` / `_looks_like_toc_content`,
  rescue windowing, slicing.

## Detection tooling
- Manual XML inspection (ground truth). Automated: needs a content-quality check (junk/ToC-fragment
  body), since coverage and length do not reveal it.
