"""
Ручная разметка для калибровки (страница static/label.html).

Два набора примеров (фиксируются в БД один раз, чтобы ответы разных людей были сопоставимы):
  pairs — пары фрагментов разных статей (и повторы внутри статьи) с близостью 0.70–0.95,
          поровну по корзинам близости. Вопрос: та же мысль / та же тема / разное.
  water — абзацы (≥200 знаков, не формулы). Вопрос: вода / по делу / смешано.
Ответ модели разметчику не показывается (чтобы не подсказывать); сравнение — в report().
"""
import json
import random
import time
from collections import defaultdict

import numpy as np

from app.analysis.water import eligible, lookup as water_lookup

PAIR_BINS = [(0.70, 0.75), (0.75, 0.80), (0.80, 0.85), (0.85, 0.90), (0.90, 1.01)]
PAIRS_PER_BIN = 8
INNER_PAIRS = 10
WATER_ITEMS = 40
SEED = 42
LABELS = {
    'pairs': [('same', 'Та же мысль'), ('topic', 'Та же тема, мысли разные'), ('diff', 'Разное'), ('unsure', 'Не могу сказать')],
    'water': [('water', 'Вода'), ('mixed', 'Смешано'), ('dense', 'По делу'), ('unsure', 'Не могу сказать')],
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS label_item (
  id INTEGER PRIMARY KEY, kind TEXT, payload TEXT, created REAL);
CREATE TABLE IF NOT EXISTS label (
  id INTEGER PRIMARY KEY, item_id INTEGER REFERENCES label_item(id), annotator TEXT,
  value TEXT, created REAL, UNIQUE(item_id, annotator));
"""


def init(db):
    db.executescript(_SCHEMA)


def _docs(store, corpora, min_chunks=2):
    out = []
    for name in corpora:
        try:
            out += [(name, d) for d in store.index(name)['docs'].values() if len(d['Mk']) >= min_chunks]
        except KeyError:
            continue
    return out


def generate(store, corpora: list) -> dict:
    """Создаёт наборы, если их ещё нет (повторный вызов ничего не меняет)."""
    init(store.db)
    have = {k: n for k, n in store.db.execute('SELECT kind, COUNT(*) FROM label_item GROUP BY kind')}
    rnd = random.Random(SEED)
    docs = _docs(store, corpora)
    if not have.get('pairs'):
        cand, inner = defaultdict(list), []
        for ci, (cname, d) in enumerate(docs):
            others = [(c, e) for c, e in docs if c == cname and e['id'] != d['id']]
            if others:
                R = np.vstack([e['Mk'] for _, e in others])
                owner = [(e, k) for _, e in others for k in range(len(e['Mk']))]
                S = d['Mk'] @ R.T
                for i in range(len(d['Mk'])):
                    j = int(np.argmax(S[i]))
                    s = float(S[i, j])
                    for lo, hi in PAIR_BINS:
                        if lo <= s < hi:
                            e, k = owner[j]
                            cand[(lo, hi)].append((s, d, i, e, k))
            S = d['Mk'] @ d['Mk'].T
            for i in range(len(d['Mk'])):
                for j in range(i + 2, len(d['Mk'])):
                    if 0.75 <= S[i, j] < 0.97:
                        inner.append((float(S[i, j]), d, i, d, j))
        items = []
        for b in PAIR_BINS:
            pool = cand[b]
            rnd.shuffle(pool)
            seen_docs = set()
            for s, d, i, e, k in pool:                 # не больше одной пары на статью в корзине
                if d['id'] in seen_docs or len([x for x in items if x[0] == b]) >= PAIRS_PER_BIN:
                    continue
                seen_docs.add(d['id'])
                items.append((b, s, d, i, e, k, 'cross'))
        rnd.shuffle(inner)
        for s, d, i, e, k in inner[:INNER_PAIRS]:
            items.append((None, s, d, i, e, k, 'inner'))
        rnd.shuffle(items)
        for b, s, d, i, e, k, where in items:
            payload = {'sim': round(s, 3), 'where': where,
                       'a': {'doc_id': d['id'], 'title': d['title'], 'section': d['chunks'][i]['section'], 'text': d['chunks'][i]['text']},
                       'b': {'doc_id': e['id'], 'title': e['title'], 'section': e['chunks'][k]['section'], 'text': e['chunks'][k]['text']}}
            store.db.execute('INSERT INTO label_item(kind, payload, created) VALUES (?,?,?)',
                             ('pairs', json.dumps(payload, ensure_ascii=False), time.time()))
    if not have.get('water'):
        paras = []
        for cname, d in _docs(store, corpora, min_chunks=0):     # абзацам фрагменты не нужны
            parsed = json.loads(d['parsed'])
            for s in parsed['sections']:
                for t in s['paragraphs']:
                    if eligible(t) and len(t) <= 1500:
                        paras.append({'doc_id': d['id'], 'title': d['title'], 'section': s['title'], 'text': t})
        rnd.shuffle(paras)
        for p in paras[:WATER_ITEMS]:
            store.db.execute('INSERT INTO label_item(kind, payload, created) VALUES (?,?,?)',
                             ('water', json.dumps(p, ensure_ascii=False), time.time()))
    store.db.commit()
    return {k: n for k, n in store.db.execute('SELECT kind, COUNT(*) FROM label_item GROUP BY kind')}


def items(db, kind: str, annotator: str) -> list:
    init(db)
    mine = {i: v for i, v in db.execute('SELECT item_id, value FROM label WHERE annotator=?', (annotator,))}
    out = []
    for i, payload in db.execute('SELECT id, payload FROM label_item WHERE kind=? ORDER BY id', (kind,)):
        p = json.loads(payload)
        p.pop('sim', None)                              # не подсказываем
        out.append({'id': i, 'payload': p, 'value': mine.get(i)})
    return out


def save(db, item_id: int, annotator: str, value: str):
    init(db)
    db.execute('INSERT INTO label(item_id, annotator, value, created) VALUES (?,?,?,?) '
               'ON CONFLICT(item_id, annotator) DO UPDATE SET value=excluded.value, created=excluded.created',
               (item_id, annotator, value, time.time()))
    db.commit()


def report(db) -> dict:
    """Человек против машины: близость по ответам (пары) и флаг «вода» модели (абзацы)."""
    init(db)
    rows = db.execute('SELECT li.kind, li.payload, l.value, l.annotator FROM label l '
                      'JOIN label_item li ON li.id = l.item_id').fetchall()
    pairs, water = defaultdict(list), []
    for kind, payload, value, ann in rows:
        p = json.loads(payload)
        if kind == 'pairs':
            pairs[value].append(p['sim'])
        else:
            water.append((p['text'], value, ann))
    prof = water_lookup(db, [t for t, _, _ in water])
    from app.analysis.water import text_hash
    conf = defaultdict(int)
    for t, v, _ in water:
        pr = prof.get(text_hash(t))
        conf[(v, 'нет проверки' if pr is None else ('вода' if pr['watery'] else 'по делу'))] += 1
    return {
        'annotators': sorted({a for *_, a in rows}),
        'pairs': {v: {'n': len(s), 'sim_mean': round(float(np.mean(s)), 3), 'sim_min': round(min(s), 3),
                      'sim_max': round(max(s), 3)} for v, s in pairs.items()},
        'water': [{'human': h, 'model': m, 'n': n} for (h, m), n in sorted(conf.items())],
        'labels': {k: dict(v) for k, v in LABELS.items()},
    }
