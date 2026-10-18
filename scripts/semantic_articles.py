"""Смысловой анализ корпуса разобранных статей (JSON из scripts/parse_articles.py).

    venv/Scripts/python.exe scripts/semantic_articles.py DIR [DIR ...] [--out report.json]
Нужен LM Studio с text-embedding-qwen3-embedding-0.6b (эмбеддинги кэшируются в .cache/).

Печатает по каждой статье:
  новизна   — средняя (1 − близость к ближайшему фрагменту ДРУГИХ статей корпуса);
  повторы   — доля фрагментов, у которых есть смысловой двойник внутри той же статьи;
  ближайшая — статья корпуса, которая сильнее всего «покрывает» эту (coverage);
и проверку: различает ли модель темы (группа = префикс файла до «__», иначе mivar).
"""
import glob
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding='utf-8')

from app.analysis.lexical import self_citation_share  # noqa: E402
from app.analysis.semantic import Embedder, coverage, make_chunks, novelty, redundant_pairs  # noqa: E402

argv = sys.argv[1:]
out_path = None
if '--out' in argv:
    k = argv.index('--out')
    out_path = argv[k + 1]
    argv = argv[:k] + argv[k + 2:]
args = argv
dirs = args or ['articles_parsed', 'articles_web_parsed']

docs = []
for d_ in dirs:
    for p in sorted(glob.glob(os.path.join(d_, '*.json'))):
        with open(p, encoding='utf-8') as f:
            d = json.load(f)
        ch = [c for c in make_chunks(d)]
        if len([c for c in ch if not c['formula']]) < 2:
            continue
        name = os.path.splitext(os.path.basename(p))[0]
        docs.append({'name': name, 'short': name.split('__')[-1][:24],
                     'group': name.split('__')[0] if '__' in name else 'mivar',
                     'title': d['meta']['title'], 'parsed': d, 'chunks': ch})

emb = Embedder()
for d in docs:
    d['M'] = emb.embed([c['text'] for c in d['chunks']])
    d['keep'] = np.array([not c['formula'] for c in d['chunks']])
    d['Mk'] = d['M'][d['keep']]

n = len(docs)
cov = np.zeros((n, n))
for i in range(n):
    for j in range(n):
        if i != j:
            cov[i, j] = coverage(docs[i]['Mk'], docs[j]['Mk'])

# --- проверка тематической чувствительности ---
same, cross = [], []
for i in range(n):
    for j in range(n):
        if i != j:
            (same if docs[i]['group'] == docs[j]['group'] else cross).append(cov[i, j])
print(f'Проверка: coverage статей одной темы {np.mean(same):.3f} vs разных тем {np.mean(cross):.3f} '
      f'(разрыв {np.mean(same) - np.mean(cross):+.3f})')
hits = 0
for i in range(n):
    j = int(np.argmax(cov[i]))
    hits += docs[j]['group'] == docs[i]['group']
print(f'Ближайшая статья из той же темы: {hits}/{n}\n')

# --- метрики по статьям ---
report = []
for i, d in enumerate(docs):
    others = np.vstack([docs[j]['Mk'] for j in range(n) if j != i])
    nov = novelty(d['Mk'], others)
    pairs = redundant_pairs(d['M'], d['chunks'])
    in_pair = {x for p in pairs for x in p[:2]}
    body = [c for c in d['chunks'] if not c['formula'] and not c['abstract']]
    red_share = len(in_pair) / max(1, len(body))
    j = int(np.argmax(cov[i]))
    kept = [c for c in d['chunks'] if not c['formula']]
    order = np.argsort(nov)
    report.append({
        'name': d['name'], 'group': d['group'], 'title': d['title'],
        'chunks': len(d['chunks']), 'formula_chunks': int((~d['keep']).sum()),
        'novelty_mean': float(nov.mean()), 'novelty_min': float(nov.min()),
        'redundancy_share': red_share, 'self_citation': self_citation_share(d['parsed']),
        'nearest': docs[j]['name'], 'nearest_coverage': float(cov[i, j]),
        'redundant_pairs': [{'a': d['chunks'][a]['text'], 'b': d['chunks'][b]['text'],
                             'a_section': d['chunks'][a]['section'], 'b_section': d['chunks'][b]['section'],
                             'sim': s} for a, b, s in pairs[:5]],
        'least_novel': [{'text': kept[k]['text'], 'novelty': float(nov[k])} for k in order[:2]],
        'most_novel': [{'text': kept[k]['text'], 'novelty': float(nov[k])} for k in order[-2:]],
    })

print(f'{"тема":7} {"статья":25} {"фрагм":>5} {"новизна":>7} {"повторы":>7} {"самоцит":>7}  ближайшая (coverage)')
for r in sorted(report, key=lambda r: (r['group'], -r['novelty_mean'])):
    short = r['name'].split('__')[-1][:24]
    near = r['nearest'].split('__')[-1][:22]
    print(f'{r["group"]:7} {short:25} {r["chunks"]:>5} {r["novelty_mean"]:>7.3f} {r["redundancy_share"]:>7.0%} '
          f'{r["self_citation"]:>7.0%}  {near} ({r["nearest_coverage"]:.2f})')

print('\nСмысловые повторы внутри статей (близость ≥ порога, топ-1 на статью):')
for r in report:
    if r['redundant_pairs']:
        p = r['redundant_pairs'][0]
        print(f'  [{p["sim"]:.2f}] {r["name"][:30]}  §«{p["a_section"][:25]}» ↔ §«{p["b_section"][:25]}»')
        print(f'        A: {p["a"][:120]}')
        print(f'        B: {p["b"][:120]}')

if out_path:
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump({'docs': report, 'coverage': cov.tolist(), 'names': [d['name'] for d in docs]},
                  f, ensure_ascii=False, indent=1)
    print(f'\nотчёт: {out_path}')
