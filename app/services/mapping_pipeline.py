"""
Многоуровневый pipeline для маппинга секций ToC в основной текст.

Уровни (по возрастанию стоимости):
    1. Эвристический поиск с 4 стратегиями (find_real_indices)
    2. Page-hint window — если у секции есть page в ToC, искать ТОЛЬКО
       в окне ±N страниц от ожидаемой позиции, игнорируя глобальный текст.
       Решает «секция найдена в Примечаниях вместо текста главы».
    3. Embedding rescue — семантический поиск через text-embedding модель
    4. LLM rescue — точечный вызов LLM с просьбой найти заголовок в окне
    5. Verification & re-map — после всех попыток проверить порядок секций
       и перепроверить выбивающиеся (out-of-order) элементы.
"""

import re

from .pdf_utils import find_real_indices, _search_with_confidence, get_clean_title
from .llm_engine import llm_client
from .embedding_engine import embedding_client


# Паттерн «строки оглавления»: либо точки-лидеры + число в конце, либо
# короткое «N.N название ... число». Если в первых N строках после места
# совпадения большинство строк такие — это ToC, не основной текст.
_TOC_LINE_PATTERN = re.compile(
    r'(?:\.{3,}|\s*[._]{2,})\s*\d{1,4}\s*$|^\s*\d+(?:\.\d+)+\s+\S'
)


def _looks_like_toc_content(text: str, sample_lines: int = 10) -> bool:
    """
    True если первые sample_lines строк выглядят как ToC: много точек-лидеров,
    короткие строки оканчивающиеся числом. Это означает что секция найдена
    ВНУТРИ страниц оглавления, а не в основном тексте.
    """
    if not text:
        return False
    lines = [l.strip() for l in text.splitlines() if l.strip()][:sample_lines]
    if len(lines) < 3:
        return False
    toc_like = sum(1 for l in lines if _TOC_LINE_PATTERN.search(l))
    return toc_like / len(lines) > 0.5


# Размер окна для page-hint поиска (в символах от ожидаемой позиции)
PAGE_HINT_WINDOW = 5_000
# Размер окна для embedding/LLM rescue
RESCUE_WINDOW = 8_000
# Минимальная относительная позиция out-of-order чтобы считать секцию
# подозрительной (например, секция 50 нашлась раньше секции 30).
VERIFY_OUT_OF_ORDER_TOLERANCE = 3


def _estimate_position_from_page(page: int, total_pages: int, full_text_len: int) -> int:
    """Приближённо переводит номер страницы в позицию в char-stream."""
    if not page or not total_pages or page < 1:
        return 0
    ratio = (page - 1) / max(total_pages, 1)
    return int(ratio * full_text_len)


def _page_hint_search(
    title: str,
    full_text: str,
    full_text_len: int,
    page: int,
    total_pages: int,
) -> dict | None:
    """
    Ищет title в окне ±PAGE_HINT_WINDOW от ожидаемой позиции по странице.
    Использует те же стратегии что _search_with_confidence, но в узком окне.
    """
    if not page or not total_pages:
        return None

    est_pos = _estimate_position_from_page(page, total_pages, full_text_len)
    window_start = max(0, est_pos - PAGE_HINT_WINDOW)
    window_end = min(full_text_len, est_pos + PAGE_HINT_WINDOW)
    window_text = full_text[window_start:window_end]

    clean = get_clean_title(title)
    # Используем существующий поиск, но передаём в качестве start_pos то же window_start.
    # current_pos = 0 чтобы не блокировать поиск началом окна.
    result = _search_with_confidence(
        window_text, title.strip(), clean, current_pos=0, start_pos=0
    )
    if not result:
        return None
    # Переводим offset обратно в абсолютную позицию
    return {
        'start': window_start + result['start'],
        'end': window_start + result['end'],
        'confidence': max(result['confidence'] - 0.1, 0.40),  # снижаем confidence (был fallback)
        'strategy': f"page_hint_{result['strategy']}",
    }


async def _embedding_rescue(title: str, window_text: str) -> tuple[int, float]:
    """Семантический поиск через embeddings."""
    offset, score = await embedding_client.locate_section(title, window_text)
    return offset, score


async def _llm_rescue(title: str, window_text: str) -> int:
    """LLM-поиск как последний tier."""
    return await llm_client.locate_section_in_text(title, window_text)


# --- Главный pipeline --------------------------------------------------------

async def map_sequence(
    sequence: list,
    full_text: str,
    total_pages: int,
    progress_cb=None,
) -> list:
    """
    Многоуровневый маппинг последовательности секций в полный текст.

    Возвращает indices_map того же формата что и find_real_indices.
    """
    # --- Уровень 1: базовая эвристика ---
    mapped = find_real_indices(full_text, sequence)

    if not mapped:
        return mapped

    full_text_len = len(full_text)

    # --- РАННИЙ verify: убираем находки внутри страниц ToC ---
    # Запускаем ДО rescue, чтобы передать «найденные в ToC» секции на rescue,
    # как настоящие not_found. Иначе они останутся с ложным confidence=1.0.
    mapped = _verify_and_correct_order(mapped, full_text, full_text_len)

    not_found = [i for i, m in enumerate(mapped) if m['start_idx'] == -1]
    if not not_found:
        return mapped

    # --- Уровень 2: page-hint для каждой не найденной секции ---
    page_hint_rescued = 0
    for i in not_found[:]:
        item = mapped[i]['item']
        page = item.get('page')
        if not page:
            continue
        result = _page_hint_search(
            item['title'], full_text, full_text_len, page, total_pages
        )
        if result:
            mapped[i]['start_idx'] = result['start']
            mapped[i]['end_idx'] = result['end']
            mapped[i]['confidence'] = result['confidence']
            mapped[i]['match_strategy'] = result['strategy']
            page_hint_rescued += 1
            not_found.remove(i)

    if page_hint_rescued:
        print(f"[mapping] page_hint_rescue: {page_hint_rescued}")

    # --- Уровни 3+4: embedding и LLM для оставшихся ---
    if not_found:
        if progress_cb:
            await progress_cb(9, f"Rescue для {len(not_found)} секций (embedding+LLM)...")
        emb_rescued = 0
        llm_rescued = 0
        for i in not_found:
            curr = mapped[i]
            title = curr['item']['title']
            # Окно: между ближайшими найденными соседями
            prev_end = next(
                (mapped[j]['end_idx'] for j in range(i - 1, -1, -1) if mapped[j]['start_idx'] != -1),
                0
            )
            next_start = next(
                (mapped[j]['start_idx'] for j in range(i + 1, len(mapped)) if mapped[j]['start_idx'] != -1),
                full_text_len
            )

            # Если есть page-hint, центрируем окно вокруг ожидаемой позиции
            page = curr['item'].get('page')
            if page and total_pages:
                est = _estimate_position_from_page(page, total_pages, full_text_len)
                window_start = max(prev_end, est - RESCUE_WINDOW // 2)
                window_end = min(next_start, est + RESCUE_WINDOW // 2)
            else:
                # Без page-hint — ограниченное окно от prev_end
                window_start = prev_end
                window_end = min(prev_end + RESCUE_WINDOW, next_start)

            window_text = full_text[window_start:window_end]

            # Уровень 3: embedding
            emb_offset, emb_score = await _embedding_rescue(title, window_text)
            if emb_offset >= 0:
                mapped[i]['start_idx'] = window_start + emb_offset
                mapped[i]['end_idx'] = min(
                    window_start + emb_offset + len(title) + 20, window_end
                )
                mapped[i]['confidence'] = round(min(emb_score, 0.70), 2)
                mapped[i]['match_strategy'] = 'embedding_rescue'
                emb_rescued += 1
                continue

            # Уровень 4: LLM
            offset = await _llm_rescue(title, window_text)
            if offset >= 0:
                mapped[i]['start_idx'] = window_start + offset
                mapped[i]['end_idx'] = min(
                    window_start + offset + len(title) + 20, window_end
                )
                mapped[i]['confidence'] = 0.55
                mapped[i]['match_strategy'] = 'llm_rescue'
                llm_rescued += 1
        if emb_rescued or llm_rescued:
            print(f"[mapping] emb_rescue: {emb_rescued}, llm_rescue: {llm_rescued}")

    # Финальная проверка ПОРЯДКА (out-of-order). In-ToC уже проверен в начале;
    # после rescue могут появиться новые out-of-order — их нужно отловить.
    mapped = _verify_and_correct_order(mapped, full_text, full_text_len)

    return mapped


def _verify_and_correct_order(mapped: list, full_text: str, full_text_len: int) -> list:
    """
    Проверяет результаты маппинга:
      1. Out-of-order: если секция N оказалась раньше секции M раньше неё в ToC,
         маппинг ошибся (типично: rescue нашёл в Примечаниях).
      2. Found inside ToC: если контент после совпадения выглядит как сама
         таблица оглавления (точки-лидеры, page-нумерация), значит секция
         «найдена» внутри страниц ToC, а не в основном тексте.

    Помечаем такие как not_found, чтобы XML не получил мусорные секции.
    """
    running_max = -1
    out_of_order = 0
    toc_content = 0

    # --- Проверка 1: «контент это сам ToC» ---
    # Берём фиксированные 800 chars после end_idx (НЕ ограничивая next_start),
    # потому что внутри ToC секции идут подряд через узкие промежутки,
    # и без расширения окна не накопится достаточно строк для детекции.
    for i, m in enumerate(mapped):
        if m['start_idx'] == -1:
            continue
        content_preview = full_text[m['end_idx']: m['end_idx'] + 800]
        if _looks_like_toc_content(content_preview):
            m['start_idx'] = -1
            m['end_idx'] = -1
            m['confidence'] = 0.0
            m['match_strategy'] = 'reverted_in_toc'
            toc_content += 1

    # --- Проверка 2: out-of-order ---
    for i, m in enumerate(mapped):
        if m['start_idx'] == -1:
            continue
        strat = m.get('match_strategy', '')
        if running_max > 0 and m['start_idx'] < running_max - 1000:
            if strat in ('embedding_rescue', 'llm_rescue') or 'page_hint' in strat:
                m['start_idx'] = -1
                m['end_idx'] = -1
                m['confidence'] = 0.0
                m['match_strategy'] = 'reverted_out_of_order'
                out_of_order += 1
                continue
        if m['start_idx'] > running_max:
            running_max = m['start_idx']

    if out_of_order or toc_content:
        print(f"[mapping] verify: reverted {out_of_order} out-of-order, {toc_content} in-ToC")

    return mapped
