"""
Валидация и скоринг кандидатов ToC.

Используется умным fallback'ом в toc_builder: когда эвристика неполна, мы строим
несколько кандидатов ToC (heuristic / LLM / OCR+LLM) и выбираем лучший ВАЛИДНЫЙ.
Валидация ловит галлюцинации LLM и формальные ошибки:
  - CJK/иностранные символы в заголовках (glm-ocr / LLM-перевод),
  - формальные ошибки (пустые/длинные title, не-монотонные страницы, дубли, level),
  - grounding: заголовок должен реально встречаться в тексте книги (анти-галлюцинация).

Модели (эмбеддер) вызываются ТОЛЬКО для заголовков, не прошедших дешёвую
лексическую проверку, и с ограничением по количеству — GPU дорогой.
"""
import re

from .llm_engine import has_foreign_script
from .embedding_engine import embedding_client, EMBED_MATCH_THRESHOLD

# Порог: какая доля заголовков должна быть «заземлена» в тексте, чтобы ToC считался валидным.
GROUNDING_MIN = 0.8
# Максимум заголовков, проверяемых эмбеддером (cost guard).
MAX_EMBED_CHECKS = 20
# Минимальная доля значимых токенов заголовка, найденных в тексте, для лексического grounding.
LEXICAL_TOKEN_HIT = 0.6
TITLE_MAX_LEN = 150
EMBED_WINDOW_CHARS = 8000


def _norm(t: str) -> str:
    return re.sub(r'\s+', ' ', (t or '').lower()).strip()


def _strip_number_prefix(title: str) -> str:
    """Убирает ведущую нумерацию: «3.2. Название», «Глава 5. Название» → «Название»."""
    t = re.sub(r'^\s*(глава|часть|chapter|part|раздел|§)?\s*[\dIVXLCDM]+(?:\.\d+)*\.?\s*', '',
               title or '', flags=re.IGNORECASE)
    return t.strip()


def _significant_tokens(title: str) -> list:
    """Значимые слова заголовка (без нумерации, длиной >= 4, не чисто цифры)."""
    base = _strip_number_prefix(title)
    toks = re.findall(r'[^\W\d_]{4,}', base.lower(), flags=re.UNICODE)
    return toks


def _lexical_grounded(title: str, norm_full_text: str) -> bool:
    """Дешёвая проверка: значимые токены заголовка присутствуют в тексте книги."""
    toks = _significant_tokens(title)
    if not toks:
        # Заголовок без значимых слов (например «1.2.») — заземлять нечем,
        # считаем его нейтральным (не валит grounding, см. score_toc).
        return True
    hits = sum(1 for tok in toks if tok in norm_full_text)
    return hits / len(toks) >= LEXICAL_TOKEN_HIT


def _page_window(full_text: str, page, max_page: int) -> str:
    """Окно текста вокруг оценочной позиции страницы (для embedding-проверки)."""
    if not isinstance(page, int) or max_page <= 0:
        return full_text[:EMBED_WINDOW_CHARS * 4]
    est = int((page - 1) / max_page * len(full_text))
    lo = max(0, est - EMBED_WINDOW_CHARS // 2)
    return full_text[lo: lo + EMBED_WINDOW_CHARS]


def check_formal(seq: list, raw_entry_count: int = 0) -> list:
    """Возвращает список формальных проблем (пустой список = ок)."""
    issues = []
    if not seq:
        return ['empty']
    titles = []
    for s in seq:
        t = (s.get('title') or '').strip()
        if not t:
            issues.append('empty_title')
        elif len(t) > TITLE_MAX_LEN:
            issues.append(f'title_too_long:{t[:30]}')
        lvl = s.get('level', 1)
        if lvl not in (1, 2, 3):
            issues.append(f'bad_level:{lvl}')
        titles.append(_norm(t))

    # Дубли
    seen = set()
    dups = sum(1 for t in titles if t in seen or seen.add(t))
    if dups > max(1, len(titles) * 0.1):
        issues.append(f'duplicates:{dups}')

    # Монотонность страниц: допускаем один «сброс» (краткое + детальное оглавление)
    pages = [s.get('page') for s in seq if isinstance(s.get('page'), int)]
    resets = sum(1 for a, b in zip(pages, pages[1:]) if b < a)
    if resets > 1:
        issues.append(f'pages_non_monotonic:{resets}')

    # Абсурдное количество (LLM «расщепил» текст)
    if raw_entry_count and len(seq) > raw_entry_count * 3:
        issues.append(f'too_many:{len(seq)}vs{raw_entry_count}')

    return issues


def check_cjk(seq: list) -> list:
    """Заголовки с иностранными (CJK/арабскими) символами."""
    return [s.get('title', '') for s in seq if has_foreign_script(s.get('title', ''))]


async def score_toc(seq: list, full_text: str, raw_entry_count: int = 0,
                    embed: bool = True) -> dict:
    """
    Скорит кандидата ToC. Возвращает dict с метриками для выбора лучшего варианта.

    grounding = доля заголовков, реально присутствующих в тексте книги (лексически
    или семантически). Низкий grounding => LLM нагаллюцинировал.
    """
    n_total = len(seq)
    if n_total == 0:
        return {'n_total': 0, 'n_grounded': 0, 'grounding': 0.0,
                'formal_issues': ['empty'], 'cjk_items': []}

    norm_full = _norm(full_text)
    formal_issues = check_formal(seq, raw_entry_count)
    cjk_items = check_cjk(seq)

    grounded = 0
    ungrounded = []
    for s in seq:
        if _lexical_grounded(s.get('title', ''), norm_full):
            grounded += 1
        else:
            ungrounded.append(s)

    # Семантическая до-проверка не прошедших лексически (ограниченно, GPU дорогой)
    if embed and ungrounded and await embedding_client.is_available():
        max_page = max((s.get('page') for s in seq if isinstance(s.get('page'), int)), default=1)
        for s in ungrounded[:MAX_EMBED_CHECKS]:
            title = _strip_number_prefix(s.get('title', '')) or s.get('title', '')
            window = _page_window(full_text, s.get('page'), max_page)
            try:
                _, sc = await embedding_client.locate_section(title, window)
            except Exception:
                sc = 0.0
            if sc >= EMBED_MATCH_THRESHOLD:
                grounded += 1

    grounding = grounded / n_total
    return {'n_total': n_total, 'n_grounded': grounded, 'grounding': round(grounding, 3),
            'formal_issues': formal_issues, 'cjk_items': cjk_items}


def is_valid(score: dict) -> bool:
    """ToC валиден: формально чист, без CJK, заземлён в тексте."""
    return (not score['formal_issues']
            and not score['cjk_items']
            and score['grounding'] >= GROUNDING_MIN
            and score['n_total'] > 0)
