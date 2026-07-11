"""Content quality detector — flag sections whose body is junk (leader-dots,
bullet fragments, ToC fragments) instead of real prose.

Background. Standard metrics — `coverage`, `real_content_sections` — count
characters but don't tell us *what kind* of characters. Розенсон at one point
had `coverage = 0.986` because the bytes were present, just not in the right
section: subsections held «•» or `«........»` and the real prose was absorbed by
their neighbours. So a junk section can look healthy by character count.

This module gives a cheap, deterministic classifier:

    classify_content(text) -> str | None
        Returns None for OK content. Otherwise returns one of:
          'dots'           — leader-dot dominated («.......» > 50% of non-whitespace)
          'bullet'         — very short, dominated by bullet/punctuation
          'toc_fragment'   — many lines look like «текст ......... номер_страницы»
          'whitespace'     — after strip, < 20 chars of meaningful content

Used by reporting (`run_corpus.py`) to surface mis-mapped sections that the raw
length metric would otherwise mask. Can also be wired into the mapping pipeline
later as a revert signal — for now this is observation-only.
"""
import re


# Признаки «мусорного» содержимого секции.
# Все пороги откалиброваны на корпусе так, чтобы не флагать нормальную прозу.

# Доля точек среди не-пробельных символов: > 50% → leader-dot заливка.
_DOTS_FRACTION_THRESHOLD = 0.50

# Минимум значимого текста после strip — меньше = «whitespace».
_MIN_MEANINGFUL_CHARS = 20

# Bullet-fragment: содержит буллет и реального текста <50 chars.
_BULLET_FRAGMENT_MAX_TEXT = 50

# ToC-fragment: > N таких строк в первых 800 chars.
_TOC_LIKE_LINE = re.compile(r'^.{3,}?[.\s_…]{4,}\s*\d{1,4}\s*$')
_TOC_FRAGMENT_MIN_LINES = 2
_TOC_FRAGMENT_MIN_FRACTION = 0.40  # доля ToC-подобных строк среди непустых

# Bullet-символы.
_BULLETS = set('•·‣◦●▪◆◇*–—')


def _strip_text(text: str) -> str:
    if not text:
        return ''
    return text.strip()


def _dots_fraction(text: str) -> float:
    """Доля символов '.' среди не-пробельных. Лидер-точки '. . . .' тоже считаются."""
    non_ws = [c for c in text if not c.isspace()]
    if not non_ws:
        return 0.0
    dots = sum(1 for c in non_ws if c == '.')
    return dots / len(non_ws)


def _looks_bullet_fragment(text: str) -> bool:
    """Очень короткий контент, начинающийся с буллета (или почти только из буллетов)."""
    stripped = text.strip()
    if not stripped:
        return False
    if len(stripped) > _BULLET_FRAGMENT_MAX_TEXT:
        return False
    if stripped[0] in _BULLETS:
        return True
    # Только пара символов после strip и среди них есть буллеты
    if any(c in _BULLETS for c in stripped) and len(stripped) <= 10:
        return True
    return False


def _toc_fragment_ratio(text: str) -> tuple[int, int]:
    """Возвращает (toc-подобных строк, всего непустых строк) в первых 800 chars.

    Считаем строку ToC-подобной, если она матчится `_TOC_LIKE_LINE` —
    т.е. текст + лидеры (точки / много пробелов / подчёркивания) + число в конце.
    """
    snippet = text[:800]
    lines = [l.strip() for l in snippet.split('\n')]
    lines = [l for l in lines if l]
    if not lines:
        return 0, 0
    toc_like = sum(1 for l in lines if _TOC_LIKE_LINE.match(l))
    return toc_like, len(lines)


def classify_content(text: str) -> str | None:
    """Классифицирует контент секции; возвращает None для OK.

    Возможные значения:
      'whitespace'    — после strip <_MIN_MEANINGFUL_CHARS значимых символов
      'dots'          — leader-dot заливка, точек среди не-пробельных >50%
      'bullet'        — короткий буллет-фрагмент
      'toc_fragment'  — несколько строк выглядят как пункты оглавления
      None            — содержание выглядит реальным
    """
    if not text:
        return None  # пусто — это не «junk», это просто отсутствие контента
    stripped = _strip_text(text)
    if not stripped:
        return 'whitespace'  # был контент, но всё пробелы — это и есть мусор

    # Bullet-фрагмент проверяем ПЕРВЫМ — он короткий по определению и должен
    # ловиться раньше whitespace-проверки длины.
    if _looks_bullet_fragment(stripped):
        return 'bullet'

    if len(stripped) < _MIN_MEANINGFUL_CHARS:
        return 'whitespace'

    # Leader-dots
    if _dots_fraction(stripped) > _DOTS_FRACTION_THRESHOLD:
        return 'dots'

    # ToC-фрагмент: несколько строк «текст ....... число»
    toc_like, total_lines = _toc_fragment_ratio(stripped)
    if (toc_like >= _TOC_FRAGMENT_MIN_LINES
            and toc_like / max(total_lines, 1) >= _TOC_FRAGMENT_MIN_FRACTION):
        return 'toc_fragment'

    return None


def count_junk_sections(flat_nodes: list) -> dict:
    """Считает разбивку junk-секций по причинам.

    flat_nodes: список словарей с ключом 'content'.
    Возвращает: {'total': N, 'dots': N, 'bullet': N, 'toc_fragment': N, 'whitespace': N}.
    Не считает секции с пустым контентом — они не junk, у них нет тела.
    """
    out = {'total': 0, 'dots': 0, 'bullet': 0, 'toc_fragment': 0, 'whitespace': 0}
    for node in flat_nodes:
        c = node.get('content') or ''
        reason = classify_content(c)
        if reason:
            out['total'] += 1
            out[reason] = out.get(reason, 0) + 1
    return out
