"""
Многоуровневый pipeline для маппинга секций ToC в основной текст.

Уровни (по возрастанию стоимости):
    1. Эвристический поиск с 4 стратегиями (find_real_indices)
    2. Page-hint window — если у секции есть page в ToC, искать ТОЛЬКО
       в окне ±N страниц от ожидаемой позиции, игнорируя глобальный текст.
       Решает «секция найдена в Примечаниях вместо текста главы».
    3. Fuzzy match — sliding-window SequenceMatcher для опечаток LLM ToC
       и лёгких различий написания (1-2 символа). Локальный, дешёвый.
    4. Embedding rescue — семантический поиск через text-embedding модель
    5. LLM rescue — точечный вызов LLM с просьбой найти заголовок в окне
    6. Verification & re-map — после всех попыток проверить порядок секций
       и перепроверить выбивающиеся (out-of-order) элементы.
"""

import re
import difflib

from .pdf_utils import find_real_indices, _search_with_confidence, get_clean_title
from .llm_engine import llm_client
from .embedding_engine import embedding_client


# Паттерн «строки оглавления»: либо точки-лидеры (включая разреженные
# «. . . .» с пробелами между точками), либо «N.N название ... число».
# Если в первых N строках после места совпадения большинство строк такие —
# это ToC, не основной текст.
_TOC_LINE_PATTERN = re.compile(
    # Вариант 1: точки-лидеры + число в конце.
    # `(?:\.\s*){3,}` ловит «....», «. . .», «.  .  .  .» (любые пробелы)
    r'(?:\.\s*){3,}\d{1,4}\s*$'
    r'|'
    # Вариант 2: подчёркивания/тире как лидеры
    r'[_\-—–]{2,}\s*\d{1,4}\s*$'
    r'|'
    # Вариант 3: «N.N название», т.е. нумерованный подраздел в строке ToC
    r'^\s*\d+(?:\.\d+)+\s+\S'
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
# Размер окна для embedding/LLM rescue. На 8K-context LLM влезает ~10K chars
# текста в одно сообщение — поэтому 10K разумный максимум.
RESCUE_WINDOW = 10_000
# Минимальная относительная позиция out-of-order чтобы считать секцию
# подозрительной (например, секция 50 нашлась раньше секции 30).
VERIFY_OUT_OF_ORDER_TOLERANCE = 3
# Если exact-match оказался далеко от ожидаемой по page-hint позиции
# (>= этого числа символов), считаем находку подозрительной и отправляем
# в rescue. Закрывает кейс «Глава 9 нашлась в Примечаниях».
# Пропорционально размеру книги: для книги в 500K chars — 150K (~60 страниц).
# С запасом, потому что page-hint от LLM ToC не всегда точен (LLM может ошибиться
# на 5-10 страниц), и страницы в ToC могут не совпадать с физической нумерацией PDF.
PAGE_DISTANCE_TOLERANCE_RATIO = 0.30


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


# === Fuzzy matching ==========================================================
#
# Высокий порог сходства намеренный: ловим именно опечатки/мелкие отличия
# написания ("Не боитесь" vs "Не бойтесь"), но НЕ похожие по смыслу разные
# главы ("Расчёт параметров системы А" vs "Расчёт параметров системы Б") —
# это была главная проблема Левенштейна в первой итерации.
FUZZY_THRESHOLD = 0.85
# Минимальная длина title чтобы пробовать fuzzy. На очень коротких заголовках
# fuzzy слишком ненадёжен.
FUZZY_MIN_TITLE_LEN = 8


def _normalize_with_map(s: str) -> tuple[str, list]:
    """
    Возвращает (normalized_text, idx_map), где idx_map[i] — позиция i-го
    нормализованного символа в исходной строке.

    Нормализация та же что в _normalize_for_fuzzy, но сохраняет
    индексы для перевода offset обратно в исходные координаты.
    """
    if not s:
        return "", []
    out_chars = []
    out_map = []
    s_lower = s.lower()
    prev_space = False
    started = False  # для стрипа ведущих пробелов
    space_chars = {' ', ' ', ' ', ' ', ' ', '　', '\t', '\n', '\r'}
    for i, ch in enumerate(s_lower):
        if ch in space_chars:
            if not started:
                continue  # стрипим начальные пробелы
            if prev_space:
                continue  # схлопываем множественные пробелы
            out_chars.append(' ')
            out_map.append(i)
            prev_space = True
        else:
            out_chars.append(ch)
            out_map.append(i)
            prev_space = False
            started = True
    # Хвостовые пробелы (если есть)
    while out_chars and out_chars[-1] == ' ':
        out_chars.pop()
        out_map.pop()
    return ''.join(out_chars), out_map


def _normalize_for_fuzzy(s: str) -> str:
    """
    Нормализация для fuzzy-сравнения:
      - lowercase
      - неразрывные/тонкие/em-spaces → обычный пробел
      - множественные пробелы → один
      - убираем ведущие/хвостовые пробелы
    Так разница между «НЕ\\xa0БОЙТЕСЬ» и «не бойтесь» не влияет на ratio.
    """
    if not s:
        return ""
    s = s.lower()
    # NO-BREAK SPACE, EN SPACE, EM SPACE, NARROW NO-BREAK SPACE, IDEOGRAPHIC SPACE
    for ws in (' ', ' ', ' ', ' ', ' ', '　', '\t'):
        s = s.replace(ws, ' ')
    return re.sub(r'\s+', ' ', s).strip()


def _fuzzy_locate(
    title: str,
    text_window: str,
    threshold: float = FUZZY_THRESHOLD,
) -> tuple[int, float]:
    """
    Скользящим окном по тексту ищет позицию с максимальным сходством с title.

    Стратегия: окно ровно по длине title, шаг небольшой. Тогда
    SequenceMatcher.ratio() ≈ доля совпадающих символов между title и
    кандидатом, что хорошо ловит опечатки в 1-2 символа.

    Перед сравнением обе строки нормализуются через _normalize_for_fuzzy,
    что выравнивает разные виды пробелов и регистр.

    Возвращает (offset, score). Если ни одно окно не дотянуло до threshold —
    возвращает (-1, best_score).
    """
    if not title or len(title) < FUZZY_MIN_TITLE_LEN or not text_window:
        return -1, 0.0

    norm_title = _normalize_for_fuzzy(title)
    window_len = len(norm_title)
    if window_len < FUZZY_MIN_TITLE_LEN:
        return -1, 0.0
    # Нормализованный текст. Длина может отличаться от исходной из-за
    # схлопывания множественных пробелов — но offset мы вернём от исходного,
    # для этого построим mapping норм-индекс → исходный индекс.
    text_lower = text_window.lower()
    normalized, idx_map = _normalize_with_map(text_window)
    if len(normalized) < window_len:
        return -1, 0.0

    # Stride: достаточно мелкий чтобы поймать идеальное выравнивание,
    # но не настолько чтобы каждый символ проверять. 1/8 от длины title.
    stride = max(window_len // 8, 3)

    matcher = difflib.SequenceMatcher(autojunk=False)
    matcher.set_seq1(norm_title)

    best_norm_idx, best_score = -1, 0.0
    # Лёгкий префильтр: проверяем по quick_ratio (без вычисления matching blocks)
    quick_threshold = max(threshold - 0.10, 0.50)

    for i in range(0, len(normalized) - window_len + 1, stride):
        chunk = normalized[i: i + window_len]
        matcher.set_seq2(chunk)
        # quick_ratio — верхняя граница ratio, считается за O(n)
        if matcher.quick_ratio() < quick_threshold:
            continue
        score = matcher.ratio()
        if score > best_score:
            best_score = score
            best_norm_idx = i

    if best_score < threshold or best_norm_idx < 0:
        return -1, best_score

    # Перевод позиции из нормализованной обратно в исходную
    original_idx = idx_map[best_norm_idx] if best_norm_idx < len(idx_map) else best_norm_idx
    return original_idx, best_score


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
    mapped = _verify_and_correct_order(mapped, full_text, full_text_len, total_pages)

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

    # --- Уровни 3-5: fuzzy → embedding → LLM для оставшихся ---
    if not_found:
        if progress_cb:
            await progress_cb(9, f"Rescue для {len(not_found)} секций (fuzzy/embedding/LLM)...")
        fuzzy_rescued = 0
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

            page = curr['item'].get('page')
            # Соседи в порядке текста? prev_end < next_start — нормально.
            # Если порядок нарушен (соседняя секция нашлась далеко не там, где
            # должна), prev_end >= next_start и окно отрицательное.
            order_ok = prev_end < next_start

            if page and total_pages:
                # Page-hint доступен — центрируем вокруг ожидаемой позиции.
                # Если порядок ОК — ограничиваем соседями. Иначе берём
                # фиксированное окно от page-hint без учёта неправильного prev_end.
                est = _estimate_position_from_page(page, total_pages, full_text_len)
                if order_ok:
                    window_start = max(prev_end, est - RESCUE_WINDOW // 2)
                    window_end = min(next_start, est + RESCUE_WINDOW // 2)
                else:
                    window_start = max(0, est - RESCUE_WINDOW // 2)
                    window_end = min(full_text_len, est + RESCUE_WINDOW // 2)
            elif order_ok:
                # Без page-hint — ограниченное окно от prev_end
                window_start = prev_end
                window_end = min(prev_end + RESCUE_WINDOW, next_start)
            else:
                # Нет page, порядок плохой — fallback: окно сразу за next_start
                # (предполагаем что секция где-то после следующей в тексте).
                window_start = next_start
                window_end = min(next_start + RESCUE_WINDOW, full_text_len)

            window_text = full_text[window_start:window_end]
            if not window_text:
                continue

            # Уровень 3: fuzzy match (быстро, без LLM, для опечаток)
            fz_offset, fz_score = _fuzzy_locate(title, window_text)
            if fz_offset >= 0:
                mapped[i]['start_idx'] = window_start + fz_offset
                mapped[i]['end_idx'] = min(
                    window_start + fz_offset + len(title) + 20, window_end
                )
                # confidence привязан к score: 0.85 → 0.65, 1.0 → 0.80
                mapped[i]['confidence'] = round(min(fz_score - 0.20, 0.80), 2)
                mapped[i]['match_strategy'] = 'fuzzy_rescue'
                fuzzy_rescued += 1
                continue

            # Уровень 4: embedding
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

            # Уровень 5: LLM
            offset = await _llm_rescue(title, window_text)
            if offset >= 0:
                mapped[i]['start_idx'] = window_start + offset
                mapped[i]['end_idx'] = min(
                    window_start + offset + len(title) + 20, window_end
                )
                mapped[i]['confidence'] = 0.55
                mapped[i]['match_strategy'] = 'llm_rescue'
                llm_rescued += 1
        if fuzzy_rescued or emb_rescued or llm_rescued:
            print(
                f"[mapping] fuzzy={fuzzy_rescued}, emb={emb_rescued}, llm={llm_rescued}"
            )

    # --- Восстановление _backup для page-distance секций без rescue ---
    # Если page-distance отбросил secию, а rescue не нашёл лучшего варианта —
    # возвращаем оригинальный exact-match. Лучше «возможно ложная позиция»
    # чем «никакой позиции».
    restored = 0
    for m in mapped:
        if m.get('_backup') and m['start_idx'] == -1:
            b = m['_backup']
            m['start_idx'] = b['start_idx']
            m['end_idx'] = b['end_idx']
            m['confidence'] = b['confidence'] * 0.7  # снижаем уверенность
            m['match_strategy'] = b['match_strategy'] + '_restored_after_rescue_fail'
            restored += 1
        m.pop('_backup', None)
    if restored:
        print(f"[mapping] restored {restored} page-distance reverts (rescue not helpful)")

    # Финальная проверка ПОРЯДКА (out-of-order). In-ToC уже проверен в начале;
    # после rescue могут появиться новые out-of-order — их нужно отловить.
    mapped = _verify_and_correct_order(mapped, full_text, full_text_len, total_pages)

    # --- Финальный fallback: page_cut для секций у которых всё rescue провалилось ---
    # Запускается ПОСЛЕ финального verify — иначе секции, найденные rescue но откащенные
    # как out-of-order финальным verify, остаются с conf=0.00 (page_cut их не видел,
    # они были start_idx != -1 в момент первого запуска).
    # Когда заголовок секции физически отсутствует в тексте (крупная типографика,
    # скан, декоративный шрифт — PyMuPDF не извлекает), нарезаем контент по
    # позиции страницы из ToC вместо по совпадению с заголовком.
    if total_pages:
        page_cut = 0
        for m in mapped:
            if m['start_idx'] != -1:
                continue
            page = m['item'].get('page')
            if not page:
                continue
            est = _estimate_position_from_page(page, total_pages, full_text_len)
            if est <= 0:
                continue
            m['start_idx'] = est
            m['end_idx'] = est  # content = full_text[est : next_start]
            m['confidence'] = 0.30
            m['match_strategy'] = 'page_cut'
            page_cut += 1
        if page_cut:
            print(f"[mapping] page_cut fallback: {page_cut} sections")

    return mapped


def _verify_and_correct_order(
    mapped: list,
    full_text: str,
    full_text_len: int,
    total_pages: int = None,
) -> list:
    """
    Проверяет результаты маппинга:
      1. Out-of-order: если секция N оказалась раньше секции M раньше неё в ToC,
         маппинг ошибся (типично: rescue нашёл в Примечаниях).
      2. Found inside ToC: если контент после совпадения выглядит как сама
         таблица оглавления (точки-лидеры, page-нумерация), значит секция
         «найдена» внутри страниц ToC, а не в основном тексте.
      3. Page-distance: если у секции есть page в ToC, а exact-матч оказался
         далеко от ожидаемой позиции — это упоминание в Примечаниях/Глоссарии,
         не основной текст. Отменяем находку.

    Помечаем такие как not_found, чтобы XML не получил мусорные секции.
    """
    running_max = -1
    out_of_order = 0
    toc_content = 0
    page_distance = 0

    # --- Проверка 0: page-distance — exact match далеко от ожидаемой страницы ---
    # Закрывает кейс «Глава 9 нашлась в Примечаниях» — exact-match попал
    # в упоминание главы в Глоссарии вместо реального текста.
    #
    # ВАЖНО: НЕ удаляем находку сразу, а ПОМЕЧАЕМ как suspect и сохраняем
    # оригинальные данные в _backup. После rescue если найдено лучшее место —
    # оно перезапишет. Если нет — восстанавливаем оригинал в финале.
    # Это защита от случаев когда page-hint от LLM ToC неточен — мы рискуем
    # отвергнуть верную находку и не найти замену.
    if total_pages:
        tolerance = max(int(full_text_len * PAGE_DISTANCE_TOLERANCE_RATIO), 30_000)
        for m in mapped:
            if m['start_idx'] == -1:
                continue
            page = m['item'].get('page')
            if not page:
                continue
            # Только рискуем отвергать сильные стратегии (exact/tokenized).
            # У rescue confidence низкий — там и так велик шанс ошибки,
            # их не передаём в page-distance.
            strat = m.get('match_strategy', '')
            if strat not in ('exact', 'exact_normalized', 'tokenized_regex'):
                continue
            est = _estimate_position_from_page(page, total_pages, full_text_len)
            if abs(m['start_idx'] - est) > tolerance:
                # Сохраняем оригинал на случай если rescue не найдёт лучшего.
                m['_backup'] = {
                    'start_idx': m['start_idx'],
                    'end_idx': m['end_idx'],
                    'confidence': m['confidence'],
                    'match_strategy': m['match_strategy'],
                }
                m['start_idx'] = -1
                m['end_idx'] = -1
                m['confidence'] = 0.0
                m['match_strategy'] = 'reverted_page_distance'
                page_distance += 1

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
        # page_cut — позиция приблизительная (оценка по странице), не используем
        # её для якоря running_max и не ревертируем её как out-of-order.
        if strat == 'page_cut':
            continue
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

    if out_of_order or toc_content or page_distance:
        print(
            f"[mapping] verify: reverted {out_of_order} out-of-order, "
            f"{toc_content} in-ToC, {page_distance} page-distance"
        )

    return mapped
