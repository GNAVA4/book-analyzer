"""
OCR-валидация ToC. Идея: после того как pipeline извлёк sequence (через
heuristic/LLM из извлечённого PyMuPDF-текста), мы OCR-им несколько страниц
ВОКРУГ места где находится оглавление и сравниваем что нашли.

Если PyMuPDF вытащил ToC битой кодировкой (или половиной заголовков) —
OCR покажет настоящие тайтлы. Метрики уходят в meta.toc_validation,
пользователь видит coverage % и список «потерянных» строк.

Стоимость: ~10 страниц OCR ≈ 1 минута. Поэтому опционально (флаг).
"""

import re

from .ocr_engine import ocr_client


# Маркеры начала ToC — ищем их в первых 30 страницах
_TOC_MARKERS = (
    'содержание', 'оглавление', 'contents', 'table of contents',
    'inhaltsverzeichnis', 'indice', 'sommaire',
)

# Размер окна для OCR (страниц до и после найденной ToC-страницы)
TOC_OCR_WINDOW_BEFORE = 2
TOC_OCR_WINDOW_AFTER = 8

# Минимальная доля значимых слов title'а в OCR — чтобы считать title найденным
TOC_COVERAGE_WORD_THRESHOLD = 0.7
# Минимальная длина значимого слова (3+ символа отсекает предлоги)
TOC_MIN_WORD_LEN = 3
# Сколько первых значимых слов title'а сравниваем
TOC_TITLE_WORDS_TO_CHECK = 5

# Кандидат на «потерянный пункт ToC» — длина 15-150 символов и кончается номером
_TOC_LINE_CANDIDATE = re.compile(r'.{15,150}\s\d{1,4}\s*$', re.DOTALL)


def _find_toc_page(doc, max_scan_pages: int = 30) -> int | None:
    """Возвращает индекс страницы где, скорее всего, начинается ToC.
    None если не нашли явного маркера."""
    n = min(max_scan_pages, len(doc))
    for i in range(n):
        text = doc[i].get_text().lower()
        # Маркер должен быть на отдельной короткой строке (не вкраплён в прозу)
        for line in text.split('\n'):
            stripped = line.strip()
            if 5 <= len(stripped) <= 50 and any(m in stripped for m in _TOC_MARKERS):
                return i
    return None


def _normalize(s: str) -> str:
    return re.sub(r'\s+', ' ', (s or '')).lower().strip()


def _significant_words(title: str) -> list[str]:
    """Извлекает значимые слова из заголовка (без префиксов Глава/Часть/N.N)."""
    clean = re.sub(
        r'^(Глава|Chapter|Часть|Раздел|§|Введение|Предисловие|Заключение|'
        r'[IVXLCDM]+\.|[0-9]+(?:\.[0-9]+)*\.?)\s*',
        '', title or '', flags=re.IGNORECASE
    )
    norm = _normalize(clean)
    words = [w for w in norm.split() if len(w) >= TOC_MIN_WORD_LEN]
    return words[:TOC_TITLE_WORDS_TO_CHECK]


async def validate_toc_via_ocr(
    doc,
    sequence: list,
    progress_cb=None,
) -> dict:
    """
    OCR-валидация sequence по ±N страницам от найденного ToC.

    Returns dict:
        coverage_pct:    float 0-1 — доля title'ов из sequence чьи слова нашлись в OCR
        toc_page:        int | None — на какой странице PDF расположено ToC
        ocr_pages:       tuple[int, int] — диапазон страниц что OCR'или (start, end)
        missing_titles:  list[str] — title'ы которые в OCR не нашлись
        extra_lines:     list[str] — кандидаты на потерянные пункты ToC из OCR

    Возвращает {"skipped": "reason"} если OCR недоступен или ToC не найден.
    """
    if not sequence:
        return {"skipped": "empty_sequence"}
    if not await ocr_client.is_available():
        return {"skipped": "ocr_unavailable"}

    toc_page = _find_toc_page(doc)
    if toc_page is None:
        return {"skipped": "toc_marker_not_found"}

    start = max(0, toc_page - TOC_OCR_WINDOW_BEFORE)
    end = min(len(doc), toc_page + TOC_OCR_WINDOW_AFTER + 1)
    total = end - start

    if progress_cb:
        await progress_cb(
            8,
            f"OCR-валидация ToC: страницы {start+1}-{end} ({total} стр.)..."
        )

    # OCR диапазона
    pieces: list[str] = []
    for idx, page_i in enumerate(range(start, end)):
        try:
            pix = doc[page_i].get_pixmap(dpi=150)
            text = await ocr_client.ocr_page_image(pix.tobytes("png"))
            pieces.append(text or "")
        except Exception:
            pieces.append("")
        if progress_cb:
            sub_pct = 8 + int((idx + 1) / max(total, 1) * 4)  # 8 → 12
            await progress_cb(sub_pct, f"OCR-валидация {idx+1}/{total}...")

    ocr_text = "\n".join(pieces)
    if len(ocr_text.strip()) < 50:
        return {
            "skipped": "ocr_too_short",
            "toc_page": toc_page,
            "ocr_pages": (start, end),
        }

    ocr_norm = _normalize(ocr_text)

    # Coverage: для каждого title считаем долю значимых слов в OCR
    found_count = 0
    missing: list[str] = []
    for item in sequence:
        words = _significant_words(item.get('title', ''))
        if not words:
            # Title без значимых слов — пропускаем, считаем найденным
            found_count += 1
            continue
        present = sum(1 for w in words if w in ocr_norm)
        if present / len(words) >= TOC_COVERAGE_WORD_THRESHOLD:
            found_count += 1
        else:
            missing.append(item.get('title', ''))

    coverage = found_count / len(sequence) if sequence else 1.0

    # Поиск кандидатов на потерянные пункты ToC
    seq_norms = [_normalize(s.get('title', '')) for s in sequence]
    extra: list[str] = []
    for raw_line in ocr_text.split('\n'):
        line = raw_line.strip()
        if not (15 <= len(line) <= 150):
            continue
        if not _TOC_LINE_CANDIDATE.match(line):
            continue
        line_norm = _normalize(line)
        # Уже в sequence (любая подстрока в любую сторону)?
        if any(line_norm in t or (t and t in line_norm) for t in seq_norms):
            continue
        # Похожа ли на отдельный пункт (отбрасываем явно непохожие)
        # ToC-пункт: заголовок + (точки/пробелы) + число
        if re.match(r'^\s*\d{1,4}\s+\d', line):
            # «5 12» — два числа подряд, скорее всего хвост таблицы
            continue
        extra.append(line)

    return {
        "coverage_pct": round(coverage, 2),
        "toc_page": toc_page,
        "ocr_pages": (start, end),
        "missing_titles": missing[:20],
        "extra_lines": extra[:20],
        "ocr_chars": len(ocr_text),
    }
