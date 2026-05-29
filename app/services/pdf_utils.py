import re
import fitz


# ---------------------------------------------------------------------------
# Константы
# ---------------------------------------------------------------------------

TOC_SCAN_RATIO = 0.20
TOC_SCAN_MAX_CHARS = 100_000
TOC_FALLBACK_OFFSET = 3_000

CONF_EXACT      = 1.00
CONF_TOKENIZED  = 0.85
CONF_PARTIAL    = 0.60
CONF_NUM_PREFIX = 0.40
CONF_NOT_FOUND  = 0.00

PARTIAL_WORDS_MIN    = 4
PARTIAL_GAP_MAX      = 15
HEADER_JUNK_MIN_LEN  = 20
HEADER_JUNK_MIN_REPEAT = 4

# Разделы, после которых отключается откат поиска к началу (во избежание
# прыжка в алфавитный указатель, примечания и т.д.)
TERMINAL_SECTION_MARKERS = frozenset([
    'приложени', 'алфавитн', 'указател', 'примечани',
    'библиограф', 'список литератур', 'список источников',
    'appendix', 'index', 'references', 'notes',
])

# Детектор кракозябр
GARBAGE_CHAR_RATIO   = 0.07   # >7% мусорных символов → текст нечитаем
GARBAGE_MIN_TEXT_LEN = 200    # анализируем только если текста хотя бы столько
GARBAGE_REAL_WORD_MIN = 0.40  # минимальная доля «реальных» слов (3+ букв подряд)
GARBAGE_BROKEN_WORD_RATIO = 0.25  # >25% «битых» слов (буквы+цифры/символы) → мусор


# ---------------------------------------------------------------------------
# Базовые утилиты
# ---------------------------------------------------------------------------

def get_all_text(doc) -> str:
    full_text = ""
    for page in doc:
        text = page.get_text().replace('\x00', '').replace('\x0c', ' ')
        full_text += text + "\n"
    return full_text


def get_clean_title(title: str) -> str:
    if not title:
        return ""
    clean = re.sub(
        r'^(?:Глава|Chapter|Часть|Раздел|§|Введение|Предисловие|Заключение'
        r'|[IVXLCDM]+\.|[0-9]+(?:\.[0-9]+)*\.?)\s*',
        '', title, flags=re.IGNORECASE
    )
    clean = re.sub(r'[\.\s\…\t]+$', '', clean).strip()
    return clean


def clean_footer_header(full_text: str) -> str:
    lines = full_text.split('\n')
    if len(lines) < 60:
        return full_text
    counts: dict = {}
    for line in lines:
        s = line.strip()
        if len(s) > HEADER_JUNK_MIN_LEN:
            counts[s] = counts.get(s, 0) + 1
    junk = {line for line, cnt in counts.items() if cnt > HEADER_JUNK_MIN_REPEAT}
    return "\n".join(line for line in lines if line.strip() not in junk)


def fast_clean_chunk(text: str) -> str:
    text = re.sub(r'(\w+)-\n\s*(\w+)', r'\1\2', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    lines = text.split('\n')
    lines = [l for l in lines if not re.match(r'^\s*\d{1,4}\s*$', l)]
    return '\n'.join(lines)


# ---------------------------------------------------------------------------
# Детектор нечитаемого текста (кракозябры / плохой OCR)
# ---------------------------------------------------------------------------

# Паттерн нормальных символов компилируем один раз
_NORMAL_CHARS = re.compile(
    r'[а-яёА-ЯЁa-zA-Z0-9 \t\n\r.,!?;:\'"()\[\]\-—–«»…]'
)
# Токен (последовательность не-пробельных символов) длиной >= 3
_TOKEN_PATTERN = re.compile(r'\S{3,}')
# «Битый» токен: содержит букву И цифру/спецсимвол в смеси,
# например «1(апиталовлоэкения», «€ти:иулирование», «1!1отивация».
_BROKEN_WORD = re.compile(
    r'^(?=.*[а-яёА-ЯЁa-zA-Z])(?=.*[\d\|\{\}\[\]\(\)€\$\\/!@#\^&*<>])'
)


def detect_garbage_text(text: str) -> dict:
    """
    Анализирует текст на предмет артефактов плохого OCR / битой кодировки.

    Признаки кракозябр:
      1. Доля символов вне нормального диапазона > GARBAGE_CHAR_RATIO
         (например, тексты с обилием |€{}\\$).
      2. Доля «реальных» слов (3+ подряд букв одного алфавита) < GARBAGE_REAL_WORD_MIN.
         Это ловит случай когда текст состоит из коротких слов и мусора.
      3. Доля «битых» слов (буквы + цифры/спецсимволы в смеси внутри одного токена)
         > GARBAGE_BROKEN_WORD_RATIO. Это ловит unicode-валидный мусор вроде
         «1(апиталовлоэкения персонала», где кодировка повреждена, но символы валидны.

    Возвращает диагностику с булевым флагом is_garbage.
    """
    if len(text) < GARBAGE_MIN_TEXT_LEN:
        return {
            'is_garbage': False,
            'garbage_ratio': 0.0,
            'broken_word_ratio': 0.0,
            'real_word_ratio': 1.0,
            'reason': 'too_short_to_analyze',
        }

    total_chars = len(text)
    normal_chars = len(_NORMAL_CHARS.findall(text))
    garbage_ratio = (total_chars - normal_chars) / total_chars

    all_tokens = re.findall(r'\S+', text)
    token_count = max(len(all_tokens), 1)

    real_words = re.findall(r'[а-яёА-ЯЁa-zA-Z]{3,}', text)
    real_word_ratio = len(real_words) / token_count

    # «Битые» слова: длина >= 3, содержат букву и инородный символ одновременно
    longish_tokens = _TOKEN_PATTERN.findall(text)
    broken_tokens = sum(1 for t in longish_tokens if _BROKEN_WORD.match(t))
    broken_word_ratio = broken_tokens / max(len(longish_tokens), 1)

    is_garbage = (
        garbage_ratio > GARBAGE_CHAR_RATIO
        or real_word_ratio < GARBAGE_REAL_WORD_MIN
        or broken_word_ratio > GARBAGE_BROKEN_WORD_RATIO
    )

    reason = (
        f"garbage={garbage_ratio:.2%} real_words={real_word_ratio:.2%} "
        f"broken_words={broken_word_ratio:.2%}"
        if is_garbage else "ok"
    )

    return {
        'is_garbage': is_garbage,
        'garbage_ratio': round(garbage_ratio, 4),
        'broken_word_ratio': round(broken_word_ratio, 4),
        'real_word_ratio': round(real_word_ratio, 4),
        'reason': reason,
    }


def check_document_readability(doc) -> dict:
    """
    Проверяет читаемость PDF-документа по выборке страниц.

    Берёт до 10 страниц равномерно по документу. Документ считается нечитаемым
    если ХОТЯ БЫ ОДНА из проверок срабатывает на нескольких страницах:
      - доля мусорных символов (garbage_ratio)
      - доля битых слов (broken_word_ratio)
      - доля реальных слов (real_word_ratio)

    Это ловит документы с unicode-валидным мусором, где средний garbage_ratio
    низкий, но слова в принципе не читаемы.
    """
    total_pages = len(doc)
    if total_pages == 0:
        return {
            'is_readable': False, 'garbage_ratio': 1.0,
            'recommendation': 'ocr_required', 'detail': 'empty_document',
        }

    # 10 страниц равномерно по документу (или все, если их меньше)
    sample_size = min(10, total_pages)
    step = max(1, total_pages // sample_size)
    sample_indices = list(range(0, total_pages, step))[:sample_size]

    results = []
    for idx in sample_indices:
        page_text = doc[idx].get_text()
        if len(page_text.strip()) < 50:
            continue
        results.append(detect_garbage_text(page_text))

    if not results:
        return {
            'is_readable': False, 'garbage_ratio': 1.0,
            'recommendation': 'ocr_required', 'detail': 'no_text_extracted',
        }

    garbage_pages = sum(1 for r in results if r['is_garbage'])
    avg_garbage = sum(r['garbage_ratio'] for r in results) / len(results)
    avg_broken = sum(r['broken_word_ratio'] for r in results) / len(results)
    avg_real = sum(r['real_word_ratio'] for r in results) / len(results)

    # Документ нечитаем если >= 40% выборки помечены как мусор
    is_readable = (garbage_pages / len(results)) < 0.40

    return {
        'is_readable': is_readable,
        'garbage_ratio': round(avg_garbage, 4),
        'broken_word_ratio': round(avg_broken, 4),
        'real_word_ratio': round(avg_real, 4),
        'recommendation': 'algorithm' if is_readable else 'ocr_required',
        'detail': (
            f"sampled {len(results)} pages, garbage_pages={garbage_pages}/{len(results)}, "
            f"avg_garbage={avg_garbage:.2%}, avg_broken={avg_broken:.2%}, "
            f"avg_real_words={avg_real:.2%}"
        ),
    }


# ---------------------------------------------------------------------------
# Поиск границы оглавления
# ---------------------------------------------------------------------------

def _is_terminal_section(title: str) -> bool:
    t = title.lower()
    return any(marker in t for marker in TERMINAL_SECTION_MARKERS)


def find_toc_boundary(full_text: str, sequence: list) -> int:
    if not sequence:
        return 0
    limit = min(int(len(full_text) * TOC_SCAN_RATIO), TOC_SCAN_MAX_CHARS)
    last_toc_pos = 0
    for item in sequence[-5:]:
        title = get_clean_title(item['title'])
        if len(title) < 5:
            continue
        match = re.search(re.escape(title), full_text[:limit], re.IGNORECASE)
        if match and match.end() > last_toc_pos:
            last_toc_pos = match.end()
    return last_toc_pos if last_toc_pos > 0 else TOC_FALLBACK_OFFSET


# ---------------------------------------------------------------------------
# Confidence-based поиск
# ---------------------------------------------------------------------------

_LIST_BULLETS = '•·‣◦●▪–-—*'


def _is_list_context(full_text: str, end_idx: int, window: int = 140) -> bool:
    """
    True если текст СРАЗУ после позиции end_idx выглядит как пункт списка/сводки,
    а не как начало прозы. Признаки: ведущий буллет «•», цепочка точек-лидеров,
    либо очень близко (в пределах ~50 симв.) идёт следующий буллет — типичная
    буллетная сводка/мини-оглавление в теле книги (Розенсон), куда ошибочно
    матчатся заголовки подразделов.
    """
    seg = full_text[end_idx:end_idx + window]
    s = seg.lstrip()
    if not s:
        return False
    if s[0] in _LIST_BULLETS:
        return True
    # точки-лидеры (оглавление): «. . . .» или «....»
    if seg[:12].count('.') >= 4:
        return True
    # следующий буллет совсем рядом → подряд идущие пункты списка
    nxt = seg.find('•')
    if 0 <= nxt <= 50:
        return True
    return False


def _find_first_nonlist(full_text: str, title: str, from_pos: int, max_occ: int = 8) -> int:
    """
    Возвращает индекс первого вхождения title от from_pos, после которого НЕ список
    (не буллет/не точки). Если все вхождения «списочные» — возвращает первое
    (поведение по умолчанию). -1 если вхождений нет.
    """
    pos = from_pos
    first = -1
    for _ in range(max_occ):
        idx = full_text.find(title, pos)
        if idx == -1:
            break
        if first == -1:
            first = idx
        if not _is_list_context(full_text, idx + len(title)):
            return idx
        pos = idx + len(title)
    return first


def _first_nonlist_match(pattern, full_text: str, from_pos: int, max_occ: int = 8):
    """Аналог _find_first_nonlist для compiled regex: первый non-list match, иначе первый."""
    first = None
    for i, m in enumerate(pattern.finditer(full_text, from_pos)):
        if i >= max_occ:
            break
        if first is None:
            first = m
        if not _is_list_context(full_text, m.end()):
            return m
    return first


def _search_with_confidence(
    full_text: str,
    full_title: str,
    clean_title: str,
    current_pos: int,
    start_pos: int,
) -> dict | None:

    # Стратегия 1: точное совпадение.
    # Предпочитаем вхождение, после которого идёт проза, а не буллет/точки —
    # иначе заголовок матчится в буллетной сводке/мини-оглавлении в теле книги
    # (Розенсон), и секция получает контент «•» вместо реального тела.
    for title in [full_title, clean_title]:
        if not title:
            continue
        idx = _find_first_nonlist(full_text, title, current_pos)
        if idx == -1:
            idx = _find_first_nonlist(full_text, title, start_pos)
        if idx != -1:
            return {'start': idx, 'end': idx + len(title),
                    'confidence': CONF_EXACT, 'strategy': 'exact'}

    # Стратегия 2: tokenized regex
    for title in [full_title, clean_title]:
        tokens = re.findall(r'[a-zA-Zа-яА-Я0-9§]+', title)
        if len(tokens) < 2:
            continue
        pattern_str = r'[\s\W]*?'.join(re.escape(t) for t in tokens)
        pattern = re.compile(pattern_str, re.IGNORECASE | re.DOTALL)
        match = _first_nonlist_match(pattern, full_text, current_pos)
        if not match:
            match = _first_nonlist_match(pattern, full_text, start_pos)
        if match:
            # Промоут до conf=1.0 если match отличается от title только пробелами:
            # одна нормализация пробельных символов с обеих сторон — и они равны.
            # Это типичный кейс PDF-извлечения, где переносы строк/неразрывные
            # пробелы рвут exact-match, но содержимое идентично title.
            match_norm = re.sub(r'\s+', ' ', match.group()).strip().lower()
            title_norm = re.sub(r'\s+', ' ', title).strip().lower()
            if match_norm == title_norm:
                return {'start': match.start(), 'end': match.end(),
                        'confidence': CONF_EXACT, 'strategy': 'exact_normalized'}
            return {'start': match.start(), 'end': match.end(),
                    'confidence': CONF_TOKENIZED, 'strategy': 'tokenized_regex'}

    # Стратегия 3: первые N значимых слов
    words = re.findall(r'[a-zA-Zа-яА-Я]{3,}', clean_title)
    if len(words) >= PARTIAL_WORDS_MIN:
        gap = r'[\s\W]{0,' + str(PARTIAL_GAP_MAX) + r'}'
        pattern_str = gap.join(re.escape(w) for w in words[:PARTIAL_WORDS_MIN])
        pattern = re.compile(pattern_str, re.IGNORECASE | re.DOTALL)
        match = pattern.search(full_text, current_pos)
        if not match:
            match = pattern.search(full_text, start_pos)
        if match:
            return {'start': match.start(), 'end': match.end(),
                    'confidence': CONF_PARTIAL, 'strategy': 'partial_words'}

    # Стратегия 4: числовой префикс + первое слово
    num_prefix = re.match(r'^(\d+(?:\.\d+)*\.?)\s+(\w+)', clean_title)
    if num_prefix:
        pattern = re.compile(
            re.escape(num_prefix.group(1)) + r'[\s\W]{0,5}' + re.escape(num_prefix.group(2)),
            re.IGNORECASE
        )
        match = pattern.search(full_text, current_pos)
        if not match:
            match = pattern.search(full_text, start_pos)
        if match:
            return {'start': match.start(), 'end': match.end(),
                    'confidence': CONF_NUM_PREFIX, 'strategy': 'num_prefix'}

    return None


def find_real_indices(full_text: str, sequence: list) -> list:
    start_pos = find_toc_boundary(full_text, sequence)
    indices_map = []
    current_pos = start_pos
    past_terminal = False

    for item in sequence:
        full_title = item['title'].strip()
        if not full_title:
            continue
        clean_title = get_clean_title(full_title)

        if _is_terminal_section(full_title):
            past_terminal = True

        # После терминального раздела откат к start_pos отключается —
        # это предотвращает прыжок поиска в алфавитный указатель / приложения.
        effective_start = current_pos if past_terminal else start_pos

        result = _search_with_confidence(
            full_text, full_title, clean_title, current_pos, effective_start
        )

        if result:
            indices_map.append({
                "item": item,
                "start_idx": result['start'],
                "end_idx": result['end'],
                "confidence": result['confidence'],
                "match_strategy": result['strategy'],
            })
            current_pos = result['end']
        else:
            print(f"WARN [not_found]: «{full_title}»")
            indices_map.append({
                "item": item,
                "start_idx": -1,
                "end_idx": -1,
                "confidence": CONF_NOT_FOUND,
                "match_strategy": "not_found",
            })

    # Порядок ToC сохраняется намеренно: build_tree_structure строит дерево
    # по level-последовательности, и перестановка не-найденных элементов
    # в конец сломала бы родительско-дочерние связи (например, раздел 2.1
    # оказался бы дочерним к Главе 1 если Глава 2 не найдена).
    return indices_map


def get_confidence_stats(indices_map: list) -> dict:
    total = len(indices_map)
    if total == 0:
        return {}
    by_strategy: dict = {}
    for item in indices_map:
        s = item.get('match_strategy', 'unknown')
        by_strategy[s] = by_strategy.get(s, 0) + 1
    confidences = [item['confidence'] for item in indices_map]
    return {
        'total': total,
        'avg_confidence': round(sum(confidences) / total, 3),
        'not_found': by_strategy.get('not_found', 0),
        'by_strategy': by_strategy,
    }
