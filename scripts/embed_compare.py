"""Сравнение моделей эмбеддингов на данных проекта (модель должна быть загружена в LM Studio).

    venv/Scripts/python.exe scripts/embed_compare.py MODEL_ID [MODEL_ID ...]

Задачи (тексты фрагментов берутся из data/corpus.db, корпус «КиберЛенинка», ≤PER_DOC на статью):
  1. тема leave-one-out — kNN (k=7, голос по близости центроидов), как corpus.predict_area;
  2. своя/чужая область — лучшая близость фрагмента к статьям своей и чужих областей;
     AUC = вероятность, что «своя» пара ближе «чужой» (0.5 — модель не различает);
  3. пары отчёта: «та же мысль» (self, inner) должны быть ближе, чем «та же тема» (school);
  4. пересказ: ранг синтетического пересказа среди всех фрагментов при запросе исходным абзацем.
Эмбеддинги кэшируются в .cache/embed_compare_<model>.jsonl.
"""
import json
import os
import sqlite3
import sys
import time
from collections import Counter, defaultdict

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding='utf-8')

from app.analysis.semantic import Embedder  # noqa: E402

PER_DOC = 15        # фрагментов на статью: 120 статей × 15 ≈ 1800 текстов — минуты даже для 4B
K = 7


def load():
    db = sqlite3.connect('data/corpus.db')
    rows = db.execute(
        "SELECT d.id, d.area, ch.text FROM chunk ch JOIN document d ON d.id = ch.doc_id "
        "JOIN corpus c ON c.id = d.corpus_id WHERE c.name = 'КиберЛенинка' AND ch.formula = 0 "
        "AND ch.abstract = 0 ORDER BY d.id, ch.idx").fetchall()
    by = defaultdict(list)
    area = {}
    for i, a, t in rows:
        by[i].append(t)
        area[i] = a
    docs = []
    for i, ts in by.items():
        step = max(1, len(ts) // PER_DOC)
        docs.append({'id': i, 'area': area[i], 'texts': ts[::step][:PER_DOC]})
    return docs


def auc(pos, neg):
    pos, neg = np.sort(pos), np.sort(neg)
    return float(np.searchsorted(neg, pos, side='left').mean() / len(neg))


def evaluate(model, docs):
    emb = Embedder(cache_path=f'.cache/embed_compare_{model.replace("/", "_")}.jsonl', model=model)
    texts = [t for d in docs for t in d['texts']]
    t0 = time.time()
    M = emb.embed(texts)
    dt = time.time() - t0
    k = 0
    for d in docs:
        d['M'] = M[k:k + len(d['texts'])]
        k += len(d['texts'])
        c = d['M'].mean(axis=0)
        d['c'] = c / np.linalg.norm(c)
    # 1. тема
    hit = 0
    for d in docs:
        sims = sorted(((float(e['c'] @ d['c']), e['area']) for e in docs if e['id'] != d['id']), reverse=True)[:K]
        votes = Counter()
        for s, a in sims:
            votes[a] += max(s, 0)
        hit += votes.most_common(1)[0][0] == d['area']
    # 2. своя/чужая
    own, oth = [], []
    for d in docs:
        same = np.vstack([e['M'] for e in docs if e['id'] != d['id'] and e['area'] == d['area']])
        diff = np.vstack([e['M'] for e in docs if e['area'] != d['area']])
        own += list((d['M'] @ same.T).max(axis=1))
        oth += list((d['M'] @ diff.T).max(axis=1))
    own, oth = np.array(own), np.array(oth)
    # 3. пары
    pairs = json.load(open('.cache/report/pairs.json', encoding='utf-8'))
    P = emb.embed([p['a_text'] for p in pairs] + [p['b_text'] for p in pairs])
    n = len(pairs)
    psim = {p['id']: float(P[i] @ P[n + i]) for i, p in enumerate(pairs)}
    kinds = defaultdict(list)
    for p in pairs:
        kinds[p['kind']].append(psim[p['id']])
    # 4. пересказ
    gen = json.load(open('.cache/synthetic_gen.json', encoding='utf-8'))
    S = emb.embed([gen['source'], gen['paraphrase'], gen['water']])
    pool = np.vstack([M, S[1:2]])                  # пересказ среди всех фрагментов корпуса
    rank = int((pool @ S[0] > float(S[1] @ S[0])).sum()) + 1
    return {
        'model': model, 'texts': len(texts), 'embed_sec': round(dt, 1),
        'topic_loo': f'{hit}/{len(docs)} = {hit / len(docs):.0%}',
        'own_median': round(float(np.median(own)), 3), 'other_median': round(float(np.median(oth)), 3),
        'other_p90': round(float(np.percentile(oth, 90)), 3), 'own_vs_other_auc': round(auc(own, oth), 3),
        'pairs': {k: [round(x, 3) for x in v] for k, v in kinds.items()},
        'thought_minus_topic': round(float(np.mean(kinds['self']) - np.mean(kinds['school'])), 3),
        'paraphrase_rank': rank, 'paraphrase_sim': round(float(S[1] @ S[0]), 3),
        'water_sim': round(float(S[2] @ S[0]), 3),
    }


if __name__ == '__main__':
    docs = load()
    out = []
    for m in sys.argv[1:]:
        r = evaluate(m, docs)
        out.append(r)
        print(json.dumps(r, ensure_ascii=False))
    path = '.cache/report/embed_compare.json'
    old = json.load(open(path, encoding='utf-8')) if os.path.exists(path) else []
    old = [o for o in old if o['model'] not in {r['model'] for r in out}] + out
    json.dump(old, open(path, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
