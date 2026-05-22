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

    # --- Уровень 5: verification (порядок) ---
    mapped = _verify_and_correct_order(mapped, full_text, full_text_len)

    return mapped


def _verify_and_correct_order(mapped: list, full_text: str, full_text_len: int) -> list:
    """
    Проверяет что найденные секции расположены в правильном порядке.

    Если секция N оказалась раньше секции M (где N > M+TOLERANCE),
    значит mapping ошибся. Помечаем её как not_found, чтобы XML не получил
    «Главу 8» внутри «Главы 3».

    Простой алгоритм:
      идём по mapped, поддерживаем running_max(start_idx)
      если очередной start_idx сильно меньше running_max — отбрасываем
    """
    running_max = -1
    last_strong_strategy = None  # exact/tokenized — доверяем
    corrected = 0

    for i, m in enumerate(mapped):
        if m['start_idx'] == -1:
            continue
        strat = m.get('match_strategy', '')

        # Если предыдущая «сильная» находка была дальше — текущая подозрительна
        if running_max > 0 and m['start_idx'] < running_max - 1000:
            # Доверяем exact/tokenized больше чем rescue/page_hint
            if strat in ('embedding_rescue', 'llm_rescue') or 'page_hint' in strat:
                m['start_idx'] = -1
                m['end_idx'] = -1
                m['confidence'] = 0.0
                m['match_strategy'] = f'reverted_out_of_order'
                corrected += 1
                continue
            # Если это сильная стратегия — возможно прошлый поиск ошибся,
            # но мы не можем легко вернуться; просто оставляем.

        if m['start_idx'] > running_max:
            running_max = m['start_idx']
            last_strong_strategy = strat

    if corrected:
        print(f"[mapping] verify: reverted {corrected} out-of-order sections")

    return mapped
