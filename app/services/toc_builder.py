"""
Многоуровневый pipeline для извлечения оглавления.

Идея: каждый уровень — отдельная функция, возвращающая sequence и quality score.
Builder пробует уровни по очереди, останавливается на первом результате
с достаточным quality, либо собирает лучший из всех попыток.

Уровни (от дешёвого к дорогому):
    1. Эвристика (HeuristicParser)         — мгновенно, работает на 70% книг
    2. LLM extract_toc_json (первые страницы) — секунды, для книг с битой вёрсткой
    3. OCR + эвристика                       — минуты, для книг где ToC = картинка
    4. OCR + LLM                              — минуты, для нечитаемых документов
    5. Deep scan (LLM по chunks)              — десятки минут, последний шанс
    6. LLM expansion: добавить главы внутрь высокоуровневых частей
"""

from typing import Callable, Awaitable

from .toc_parser import HeuristicParser, toc_to_linear_sequence
from .llm_engine import llm_client
from .ocr_engine import ocr_client


# --- Пороги качества ---------------------------------------------------------

# Достаточное число секций чтобы считать ToC «качественным» (не использовать
# более дорогие уровни).
TOC_GOOD_ENOUGH = 8

# Минимум секций, ниже которого ToC считается «недостаточным» и срабатывает
# LLM-fallback (даже если эвристика что-то нашла).
TOC_MIN_USEFUL = 3

# Если ToC выглядит «верхнеуровневым» — все секции level 1 и их мало —
# попробовать LLM-expansion для поиска глав внутри частей.
TOC_HIGH_LEVEL_THRESHOLD = 6

# Контекст-лимиты под 4096-token модели LM Studio. См. llm_engine.LLM_*.
TOC_LLM_MAX_CHARS = 6_000
TOC_LLM_PAGES = 15
OCR_TOC_PAGES = 30  # сколько первых страниц OCR-ить для поиска ToC


# --- Уровни ------------------------------------------------------------------

def _heuristic(text: str) -> list:
    """Уровень 1: чисто алгоритмический парс."""
    parser = HeuristicParser()
    return toc_to_linear_sequence(parser.parse_toc(text))


async def _llm_from_text(text: str) -> list:
    """Уровень 2: LLM extract_toc_json от первых страниц."""
    items = await llm_client.extract_toc_json(text[:TOC_LLM_MAX_CHARS])
    return _normalize_llm_items(items)


async def _ocr_then_heuristic(doc, progress_cb=None) -> tuple[list, str]:
    """Уровень 3: OCR первых страниц + эвристика."""
    if not await ocr_client.is_available():
        return [], ""
    if progress_cb:
        await progress_cb(6, f"OCR первых {OCR_TOC_PAGES} стр. для поиска ToC...")
    ocr_text = await ocr_client.ocr_document(doc, progress_callback=progress_cb, max_pages=OCR_TOC_PAGES)
    return _heuristic(ocr_text), ocr_text


async def _ocr_then_llm(ocr_text: str) -> list:
    """Уровень 4: эвристика по OCR-тексту провалилась — пробуем LLM."""
    if not ocr_text:
        return []
    items = await llm_client.extract_toc_json(ocr_text[:TOC_LLM_MAX_CHARS])
    return _normalize_llm_items(items)


async def _deep_scan(full_text: str, progress_cb=None) -> list:
    """Уровень 5: LLM проходит по chunks полного текста и предлагает структуру."""
    if progress_cb:
        await progress_cb(8, "Deep-scan: LLM анализирует полный текст...")
    items = await llm_client.propose_structure_from_text(full_text)
    return _normalize_llm_items(items)


async def _llm_expand_parts(sequence: list, full_text: str, progress_cb=None) -> list:
    """
    Уровень 6: если ToC слишком верхнеуровневый (только ЧАСТЬ I/II/III),
    просим LLM найти главы ВНУТРИ каждой части по тексту книги.

    Возвращает расширенный sequence, либо оригинал если расширение не удалось.
    """
    if not sequence or not full_text:
        return sequence

    # Берём окна между соседними секциями верхнего уровня
    expanded = []
    for i, item in enumerate(sequence):
        expanded.append(item)
        if item.get('level', 1) > 1:
            continue
        # Окно от страницы текущей секции до следующей секции верхнего уровня
        # (либо до конца текста). Эвристически переводим страницы в позиции —
        # пропорционально по объёму full_text.
        # Если страниц нет — этот шаг просто пропускаем.
        if item.get('page') is None:
            continue
        next_top = next(
            (sequence[j] for j in range(i + 1, len(sequence)) if sequence[j].get('level', 1) == 1),
            None
        )
        # Очень грубая аппроксимация позиции в тексте по странице.
        # Точнее это сделать сложно без реальных границ страниц.
        # Здесь нам важна качественная картина — поэтому берём кусок ~30K символов.
        try:
            # Возьмём ~20K char окно начиная от примерной позиции
            est_chars_per_page = len(full_text) / max(_max_page(sequence), 1)
            start_pos = int((item['page'] - 1) * est_chars_per_page)
            end_pos = int(next_top['page'] * est_chars_per_page) if next_top and next_top.get('page') else start_pos + 30_000
            window = full_text[start_pos:end_pos][:30_000]
        except Exception:
            continue

        if len(window) < 1000:
            continue

        if progress_cb:
            await progress_cb(10, f"LLM ищет главы в «{item['title'][:30]}»...")

        try:
            sub_items = await llm_client.propose_structure_from_text(window, chunk_size=5_000)
        except Exception:
            sub_items = []

        # Добавляем найденные подпункты как level 2 (потомки текущей части)
        for sub in sub_items:
            sub_title = sub.get('title', '').strip()
            if sub_title and sub_title.lower() != item['title'].lower():
                expanded.append({
                    'title': sub_title,
                    'level': max(item.get('level', 1) + 1, 2),
                    'page': None,
                })

    return expanded if len(expanded) > len(sequence) else sequence


# --- Helpers -----------------------------------------------------------------

def _normalize_llm_items(items: list) -> list:
    """Приводит LLM-возвращённые items к стандартному формату sequence."""
    out = []
    for it in items:
        title = str(it.get("title", "")).strip()
        if not title:
            continue
        level = it.get("level", 1)
        try:
            level = int(level) if level else 1
        except (TypeError, ValueError):
            level = 1
        page_raw = it.get("page")
        page = int(page_raw) if str(page_raw).isdigit() else None
        out.append({"title": title, "level": level, "page": page})
    return out


def _max_page(sequence: list) -> int:
    pages = [s.get('page') for s in sequence if isinstance(s.get('page'), int)]
    return max(pages) if pages else 1


def _is_high_level_only(sequence: list) -> bool:
    """True если sequence содержит только верхнеуровневые пункты — мало уровней 2/3."""
    if len(sequence) > TOC_HIGH_LEVEL_THRESHOLD:
        return False
    deeper = sum(1 for s in sequence if s.get('level', 1) > 1)
    return deeper == 0


# --- Главный pipeline --------------------------------------------------------

async def build_toc(
    doc,
    full_text_extractor: Callable[[], str],
    ocr_text: str | None = None,
    progress_cb: Callable[[int, str], Awaitable[None]] | None = None,
    enable_ocr: bool = True,
    enable_deep_scan: bool = False,
    enable_llm_expand: bool = True,
) -> tuple[list, str, str | None]:
    """
    Главный многоуровневый pipeline извлечения ToC.

    Возвращает (sequence, source, ocr_text):
        sequence  — список секций с {title, level, page}
        source    — какой уровень дал результат
        ocr_text  — полный OCR-текст, если OCR использовался (для повторного
                    использования в маппинге)
    """
    # Если OCR уже сделан выше по pipeline (например, документ unreadable) —
    # сразу работаем с OCR-текстом.
    if ocr_text:
        seq = _heuristic(ocr_text[:50_000])
        if len(seq) > TOC_MIN_USEFUL:
            return seq, "ocr_heuristic", ocr_text
        seq = await _llm_from_text(ocr_text)
        if seq:
            return seq, "ocr_llm", ocr_text
        # OCR есть, но никто ничего не нашёл — оставляем пустое
        return [], "none", ocr_text

    # --- Уровень 1: эвристика по обычно извлечённому тексту ---
    if progress_cb:
        await progress_cb(5, "ToC: эвристика...")
    raw_text = ""
    for i in range(min(20, len(doc))):
        raw_text += doc[i].get_text() + "\n"
    seq = _heuristic(raw_text)

    if len(seq) >= TOC_GOOD_ENOUGH and not _is_high_level_only(seq):
        # Качественный многоуровневый ToC — можно использовать как есть
        return seq, "heuristic", None

    # --- Уровень 2: дополняем LLM-ом из первых страниц ---
    if progress_cb:
        await progress_cb(6, "ToC: LLM extract_toc_json...")
    llm_seq = await _llm_from_text(raw_text)
    if len(llm_seq) > len(seq):
        seq = llm_seq
        source = "llm"
    else:
        source = "heuristic"

    if len(seq) >= TOC_GOOD_ENOUGH and not _is_high_level_only(seq):
        # LLM-expansion для верхнеуровневого ToC отрабатывается отдельно ниже
        return seq, source, None

    # --- Уровень 3+4: OCR (картиночный ToC даже в «читаемом» PDF) ---
    if enable_ocr and len(seq) < TOC_GOOD_ENOUGH:
        try:
            ocr_seq, ocr_text = await _ocr_then_heuristic(doc, progress_cb)
            if len(ocr_seq) > len(seq):
                seq = ocr_seq
                source = "ocr_heuristic"
            elif ocr_text and not ocr_seq:
                # OCR сделан, но эвристика на OCR-тексте не помогла — LLM-попытка
                ocr_llm_seq = await _ocr_then_llm(ocr_text)
                if len(ocr_llm_seq) > len(seq):
                    seq = ocr_llm_seq
                    source = "ocr_llm"
        except Exception as e:
            print(f"[toc_builder] OCR failed: {e}")
            ocr_text = None

    # --- Уровень 5: deep_scan — последний шанс ---
    if enable_deep_scan and len(seq) <= TOC_MIN_USEFUL:
        full_text = full_text_extractor() if not ocr_text else ocr_text
        deep_seq = await _deep_scan(full_text, progress_cb)
        if len(deep_seq) > len(seq):
            seq = deep_seq
            source = "deep_scan"

    # --- Уровень 6: LLM-expansion для верхнеуровневого ToC ---
    if enable_llm_expand and _is_high_level_only(seq):
        full_text = full_text_extractor() if not ocr_text else ocr_text
        expanded = await _llm_expand_parts(seq, full_text, progress_cb)
        if len(expanded) > len(seq):
            seq = expanded
            source = f"{source}+expand"

    return seq, source, ocr_text
