"""
Хранилище корпусов и смысловой индекс — основа сервиса анализа статей.

Модель данных (SQLite, один файл):
  corpus    — ИМЕНОВАННЫЙ корпус («Мивары», «Курсовые 2026», «КиберЛенинка») [user, session 020];
  document  — статья корпуса: разбор (JSON парсера), метаданные, ТЕМА (область наук);
  chunk     — фрагмент 300–1500 знаков (semantic.make_chunks) + эмбеддинг (float32 BLOB).

Тема документа (area) — область наук. Источник:
  'label'    — метка источника (рубрика КиберЛенинки = классификатор ОЭСР) или указана при загрузке;
  'auto'     — предсказана по ближайшим размеченным документам (predict_area).
Сравнение новой статьи идёт не со всем корпусом, а с документами той же темы и из них —
с ближайшими по смыслу (neighbours): статья по моделированию не должна «быть новой»
относительно статей по менеджменту только потому, что тема другая.

Индекс — полный перебор по матрице numpy: 100 тыс. фрагментов × 1024 = 400 МБ, умножение —
миллисекунды; отдельная векторная БД на таком объёме не нужна.
"""
import json
import os
import sqlite3
import time

import numpy as np

from app.analysis.semantic import Embedder, coverage, make_chunks

DB_PATH = 'data/corpus.db'
# Ближайших документов, с которыми сравнивается статья. 10 — компромисс: меньше — новизна
# зависит от случайного состава, больше — в сравнение попадают статьи «рядом с темой».
NEIGHBOURS = 10
# Предварительный отбор кандидатов по центроидам перед точным coverage (дорогой шаг).
PREFILTER = 40
# k ближайших размеченных документов для голосования за тему.
AREA_K = 7

_SCHEMA = """
CREATE TABLE IF NOT EXISTS corpus (
  id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, description TEXT DEFAULT '', created REAL);
CREATE TABLE IF NOT EXISTS document (
  id INTEGER PRIMARY KEY, corpus_id INTEGER NOT NULL REFERENCES corpus(id) ON DELETE CASCADE,
  name TEXT NOT NULL, title TEXT, authors TEXT, year INTEGER, url TEXT,
  area TEXT, area_source TEXT, area_conf REAL,
  n_chars INTEGER, lang TEXT, parsed TEXT, added REAL,
  UNIQUE(corpus_id, name));
CREATE TABLE IF NOT EXISTS chunk (
  id INTEGER PRIMARY KEY, doc_id INTEGER NOT NULL REFERENCES document(id) ON DELETE CASCADE,
  idx INTEGER, section TEXT, text TEXT, formula INTEGER, abstract INTEGER, paras TEXT, emb BLOB);
CREATE INDEX IF NOT EXISTS chunk_doc ON chunk(doc_id);
"""


def doc_vectors(chunks: list, M: np.ndarray) -> np.ndarray:
    """Фрагменты, участвующие в метриках: без формул (их вектор — «похожесть мусора»)."""
    keep = np.array([not c['formula'] for c in chunks], dtype=bool)
    return M[keep] if len(M) else M


def centroid(Mk: np.ndarray) -> np.ndarray:
    if not len(Mk):
        return np.zeros(0, dtype=np.float32)
    v = Mk.mean(axis=0)
    return v / (np.linalg.norm(v) + 1e-9)


class CorpusStore:
    def __init__(self, path: str = DB_PATH, embedder: Embedder = None):
        os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA foreign_keys = ON')
        self.db.executescript(_SCHEMA)
        self._emb = embedder
        self._cache = {}          # corpus_id → загруженный индекс

    @property
    def embedder(self) -> Embedder:
        if self._emb is None:
            self._emb = Embedder()
        return self._emb

    # --- корпуса ---------------------------------------------------------
    def corpus_id(self, name: str, create: bool = False) -> int:
        row = self.db.execute('SELECT id FROM corpus WHERE name=?', (name,)).fetchone()
        if row:
            return row['id']
        if not create:
            raise KeyError(f'нет корпуса «{name}»')
        cur = self.db.execute('INSERT INTO corpus(name, created) VALUES (?, ?)', (name, time.time()))
        self.db.commit()
        return cur.lastrowid

    def corpora(self) -> list:
        rows = self.db.execute(
            'SELECT c.name, c.description, COUNT(d.id) AS n FROM corpus c '
            'LEFT JOIN document d ON d.corpus_id = c.id GROUP BY c.id ORDER BY c.name').fetchall()
        out = []
        for r in rows:
            areas = self.db.execute(
                'SELECT area, area_source, COUNT(*) AS n FROM document d JOIN corpus c ON c.id=d.corpus_id '
                'WHERE c.name=? GROUP BY area, area_source ORDER BY n DESC', (r['name'],)).fetchall()
            out.append({'name': r['name'], 'description': r['description'], 'documents': r['n'],
                        'areas': [dict(a) for a in areas]})
        return out

    def documents(self, corpus: str) -> list:
        rows = self.db.execute(
            'SELECT d.id, d.name, d.title, d.year, d.area, d.area_source, d.area_conf, d.n_chars, d.url '
            'FROM document d JOIN corpus c ON c.id = d.corpus_id WHERE c.name=? ORDER BY d.area, d.title',
            (corpus,)).fetchall()
        return [dict(r) for r in rows]

    # --- добавление документа -------------------------------------------
    def add_document(self, corpus: str, name: str, parsed: dict, area: str = None,
                     meta: dict = None) -> dict:
        """Разбор → фрагменты → эмбеддинги → в корпус. Тема: явная метка или автопредсказание."""
        meta = meta or {}
        cid = self.corpus_id(corpus, create=True)
        chunks = make_chunks(parsed)
        M = self.embedder.embed([c['text'] for c in chunks]) if chunks else np.zeros((0, 1))
        area_source, area_conf = 'label', 1.0
        if not area:
            pred = self.predict_area(corpus, doc_vectors(chunks, M))
            area, area_conf, area_source = pred['area'], pred['confidence'], 'auto'
        n_chars = sum(len(t) for s in parsed['sections'] for t in s['paragraphs'])
        self.db.execute('DELETE FROM document WHERE corpus_id=? AND name=?', (cid, name))
        cur = self.db.execute(
            'INSERT INTO document(corpus_id, name, title, authors, year, url, area, area_source, area_conf,'
            ' n_chars, lang, parsed, added) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (cid, name, meta.get('title') or parsed['meta'].get('title') or name,
             json.dumps(parsed['meta'].get('authors', []), ensure_ascii=False), meta.get('year'),
             meta.get('url'), area, area_source, area_conf, n_chars, parsed.get('lang', 'ru'),
             json.dumps(parsed, ensure_ascii=False), time.time()))
        doc_id = cur.lastrowid
        self.db.executemany(
            'INSERT INTO chunk(doc_id, idx, section, text, formula, abstract, paras, emb) VALUES (?,?,?,?,?,?,?,?)',
            [(doc_id, c['idx'], c['section'], c['text'], int(c['formula']), int(c['abstract']),
              json.dumps(c['paras']), M[i].astype(np.float32).tobytes()) for i, c in enumerate(chunks)])
        self.db.commit()
        self._cache.pop(cid, None)
        return {'id': doc_id, 'area': area, 'area_source': area_source, 'area_conf': area_conf,
                'chunks': len(chunks)}

    def set_area(self, doc_id: int, area: str):
        self.db.execute("UPDATE document SET area=?, area_source='label', area_conf=1 WHERE id=?",
                        (area, doc_id))
        self.db.commit()
        self._cache.clear()

    def delete_document(self, doc_id: int):
        self.db.execute('DELETE FROM document WHERE id=?', (doc_id,))
        self.db.commit()
        self._cache.clear()

    # --- индекс ---------------------------------------------------------
    def index(self, corpus: str) -> dict:
        """Все документы корпуса в памяти: метаданные, векторы фрагментов, центроиды."""
        cid = self.corpus_id(corpus)
        if cid in self._cache:
            return self._cache[cid]
        docs = {r['id']: {**dict(r), 'chunks': [], 'vecs': []} for r in self.db.execute(
            'SELECT id, name, title, year, url, area, area_source, parsed FROM document WHERE corpus_id=?',
            (cid,)).fetchall()}
        for r in self.db.execute(
                'SELECT ch.doc_id, ch.idx, ch.section, ch.text, ch.formula, ch.emb FROM chunk ch '
                'JOIN document d ON d.id = ch.doc_id WHERE d.corpus_id=? ORDER BY ch.doc_id, ch.idx', (cid,)):
            if r['formula']:
                continue
            d = docs[r['doc_id']]
            d['chunks'].append({'idx': r['idx'], 'section': r['section'], 'text': r['text']})
            d['vecs'].append(np.frombuffer(r['emb'], dtype=np.float32))
        for d in docs.values():
            d['Mk'] = np.vstack(d['vecs']) if d['vecs'] else np.zeros((0, 1), dtype=np.float32)
            d['c'] = centroid(d['Mk'])
            del d['vecs']
        idx = {'docs': docs}
        self._cache[cid] = idx
        return idx

    def predict_area(self, corpus: str, Mk: np.ndarray, exclude: int = None) -> dict:
        """Тема по k ближайшим РАЗМЕЧЕННЫМ документам (голос, взвешенный близостью центроидов).
        Области наук — общий словарь, поэтому размеченные документы берутся из ВСЕХ корпусов:
        корпус «Курсовые» без меток получает тему по размеченному корпусу КиберЛенинки."""
        c = centroid(Mk)
        labeled = [d for name in [r['name'] for r in self.db.execute('SELECT name FROM corpus')]
                   for d in self.index(name)['docs'].values()
                   if d['area_source'] == 'label' and len(d['c']) and d['id'] != exclude]
        if not len(c) or not labeled:
            return {'area': None, 'confidence': 0.0, 'votes': {}}
        sims = sorted(((float(d['c'] @ c), d['area']) for d in labeled), reverse=True)[:AREA_K]
        votes = {}
        for s, a in sims:
            votes[a] = votes.get(a, 0.0) + max(s, 0.0)
        total = sum(votes.values()) or 1.0
        area = max(votes, key=votes.get)
        return {'area': area, 'confidence': round(votes[area] / total, 3),
                'votes': {a: round(v / total, 3) for a, v in sorted(votes.items(), key=lambda x: -x[1])}}

    def neighbours(self, corpus: str, Mk: np.ndarray, area: str = None, scope: str = 'topic',
                   k: int = NEIGHBOURS, exclude: int = None) -> list:
        """Документы корпуса для сравнения.
        scope='topic'   — только документы той же темы, из них k ближайших;
        scope='nearest' — k ближайших по всему корпусу (тема не учитывается);
        scope='corpus'  — весь корпус.
        Ближайшие — по coverage(статья → документ) среди PREFILTER лучших по центроиду."""
        docs = [d for d in self.index(corpus)['docs'].values() if len(d['Mk']) and d['id'] != exclude]
        if scope == 'topic' and area:
            docs = [d for d in docs if d['area'] == area]
        if scope == 'corpus' or not len(Mk):
            return [(d, coverage(Mk, d['Mk']) if len(Mk) else 0.0) for d in docs]
        c = centroid(Mk)
        docs = sorted(docs, key=lambda d: -float(d['c'] @ c))[:PREFILTER]
        scored = sorted(((d, coverage(Mk, d['Mk'])) for d in docs), key=lambda x: -x[1])
        return scored[:k]
