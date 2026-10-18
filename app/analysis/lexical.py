"""
Лексический слой анализа (без моделей) — plan_001 фаза 1b.

Ищет почти дословные повторы фрагментов: внутри статьи и между статьями корпуса.
Метод — шинглы: последовательности из SHINGLE_N «основ» слов (первые STEM_LEN букв —
грубая замена стеммингу, гасит падежные окончания русского). Для пары абзацев
containment(a, b) = |шинглы(a) ∩ шинглы(b)| / |шинглы(a)| — доля абзаца a, найденная в b
(асимметрично: короткий абзац целиком внутри длинного даёт 1.0).

Это ловит ПЕРЕИСПОЛЬЗОВАНИЕ ТЕКСТА (копипаст, слегка правленный), но не пересказ —
пересказ — задача эмбеддингового слоя.
"""
import re
from collections import defaultdict

SHINGLE_N = 5          # 5 слов подряд — случайное совпадение в русском тексте маловероятно
STEM_LEN = 6           # «миварных»/«миварные» → «миварн»
MIN_SHINGLES = 4       # абзацы короче ~8 слов не сравниваем (шум)
REUSE_THRESHOLD = 0.3  # ≥30% шинглов абзаца найдено в другом абзаце → повтор

_WORD = re.compile(r'[а-яёa-z0-9]+', re.I)


def stems(text: str) -> list:
    return [w.lower()[:STEM_LEN] for w in _WORD.findall(text)]


def shingles(text: str) -> set:
    s = stems(text)
    return {tuple(s[i:i + SHINGLE_N]) for i in range(len(s) - SHINGLE_N + 1)}


def body_units(parsed: dict) -> list:
    """Абзацы тела статьи (+ аннотация) — единицы анализа. Шапка/литература/таблицы — нет."""
    units = []
    if parsed['meta'].get('abstract'):
        units.append({'section': 'Аннотация', 'text': parsed['meta']['abstract']})
    for sec in parsed['sections']:
        for t in sec['paragraphs']:
            units.append({'section': sec['title'], 'text': t})
    for i, u in enumerate(units):
        u['idx'] = i
        u['sh'] = shingles(u['text'])
    return units


def containment(a: set, b: set) -> float:
    return len(a & b) / len(a) if a else 0.0


def find_reuse(units_a: list, units_b: list, same_doc: bool) -> list:
    """Пары (i, j, c_ij, c_ji) с containment ≥ порога хотя бы в одну сторону."""
    index = defaultdict(set)                 # шингл → абзацы b, где он есть
    for u in units_b:
        if len(u['sh']) >= MIN_SHINGLES:
            for s in u['sh']:
                index[s].add(u['idx'])
    by_b = {u['idx']: u for u in units_b}
    pairs = []
    for ua in units_a:
        if len(ua['sh']) < MIN_SHINGLES:
            continue
        cand = defaultdict(int)
        for s in ua['sh']:
            for j in index.get(s, ()):
                cand[j] += 1
        for j, hits in cand.items():
            if same_doc and j <= ua['idx']:
                continue
            ub = by_b[j]
            c_ab = hits / len(ua['sh'])
            c_ba = hits / len(ub['sh'])
            if max(c_ab, c_ba) >= REUSE_THRESHOLD:
                pairs.append((ua['idx'], j, c_ab, c_ba))
    return sorted(pairs, key=lambda p: -max(p[2], p[3]))


def reused_share(units: list, others: list) -> float:
    """Доля символов тела, которые почти дословно встречаются в других документах корпуса."""
    total = sum(len(u['text']) for u in units) or 1
    covered = set()
    for other in others:
        for i, _j, c_ab, _c in find_reuse(units, other, same_doc=False):
            if c_ab >= REUSE_THRESHOLD:
                covered.add(i)
    return sum(len(units[i]['text']) for i in covered) / total


def self_citation_share(parsed: dict) -> float:
    """Доля пунктов литературы, где среди авторов есть автор статьи (по фамилии)."""
    refs = parsed['meta'].get('references') or []
    surnames = set()
    for a in parsed['meta'].get('authors', []):
        for w in re.findall(r'[А-ЯЁ][а-яё]{2,}|[А-ЯЁ]{3,}', a):
            surnames.add(w.lower()[:STEM_LEN])
    if not refs or not surnames:
        return 0.0
    hit = sum(1 for r in refs if surnames & {w.lower()[:STEM_LEN] for w in re.findall(r'[А-ЯЁA-Z][а-яёa-z]{2,}', r)})
    return hit / len(refs)
