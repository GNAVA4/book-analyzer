"""Калибровка корпуса: угадывание темы и порог «есть опора» (SUPPORT_SIM).

    venv/Scripts/python.exe scripts/corpus_calibrate.py CORPUS [--out .cache/report/calibration.json]
Модель не нужна — векторы лежат в БД.

1. Тема leave-one-out: каждой размеченной статье предсказываем тему по остальным
   (corpus.predict_area с exclude) — точность по областям и матрица ошибок.
2. Для каждого фрагмента размеченной статьи — лучшая близость к фрагментам ДРУГИХ статей
   своей области и чужих областей. Распределение «чужих» — уровень «не по теме»: если лучший
   фрагмент своей темы не выше его, сравнивать не с чем.
"""
import json
import os
import sys
from collections import Counter, defaultdict

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding='utf-8')

from app.analysis.corpus import CorpusStore  # noqa: E402

argv = sys.argv[1:]
out = None
if '--out' in argv:
    k = argv.index('--out')
    out = argv[k + 1]
    argv = argv[:k] + argv[k + 2:]
corpus = argv[0]
st = CorpusStore()
docs = [d for d in st.index(corpus)['docs'].values() if d['area_source'] == 'label' and len(d['Mk'])]
print(f'Размеченных документов: {len(docs)}; по областям: {dict(Counter(d["area"] for d in docs))}\n')

# --- 1. тема ---
conf = defaultdict(Counter)
for d in docs:
    p = st.predict_area(corpus, d['Mk'], exclude=d['id'])
    conf[d['area']][p['area']] += 1
total = sum(sum(c.values()) for c in conf.values())
hit = sum(conf[a][a] for a in conf)
print(f'Тема leave-one-out: {hit}/{total} = {hit / total:.0%}')
for a, c in sorted(conf.items()):
    print(f'  {a:40s} {c[a]}/{sum(c.values())}  ошибки: {dict((k, v) for k, v in c.items() if k != a)}')

# --- 2. близости фрагментов ---
own, other = [], []
for d in docs:
    same = [e['Mk'] for e in docs if e['id'] != d['id'] and e['area'] == d['area']]
    diff = [e['Mk'] for e in docs if e['area'] != d['area']]
    if same:
        own += list((d['Mk'] @ np.vstack(same).T).max(axis=1))
    if diff:
        other += list((d['Mk'] @ np.vstack(diff).T).max(axis=1))
own, other = np.array(own), np.array(other)
q = [5, 10, 25, 50, 75, 90, 95]
print('\nЛучшая близость фрагмента (перцентили ' + ', '.join(map(str, q)) + '):')
print('  своя область :', ' '.join(f'{v:.3f}' for v in np.percentile(own, q)))
print('  чужие области:', ' '.join(f'{v:.3f}' for v in np.percentile(other, q)))
for thr in (0.55, 0.60, 0.65, 0.70):
    print(f'  порог {thr:.2f}: своя область ниже — {np.mean(own < thr):.0%}, чужие выше — {np.mean(other >= thr):.0%}')

if out:
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, 'w', encoding='utf-8') as f:
        json.dump({'area_loo': {'hit': hit, 'total': total,
                                'confusion': {a: dict(c) for a, c in conf.items()}},
                   'own_pct': dict(zip(q, np.percentile(own, q).round(3).tolist())),
                   'other_pct': dict(zip(q, np.percentile(other, q).round(3).tolist()))},
                  f, ensure_ascii=False, indent=1)
