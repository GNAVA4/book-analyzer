import os

# Ниже этого порога чанк идёт в LLM cleanup (полная обработка текста)
CONFIDENCE_THRESHOLD = float(os.getenv("CONFIDENCE_THRESHOLD", "0.7"))

# Ниже этого порога чанку нужен только _llm_fix_boundary (без полного текста)
LLM_REFINEMENT_THRESHOLD = float(os.getenv("LLM_REFINEMENT_THRESHOLD", "0.5"))

# Ниже этого порога — ToC считается ненадёжным, нужен fallback
TOC_CONFIDENCE_FALLBACK_THRESHOLD = float(os.getenv("TOC_CONFIDENCE_FALLBACK_THRESHOLD", "0.6"))

# Минимальный порог для стратегии 3 (partial_words) — ниже отправлять в LLM для верификации
PARTIAL_WORDS_MIN_CONFIDENCE = float(os.getenv("PARTIAL_WORDS_MIN_CONFIDENCE", "0.5"))

# Для не найденных глав: LLM ищет в ± этом количестве страниц
MISSING_CHAPTER_CONTEXT_PAGES = int(os.getenv("MISSING_CHAPTER_CONTEXT_PAGES", "3"))
