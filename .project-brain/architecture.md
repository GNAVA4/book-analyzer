# Architecture — Book Analyzer
_Last updated: 2026-06-28 (session 013)_

## What this system does
Parses PDF/DOCX/TXT books into structured XML: extracts Table of Contents and slices each section's
content. The neural pipeline uses a multi-tier cascade with local LLM, OCR, and embeddings as fallbacks
for both ToC extraction and section mapping — handles damaged, scanned, and complex-layout books.

## System diagram

```
[Browser UI]
    │  POST /upload (multipart PDF)
    │  WS  /ws/analyze {temp_id, flags}
    ▼
[FastAPI — app/api.py]
    │  fast mode → pdf_parser_fast / docx_parser / txt_parser
    │  neural mode ──────────────────────────────────────────────┐
    ▼                                                            ▼
[pdf_parser_neural.py]  ←── coordinator, thin                   │
    │                                                            │
    ├─► [toc_builder.py]  — ToC cascade + smart fallback        │
    │       ├─► toc_parser.py   (heuristic)                     │
    │       ├─► llm_engine.py   (extract_toc_json + salvage)    │
    │       ├─► ocr_engine.py   (glm-ocr)                       │
    │       ├─► toc_validate.py (score/validate candidates)     │
    │       │      └─► embedding_engine.py (grounding)          │
    │       └─► toc_validator.py (OCR verify, optional)         │
    │                                                            │
    ├─► [mapping_pipeline.py]  — section mapping (5 levels)     │
    │       ├─► pdf_utils.py   (regex search)                   │
    │       ├─► embedding_engine.py (semantic rescue)           │
    │       └─► llm_engine.py  (locate_section, LLM rescue)     │
    │                                                            │
    ├─► [llm_engine.py]  — content clean (boundary + clean)     │
    ├─► [pdf_utils.py]   — fast_clean_chunk (algo)              │
    └─► [xml_builder.py] — final XML assembly                   │
                                                                 │
[LM Studio at 127.0.0.1:1234]  ◄──────────────────────────────┘
    ├─ qwen2.5-7b-instruct  (LLM, ctx=8192)
    ├─ glm-ocr              (vision OCR)
    └─ text-embedding-qwen3-embedding-0.6b
```

## Components

### pdf_parser_neural.py
- **What:** Main coordinator for the neural pipeline. Thin (~265 lines).
- **Why it exists:** Entry point that sequences all stages; contains no cascade logic itself.
- **Location:** `app/services/pdf_parser_neural.py`
- **Key files:** same
- **Depends on:** toc_builder, toc_validator, mapping_pipeline, pdf_utils, llm_engine, ocr_engine
- **Used by:** app/api.py WebSocket handler
- **Non-obvious:** `scrub_foreign_script` is called HERE after the clean loop, not inside fast_clean_chunk.
  This is intentional — it must run on ALL paths (fast and LLM). Do not move it into fast_clean_chunk.
  The CONFIDENCE_THRESHOLD=0.85 split determines algo vs LLM clean — don't change without ADR.
  - **`_pick_body_source(text_layer, ocr_text)` (s12)** — selects the BODY text for mapping by
    directly COMPARING the candidate texts (garbage-quality on a front+mid sample + length/coverage),
    NOT the readability flag. Was a critical bug: when a READABLE doc's ToC escalated to OCR,
    build_toc returned a short ToC-only `ocr_text` that overrode the full readable text layer
    (digital-design: 25k ToC vs 1.5M body → 94 sections page_cut). Rule: garbage text layer → OCR
    (0e6e53b); clean & len≥ocr → text layer (digital-design/978/Клейнман/Кениг/ВКР); clean but shorter
    → OCR. Fixed a SYSTEMIC issue across every OCR-escalated book.
  - **Per-page watermark strip (s13)** — `footer_junk_lines(full_text, total_pages)` computed once;
    the junk lines are stripped from each section's SLICED content (not from full_text before mapping —
    that would shift page_cut positions and churn neighbours). See pdf_utils.
  - **`chars` XML attr (s9)** — each `<section>` carries `chars` = own-content length (for honest
    content audits; the `real>100` count is a metric trap).

---

### toc_builder.py
- **What:** 6-level cascade for ToC extraction. Returns `(sequence, toc_source, ocr_text)`.
- **Why it exists:** Isolated from neural parser so the cascade logic is independently testable and readable.
- **Location:** `app/services/toc_builder.py`
- **Key files:** same
- **Depends on:** toc_parser, llm_engine, ocr_engine
- **Used by:** pdf_parser_neural.py
- **Non-obvious:**
  - `_dedup_and_order` must run on ALL return paths, not just at the end. It uses title-only key
    (not title+page) — this is intentional (see ADR + insight). Do not revert to title+page key.
  - `_drop_fuzzy_pageless_dupes` (session 005) runs as a second pass inside `_dedup_and_order`:
    SequenceMatcher ≥ 0.88 on normalized titles; drops a page-less entry that fuzzy-matches an
    earlier paged entry. Guard: skip when title length < 8 chars. Targets OCR drift variants of
    the same heading (ВКР: «ИЗУЧЕНЯЯ»/«ИЗУЧЕНЯЮ» — 1 letter diff, defeated exact-norm dedup).
  - `_split_sticky_toc_lines` is applied to BOTH OCR text AND PyMuPDF raw_text. OCR glues
    multiple ToC lines into one; PyMuPDF sometimes does too on certain PDF layouts.
  - `TOC_GOOD_ENOUGH=12` — pipeline stops early only if ≥12 sections found with sub-sections.
    Raised from 8 to avoid stopping on upper-level-only ToC (like Иглмен with just 6 ЧАСТИ).
  - `_looks_incomplete` over-triggers on books with text-layer body refs in the first 20 pages
    (ВКР: ratio 2.36 yet heuristic 14 = correct ToC). Trigger is conservative-for-recall by
    design but wastes ~170s on these books. See OPEN.md "Open questions" for tuning options.

**Cascade levels:**
1. HeuristicParser on PyMuPDF raw_text (fast, first 20 pages)
2. LLM `extract_toc_json` on first pages text (if level 1 insufficient OR incomplete)
3. OCR first 30 pages + HeuristicParser (escalation if 1+2 weak/ungrounded)
4. OCR + LLM `extract_toc_json` (if OCR heuristic failed)
5. Deep-scan: LLM on full-text chunks (SLOW — only when `enable_deep_scan=True` and still ≤3)
6. LLM-expand: find chapters inside ЧАСТИ (when ToC is upper-level-only and `enable_llm_expand=True`)

**Smart fallback (session 004):** the early-return gate is widened with `_looks_incomplete` —
an ALGORITHMIC (no-model) signal: count ToC-entry-like lines in raw text vs heuristic items; if
`raw_entries > items * 1.5` the heuristic is incomplete (Розенсон 4.14, Клейнман 1.76) and we fall
through. When triggered, candidates from levels 1–4 are collected and the BEST VALIDATED one is
chosen via `toc_validate.score_toc` (`_select_best`) — never ship unvalidated LLM output. Sources
are invoked SEQUENTIALLY (GPU ≤90%). `_llm_from_text_retry` retries up to 3× on formal/CJK errors.

---

### mapping_pipeline.py
- **What:** 9-stage cascade for mapping sequence→positions in full text. Returns `mapped` list.
- **Why it exists:** Isolated from neural parser; the rescue logic is complex enough to test separately.
- **Location:** `app/services/mapping_pipeline.py`
- **Key files:** same
- **Depends on:** pdf_utils, llm_engine, embedding_engine
- **Used by:** pdf_parser_neural.py
- **Non-obvious:**
  - `_verify_and_correct_order` runs TWICE: before rescue (catches in-ToC matches) and after (catches
    out-of-order rescue results). Early verify is critical — without it, in-ToC matches stay at conf=1.0.
  - `_restored_after_rescue_fail` (session 004 Fix A): if page-distance reverted a match and rescue
    found nothing better, restore the original ONLY when the section has NO page. If it has a page,
    defer to page_cut (positional by page) — restoring a far match re-introduces a false positive
    (Do Good «Об авторе» copyright, Главы 1/3/5/7). 
  - `_revert_position_clusters` (session 004 Fix B): a run of ≥3 consecutive ToC sections whose matched
    positions are crammed in ≤`CLUSTER_SPAN`(4000) chars while page-estimates span ≥`CLUSTER_PAGE_SPAN`
    (15000) = a chapter-list/index/back-matter, not real bodies → revert to not_found so page_cut
    spreads them by page (Do Good Главы 8–12). Runs after final verify, before page_cut. Does NOT
    fire on dense legit books (parallelnoe/Массель: 0 reverts) — positions crammed BUT pages spread is
    the anomaly signal. Excludes page_cut and sections without a page.
  - `PAGE_DISTANCE_TOLERANCE_RATIO=0.30`: if exact match is >30% of text_len from expected page position,
    it's considered suspect. Calibrated for "chapter found in Index/Appendix" case. Do not raise without testing.
  - Fuzzy matching uses `difflib.SequenceMatcher` with `FUZZY_THRESHOLD=0.85`. High threshold is intentional —
    lower values cause "система А" to match "система Б" (different chapters with similar names).
  - **`current_pos` cascade discipline**: `find_real_indices` advances `current_pos` after each
    match, so a wrong early match pushes later titles past their real bodies → page_cut. The
    list-context preference in `pdf_utils._search_with_confidence` is what keeps this disciplined.
  - **page_cut = PURE linear estimate + same-page staggering (s9–s11).** Final fallback for sections
    with no found position: `start = (page-1)/total_pages * full_text_len`. Sessions 007–010 tried
    GLOBAL anchor-interpolation (interpolate page→pos between trusted matches) AND a narrow LOCAL
    backward-anchor — BOTH REVERTED. The global version got poisoned by mis-placed trusted matches
    (a title matched inside the back-matter index) and cascaded neighbours into the index
    (AI 184→169; 30 sections sharing one identical index blob). The local version gave ≈0 net and
    couldn't fix MIL-STD 5.1.2.5 (its right bound 5.1.3 is itself order-inverted). **Do NOT
    reintroduce anchor-interpolation here.** Fix B (s11): page_cut sections on the SAME page were
    getting the IDENTICAL estimate → identical slice window → byte-identical duplicate content;
    now a page's G>1 sections are staggered across the page width (single-per-page = exact linear).

**Cascade levels:**
1. `find_real_indices` — regex with 4 strategies: exact, tokenized_regex, partial_words, num_prefix
2. Early `_verify_and_correct_order` (in-ToC + page-distance check with backup/restore)
3. Page-hint search (±5000 chars from estimated page position)
4. Fuzzy match — `_fuzzy_locate` with SequenceMatcher (handles LLM ToC typos)
5. Embedding rescue — semantic search via `embedding_client.locate_section`
6. LLM rescue — `llm_client.locate_section_in_text` as last resort
7. Final `_verify_and_correct_order` (out-of-order check on rescue results)
8. `_restored_after_rescue_fail` — restore page-distance backups ONLY if no page (else defer to page_cut)
9. `_revert_position_clusters` — revert crammed back-matter title clusters → page_cut (Class-2 fix)

---

### toc_parser.py (HeuristicParser)
- **What:** Regex-based ToC parser. Reads raw text line by line, detects ToC structure patterns.
- **Why it exists:** Fast, zero-cost first attempt before any LLM/OCR.
- **Location:** `app/services/toc_parser.py`
- **Key files:** same
- **Depends on:** nothing
- **Used by:** toc_builder.py
- **Non-obvious:**
  - `_COMPLETE_SECTION_NAMES` frozenset: when a known terminal section name appears ("Благодарности",
    "Глоссарий", etc.), the rest of the line is trimmed. Prevents "Благодарности Я написал…" as title.
  - `TERMINAL_SECTION_MARKERS`: when these appear in ToC, parsing stops (Appendix, Index, etc. —
    everything after is reference material, not chapters). This prevents 250+ items for parallelnoe.

---

### toc_validator.py
- **What:** Validates extracted ToC via OCR of ±5 pages around the ToC page. Returns coverage_pct.
- **Why it exists:** Catch cases where heuristic/LLM extracted wrong titles from non-ToC pages.
- **Location:** `app/services/toc_validator.py`
- **Key files:** same
- **Depends on:** ocr_engine
- **Used by:** pdf_parser_neural.py (optional, skipped when ToC was OCR-derived)
- **Non-obvious:**
  - Coverage metric is NOISY. When heuristic finds ToC perfectly (Клейнман: 40/40), OCR of ToC pages
    often returns reformatted text that doesn't match heuristic's normalized titles → coverage=0.05.
  - Treat result as informational only. Do not gate pipeline on coverage_pct.
  - SKIPPED when `toc_source.startswith('ocr')` — would be self-comparison.

---

### toc_validate.py (NEW — session 004)
- **What:** Validates/scores ToC candidates so the smart fallback can pick the best one and reject
  LLM hallucinations. `score_toc`, `is_valid`, `check_formal`, `check_cjk`.
- **Why it exists:** LLM ToC extraction is powerful but hallucinates / makes formal errors. This is
  the quality gate (user requirement: quality > speed) that lets us trust LLM output.
- **Location:** `app/services/toc_validate.py`
- **Depends on:** `llm_engine.has_foreign_script` (CJK), `embedding_engine` (semantic grounding)
- **Used by:** toc_builder (`_select_best`)
- **Non-obvious:**
  - `is_valid` = formal_ok AND no CJK AND grounding ≥ 0.8 (`GROUNDING_MIN`).
  - **grounding** = fraction of titles that actually occur in the book text. Lexical-first
    (significant tokens present), embedder ONLY for titles that fail lexical, capped at
    `MAX_EMBED_CHECKS=20` (GPU cost). Catches hallucinated titles.
  - `check_formal` allows ONE page "reset" (dual brief+detailed ToC) before flagging non-monotonic.
  - Levels are clamped to 1..3 upstream in `toc_builder._normalize_llm_items` — do NOT also reject
    level>3 here (a perfect 77-item Розенсон ToC was once falsely invalidated by a stray level 4).

---

### pdf_utils.py
- **What:** Text search, confidence scoring, readability check, content clean (fast_clean_chunk).
- **Why it exists:** Shared utilities used by both mapping_pipeline and toc_builder.
- **Location:** `app/services/pdf_utils.py`
- **Key files:** same
- **Depends on:** toc_parser (for `get_clean_title`)
- **Used by:** mapping_pipeline, toc_builder, pdf_parser_neural
- **Non-obvious:**
  - `exact_normalized` strategy: tokenized_regex match where normalized match == normalized title
    → promoted to confidence=1.0. Added to page-distance whitelist. This catches whitespace-only diffs.
  - `fast_clean_chunk` does NOT remove CJK. `scrub_foreign_script` in pdf_parser_neural.py handles that.
  - `check_document_readability` uses garbage_ratio + char diversity. Threshold tuned empirically.
    NB: it samples ≤10 pages and SKIPS pages with <50 chars — so it can mis-judge a doc whose body
    is sparse; the body-source decision uses `_pick_body_source` (compares actual texts), not just
    this flag.
  - **Footer/watermark removal (s13)**: `footer_junk_lines(text, total_pages)` returns junk lines —
    (a) long running headers (len>20, repeat>4) and (b) short per-page watermarks (len≥5 repeating on
    > `HEADER_WATERMARK_PAGE_FRACTION=0.60` of pages, e.g. «Библиотека БГУИР» 100%, «MIL-STD-1472G»
    100%). `strip_junk_lines(text, junk)` removes them. Threshold is HIGH (0.60) on purpose: repeated
    CONTENT (parallelnoe code labels «Объявление» 39%, ВКР district names 31%) sits ≤39% and must NOT
    be deleted; the 39%↔82% gap is clean. Match is exact stripped LINES, not substrings.
    `clean_footer_header` = compose both (used by fast parser on full_text; neural parser strips junk
    from per-section content instead, to keep page_cut positions stable).
  - **List-context preference (session 004 end, commit c81fcf0)**: `_search_with_confidence`
    prefers the first occurrence whose FOLLOWING text is prose, not a list. Helpers:
    `_is_list_context` (detects leading bullet `•·…`, leader-dots, or another bullet within
    ~50 chars after) + `_find_first_nonlist` / `_first_nonlist_match`. Applied to the `exact`
    and `tokenized` strategies. Fallback: first occurrence if ALL are list-like — no behaviour
    change for clean books. Fixed Розенсон/Кениг subsection content («•»/dots → prose) AND
    Клейнман regression (wrong-occurrence cascade pushed current_pos forward → later titles
    fell to page_cut). **Do NOT revert this preference** — it's the bedrock of subsection
    mapping quality.

---

### llm_engine.py
- **What:** LLM client wrapping LM Studio API. All LLM calls go through here.
- **Why it exists:** Single point for LLM communication, prompt management, retry logic.
- **Location:** `app/services/llm_engine.py`
- **Key files:** same
- **Depends on:** LM Studio HTTP API at 127.0.0.1:1234
- **Used by:** toc_builder, mapping_pipeline, pdf_parser_neural
- **Non-obvious:**
  - `scrub_foreign_script(text)` is a PUBLIC function exported from this module — used by
    pdf_parser_neural.py. Do not remove.
  - `_extract_message_content`: some LLM reasoning models (Qwen3 family) return empty `content`
    with reasoning in `reasoning_content` or `model_extra.reasoning_content`. This fallback handles that.
  - `process_large_text` uses `asyncio.gather` — LLM clean IS PARALLEL. Do not claim it's sequential.
  - `LLM_BOUNDARY_CONTEXT = 3000` — only first 3000 chars sent for boundary detection (LLM only needs start).
  - CJK-aware retry: if input text has CJK → prepend explanation prompt → if output still has CJK → scrub.
  - `extract_toc_json` uses `_parse_toc_items` (session 004): robust JSON salvage — extracts individual
    `{...}` objects even from TRUNCATED/malformed output (large ToC overruns max_tokens). Returns the
    items parsed so far instead of failing the whole `json.loads`. `TOC_MAX_TOKENS=3000` (ctx 8192).
    Has optional `extra_instruction` for retry-with-feedback.

---

### ocr_engine.py
- **What:** OCR client using glm-ocr via LM Studio vision API.
- **Why it exists:** Handles scanned/damaged PDFs that PyMuPDF can't read.
- **Location:** `app/services/ocr_engine.py`
- **Key files:** same
- **Depends on:** LM Studio HTTP API (glm-ocr model)
- **Used by:** toc_builder, toc_validator
- **Non-obvious:**
  - `_safe_print()`: ANY print of OCR output must use this. U+FFFD (OCR replacement char) crashes
    Windows cp1251 console with UnicodeEncodeError. This caused silent full-pipeline failure on 0e6e53b.
  - `OCR_PAGE_TIMEOUT_SEC = 60`: per-page timeout. glm-ocr can hang on complex pages.
  - Empty page skip: pages with 0 chars AND 0 images are skipped entirely (not 0 chars alone —
    some image-only pages have 0 extracted chars but do have images → must OCR those).
  - **400 «Failed to parse input» recovery (session 005)**: glm-ocr sometimes returns HTTP 400
    on pages whose OCR'd text is otherwise fine — the text is EMBEDDED in the error body
    (`body['error']` or `str(exc)`). `_extract_text_from_ocr_error` pulls it out via regex
    `Failed to parse input at pos N:\\n<text>`. Fallback: `page.get_text()` PyMuPDF text-layer.
    Never silently drop pages. Verified on 0e6e53b — recovered 4 pages (25/120/137/174, 1757 chars).

---

### embedding_engine.py
- **What:** Embedding client for semantic section search.
- **Why it exists:** Rescue tier between fuzzy (cheap, handles typos) and LLM (expensive, accurate).
- **Location:** `app/services/embedding_engine.py`
- **Depends on:** LM Studio HTTP API (text-embedding-qwen3-embedding-0.6b model)
- **Used by:** mapping_pipeline
- **Non-obvious:**
  - Model is `text-embedding-qwen3-embedding-0.6b` — NOT nomic-embed. If LM Studio loads a different
    embedding model the results will be wrong but no error will surface.

---

### api.py
- **What:** FastAPI app with POST /upload and WebSocket /ws/analyze.
- **Location:** `app/api.py`
- **Depends on:** pdf_parser_neural, pdf_parser_fast, docx_parser, txt_parser, xml_builder
- **Non-obvious:**
  - WebSocket double-close: check `websocket.application_state == WebSocketState.CONNECTED` before
    send AND in finally block. Client disconnect causes RuntimeError if not checked.
  - Default flag values in WebSocket message parsing must match pdf_parser_neural.py defaults.
  - `validate_toc_ocr` is parsed from WS message with default=True.

---

### xml_builder.py
- **What:** Builds final XML from `final_nodes` + `sequence` + stats.
- **Location:** `app/services/xml_builder.py`
- **Non-obvious:**
  - `NavigationTable` is built from `sequence` (ToC items), not `final_nodes` (mapped sections).
  - Each `<section>` has `title`, `page`, `confidence`, and `chars` (own-content length, s9) attributes.

---

## Data flow (primary use case: neural PDF)

1. `POST /upload` → saves to temp file, returns `temp_id`
2. `WS /ws/analyze` receives `{temp_id, deep_scan, use_ocr, llm_expand, validate_toc_ocr}`
3. `parse_pdf_neural(file_path, ...flags...)` called
4. `check_document_readability(doc)` → if unreadable AND use_ocr → `ocr_client.ocr_document()`
5. `build_toc(doc, ...)` → returns `(sequence, toc_source, ocr_text)`
   - Heuristic first; if complete (≥12, not high-level-only, not `_looks_incomplete`) → return it.
   - Else smart fallback: collect candidates (heuristic/LLM/OCR+LLM, sequential) → `_select_best`
     picks the best VALIDATED (formal+CJK+grounding≥0.8) → else keep heuristic. Then deep_scan/expand.
6. Optional: `validate_toc_via_ocr(doc, sequence)` → `meta['toc_validation']`
7. **Body text** = `_pick_body_source(get_all_text(doc), ocr_text)` (s12) — compares text-layer vs
   OCR by quality+coverage; THEN `clean_footer_header(full_text)` removes long running headers;
   `footer_junk = footer_junk_lines(full_text, total_pages)` collects per-page watermarks for later.
8. `map_sequence(sequence, full_text, total_pages)` → `mapped` list with start/end positions
   (page_cut = pure linear estimate + same-page staggering)
9. For each mapped section: slice `full_text[end_idx : next_start_idx]`, then
   `strip_junk_lines(chunk, footer_junk)` (watermark out of CONTENT only — positions stay stable)
   - confidence ≥ 0.85 (or page_cut) → `fast_clean_chunk(raw_chunk)`
   - confidence < 0.85 → `fix_chapter_boundary()` + `process_large_text()`
   - Always: `scrub_foreign_script(title)` + `scrub_foreign_script(content)`
10. `build_tree_structure(final_nodes)` + `dict_to_xml(tree)` → XML string
11. WebSocket sends `{type: "complete", xml: ..., stats: ...}`

## Cross-cutting patterns
- **scrub_foreign_script always last**: applied to title+content of every node regardless of path
- **No LLM in toc_parser.py or pdf_utils.py**: these are pure-algo utilities
- **Cascade always returns (sequence, source, ocr_text)**: never raise from a cascade — always return partial results
- **progress_callback is always optional**: all cascade functions accept `progress_cb=None`
- **_safe_print everywhere OCR output goes**: never `print()` raw OCR/LLM output without encoding safety
- **Never ship unvalidated LLM ToC**: any LLM/OCR-derived ToC candidate must pass `toc_validate.is_valid`
  (formal + no CJK + grounding≥0.8) before being preferred over the heuristic. Selection by grounded count.
- **Models run sequentially**: LLM + OCR + embedder must not be resident simultaneously (GPU ≤90%).
- **Prefer prose occurrence over list occurrence** (s4 c81fcf0): regex search in `pdf_utils._search_with_confidence`
  uses `_find_first_nonlist`; the first occurrence in a bulleted/dotted ToC-like context is skipped.
- **Never silently drop OCR pages on 400** (s5): `ocr_engine` catches `BadRequestError`, recovers
  embedded text from body, falls back to `page.get_text()`. Every page must produce *something* or an
  explicit logged failure.

## Tech stack
| Layer | Tech | Version | Why chosen |
|-------|------|---------|-----------|
| Web framework | FastAPI | latest | async-native, WebSocket built-in |
| PDF parsing | PyMuPDF (fitz) | 1.26.7 | Best text extraction + page image rendering |
| LLM/OCR/Embed | LM Studio | local | Privacy, cost, offline capability |
| LLM model | qwen2.5-7b-instruct | — | Good Russian support, fits 8GB VRAM |
| OCR model | glm-ocr | — | Best Russian OCR among available vision models |
| Embedding | text-embedding-qwen3-0.6b | — | Compact, fast, multilingual |
| Fuzzy match | difflib (stdlib) | — | No extra deps, sufficient for 1-2 char typos |
| Tests | pytest | — | Standard |

## External services
| Service | Purpose | Auth | Gotchas |
|---------|---------|------|---------|
| LM Studio :1234 | LLM + OCR + Embed | None (local) | Must be started with `lms load qwen2.5-7b-instruct -c 8192`; default ctx=4096 causes overflow. GPU max 90% or display disappears. Load models one at a time. |

## What NOT to do (learned the hard way)
- **Don't print raw LLM/OCR output directly**: U+FFFD crashes Windows cp1251 — use `_safe_print()` → session 001
- **Don't move scrub_foreign_script into fast_clean_chunk**: must stay in pdf_parser_neural.py post-loop → session 001
- **Don't revert dedup key to title+page**: causes duplicate sections when heuristic+LLM both find same chapter → session 001
- **Don't claim LLM clean is sequential**: `process_large_text` uses `asyncio.gather` → session 001
- **Don't disable _looks_like_toc_content check**: Release It! had 21/36 sections "found" inside ToC pages
- **Don't raise FUZZY_THRESHOLD below 0.85**: causes "система А" to match "система Б" (confirmed in Иглмен experiments)
- **Don't load all models at once**: GPU reaches 100%, user's display disappears → session 001
- **Don't take the first occurrence blindly in search** (s4): list-context check exists for a reason. Reverting it
  breaks Розенсон/Кениг subsection bodies and causes wrong-occurrence cascades that send later titles to page_cut.
- **Don't trust scripts/mapping_audit.py as production reality** (s5): it bypasses parts of the pipeline.
  Klein audit script claimed 0 page_cut, real `parse_pdf_neural` gives 36. Use `run_corpus.py` for truth.
- **Don't combine bash `&` with tool `run_in_background=true`** (s5): spawns TWO Python processes
  (one venv, one system 3.11) — same family as the historic two-uvicorn landmine. Pick ONE backgrounding mechanism.
- **Don't revert ToC level clamp to 1..3** in `_normalize_llm_items` (s4): a stray level-4 falsely
  invalidated a perfect 77-item Розенсон ToC.

## Known technical debt
_Resolved since s005: «LLM strips hierarchical numbering» (s6 prompt + reattach); «heuristic loses
chapter title» (s6 merge_wrap); «MIL-STD 5.1.2.5» (s12: root = mis-placed neighbour 5.1.3, accepted);
«Клейнман 36 page_cut» (s12: was the body-source bug, now 40/40 exact)._

- **`_looks_incomplete` over-triggers** (s5): on books where first 20 pages contain body refs to page
  numbers, raw_entries over-counts. ВКР: ratio 2.36 but heuristic 14 = correct. ~170s wasted.
- **12_100229 remaining junk (s13, OPEN groups A–D):** page_cut into front-matter ToC leader-dots
  (~10 early sections) + ToC-block absorption (M1/M3); running-header+underscore-fill noise
  («…микросхемы____», «Гпава 4____») and short «Глава N» headers (frequency won't catch — need
  normalize/pattern); spurious glossary/index terms extracted as sections; titles absent from body
  (only in ToC) → forced page_cut.
- **digital-design residual (s12):** 46 page_cut — OCR-extracted ToC titles don't string-match the
  text-layer body (1 truly lost). Candidate: fuzzy/OCR-aware match now that the body is searchable.
- **978-5-7996 LLM ToC nondeterminism**: section count swings run-to-run; retry didn't fix.
- **`_restored_after_rescue_fail` can restore false positives** (s4): Do Good «Об авторе» copyright.
- **MIL-STD 5.1.2.5 bloat (61677) ACCEPTED**: root is order-inverted neighbour 5.1.3, not page_cut.
- **toc_validation metric is noisy**; **no LM Studio ctx<8192 guard**.
- **`real>100` count is a metric TRAP**: page_cut into front-matter / watermark padding / duplicates
  all count as "real". Judge by `match_strategy` mix + content correctness, not the count. `chars`
  attr (s9) and content inspection are the honest signals.

## Change History
_Append only. Never delete entries._

| Date | Session | What changed | Why |
|------|---------|--------------|-----|
| 2026-05-28 | 001 | Initial architecture.md | First .project-brain setup |
| 2026-05-28 | 001 | Added toc_validator.py component | New OCR validation module |
| 2026-05-28 | 001 | scrub_foreign_script in pdf_parser_neural post-loop | CJK contamination through fast_clean path |
| 2026-05-28 | 001 | exact_normalized confidence=1.0 in pdf_utils | Whitespace-only matches were downgraded to 0.85 |
| 2026-05-28 | 001 | _split_sticky_toc_lines in toc_builder | OCR and PyMuPDF merge ToC lines (Иглмен case) |
| 2026-05-28 | 001 | All flags ON by default | User preference: full capability always |
| 2026-05-28 | 001 | _safe_print in ocr_engine + stdout.reconfigure in main | Windows cp1251 crash on U+FFFD |
| 2026-05-29 | 004 | toc_parser: don't break on page-less «Часть/Part/Раздел»; length-guard 250 | Mechanism A — dual-ToC dropped subsections; catastrophic regex hang |
| 2026-05-29 | 004 | NEW toc_validate.py (score/validate ToC candidates) | Trust-but-verify LLM ToC: CJK+formal+grounding |
| 2026-05-29 | 004 | toc_builder: `_looks_incomplete` trigger + smart fallback `_select_best` | Heuristic misses subsections on messy layouts (Розенсон/Клейнман) |
| 2026-05-29 | 004 | llm_engine: `_parse_toc_items` salvage + TOC_MAX_TOKENS + extra_instruction | LLM ToC JSON truncated/malformed; retry-with-feedback |
| 2026-05-29 | 004 | clamp LLM ToC level to 1..3 in _normalize_llm_items | level 4 falsely invalidated a perfect ToC |
| 2026-05-29 | 004 | mapping Fix A: defer far page-distance reverts to page_cut | restore re-introduced false positives (Do Good copyright + ch1/3/5/7) |
| 2026-05-29 | 004 | mapping Fix B: _revert_position_clusters (Class-2) | exact titles cluster in back-matter; bodies absorbed by neighbour (Do Good ch8-12) |
| 2026-05-29 | 004 | pdf_utils: list-context preference (`_is_list_context` / `_find_first_nonlist`) in `_search_with_confidence` | wrong-occurrence-in-bulleted-summary matched bodies, cascaded current_pos. Fixed Розенсон/Кениг subsections, Клейнман regression |
| 2026-05-29 | 004 | toc_builder: `_llm_from_text_retry` retries when items << raw entry count | LLM non-deterministic: Клейнман returned 57 some runs, 7 others; short runs let incomplete heuristic win |
| 2026-05-30 | 005 | toc_builder: `_drop_fuzzy_pageless_dupes` second pass in `_dedup_and_order` | ВКР: OCR drift («ИЗУЧЕНЯЯ»/«ИЗУЧЕНЯЮ») defeats exact-norm dedup; fuzzy ≥ 0.88 catches it |
| 2026-05-30 | 005 | ocr_engine: `_extract_text_from_ocr_error` + `BadRequestError` branch | glm-ocr 400 «Failed to parse input» bodies contain the OCR'd text; recover instead of silent drop. 0e6e53b: 4 pages, 1757 chars recovered |
| 2026-05-30 | 005 | .gitignore: exclude `test_v*/`, `tests_v*/`, `.audit_pages/`, `.claude/skills/` | Large baseline XML dirs and local Claude state shouldn't be tracked |
| 2026-05-30 | 006 | LLM ToC numbering preserved (prompt + `_reattach_numerical_prefixes`); `merge_wrap_continuations`; `effective_real_sections` metric | LLM stripped «1.2.1»; heuristic split multi-line titles; hierarchical-shell honesty |
| 2026-06-10 | 007–008 | Anchor-interpolation page_cut + ordering guard ADDED then found harmful | MIL-STD 5.1.2.5 bloat fix; but poisoned AI back-matter (cascade into index) |
| 2026-06-27 | 009 | XML `chars` attr; REVERT anchor-interpolation → pure v6 linear page_cut | Content inspection (not count) showed anchor-interp net-negative; AI 169→184 |
| 2026-06-27 | 011 | page_cut: stagger same-page sections (fix B) | Same-page page_cut got identical slice window → byte-identical duplicate content; dups 50→4 |
| 2026-06-27 | 012 | `_pick_body_source` — pick body text by comparing candidates, not readability flag | ToC-OCR overrode full readable body on OCR-escalated docs; digital-design 24→97, systemic |
| 2026-06-28 | 013 | `footer_junk_lines`/`strip_junk_lines`; per-page watermark strip from CONTENT (decoupled); 0.60 page-fraction | «Библиотека БГУИР» (787×) polluted content; full_text-strip churned page_cut; 0.30 deleted real content |
