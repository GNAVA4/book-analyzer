"""Лексический анализ корпуса разобранных статей (JSON из scripts/parse_articles.py).

    venv/Scripts/python.exe scripts/analyze_articles.py [PARSED_DIR]
Печатает: самоцитирование, долю текста, повторённого в других статьях корпуса,
повторы внутри статьи и самые сильные межстатейные совпадения.
"""
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding='utf-8')

from app.analysis.lexical import body_units, find_reuse, reused_share, self_citation_share  # noqa: E402

parsed_dir = sys.argv[1] if len(sys.argv) > 1 else 'articles_parsed'
docs = []
for path in sorted(glob.glob(os.path.join(parsed_dir, '*.json'))):
    with open(path, encoding='utf-8') as f:
        d = json.load(f)
    d['_name'] = os.path.splitext(os.path.basename(path))[0][:28]
    d['_units'] = body_units(d)
    docs.append(d)


def short(t, n=110):
    t = t.replace('\n', ' ')
    return t if len(t) <= n else t[:n] + '…'


print('=== Сводка по статьям ===')
print(f'{"статья":30} {"год":>5} {"абз":>4} {"самоцит.":>9} {"повтор в корпусе":>17} {"повторов внутри":>16}')
for d in docs:
    others = [o['_units'] for o in docs if o is not d]
    inner = find_reuse(d['_units'], d['_units'], same_doc=True)
    year = ''
    for p in d['paragraphs'][:15]:
        import re
        m = re.search(r'\b(19|20)\d{2}\b', p['text'])
        if m:
            year = m.group(0)
            break
    print(f'{d["_name"]:30} {year:>5} {len(d["_units"]):>4} {self_citation_share(d):>9.0%} '
          f'{reused_share(d["_units"], others):>17.0%} {len(inner):>16}')

print('\n=== Повторы внутри статьи (топ-3 на статью) ===')
for d in docs:
    inner = find_reuse(d['_units'], d['_units'], same_doc=True)
    if not inner:
        continue
    print(f'-- {d["_name"]}')
    for i, j, c_ij, c_ji in inner[:3]:
        u, v = d['_units'][i], d['_units'][j]
        print(f'   [{max(c_ij, c_ji):.2f}] §«{short(u["section"], 30)}» ≈ §«{short(v["section"], 30)}»')
        print(f'      A: {short(u["text"])}')
        print(f'      B: {short(v["text"])}')

print('\n=== Межстатейные совпадения (топ-12) ===')
allp = []
for a in range(len(docs)):
    for b in range(a + 1, len(docs)):
        for i, j, c_ij, c_ji in find_reuse(docs[a]['_units'], docs[b]['_units'], same_doc=False):
            allp.append((max(c_ij, c_ji), a, b, i, j))
allp.sort(reverse=True)
for c, a, b, i, j in allp[:12]:
    u, v = docs[a]['_units'][i], docs[b]['_units'][j]
    print(f'[{c:.2f}] {docs[a]["_name"]} ↔ {docs[b]["_name"]}')
    print(f'      A: {short(u["text"])}')
    print(f'      B: {short(v["text"])}')
print(f'\nвсего межстатейных пар с повтором ≥ порога: {len(allp)}')
