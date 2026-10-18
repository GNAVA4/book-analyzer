"""Хранилище корпусов и анализ статьи по абзацам — без LM Studio (подставной эмбеддер)."""
import hashlib

import numpy as np
import pytest

from app.analysis.article_report import analyze_article
from app.analysis.corpus import CorpusStore
from app.analysis.lexical import stems
from app.analysis.semantic import make_chunks


class BagEmbedder:
    """Мешок основ слов в 256 корзин: тексты на одну тему близки, на разные — далеки."""
    def embed(self, texts):
        m = np.zeros((len(texts), 256), dtype=np.float32)
        for i, t in enumerate(texts):
            for s in stems(t):
                m[i, int(hashlib.md5(s.encode()).hexdigest(), 16) % 256] += 1
        m /= np.linalg.norm(m, axis=1, keepdims=True) + 1e-9
        return m


ECON = ('Рыночная экономика предприятия зависит от спроса, предложения, цены товара, издержек '
        'производства, прибыли фирмы, инвестиций капитала и конкуренции на рынке. ')
MATH = ('Дифференциальное уравнение второго порядка имеет решение, если функция непрерывна, '
        'интеграл сходится, оператор ограничен, а норма производной убывает на отрезке. ')


def doc(title, *paras):
    return {'meta': {'title': title, 'authors': [], 'abstract': '', 'references': []},
            'sections': [{'title': 'Введение', 'level': 1, 'paragraphs': list(paras)}], 'lang': 'ru'}


@pytest.fixture
def store(tmp_path):
    s = CorpusStore(str(tmp_path / 'c.db'), embedder=BagEmbedder())
    for k in range(3):
        s.add_document('cl', f'econ{k}', doc(f'Экономика {k}', ECON * 3, ECON * 2 + 'Вывод. ' * k), area='Экономика')
        s.add_document('cl', f'math{k}', doc(f'Математика {k}', MATH * 3, MATH * 2 + 'Итог. ' * k), area='Математика')
    return s


def test_chunks_track_paragraphs():
    d = doc('t', ECON * 3, 'Коротко.', MATH * 3)
    ch = make_chunks(d)
    covered = sorted({g for c in ch for g in c['paras']})
    assert covered == [0, 1, 2]          # короткий абзац вошёл во фрагмент соседа


def test_corpora_listing(store):
    cs = store.corpora()
    assert cs[0]['name'] == 'cl' and cs[0]['documents'] == 6
    assert {a['area'] for a in cs[0]['areas']} == {'Экономика', 'Математика'}


def test_predict_area_and_auto_label(store):
    r = store.add_document('курсовые', 'kurs', doc('Курсовая', MATH * 4))
    assert r['area'] == 'Математика' and r['area_source'] == 'auto'   # метка из другого корпуса
    assert r['area_conf'] > 0.5


def test_neighbours_respect_topic(store):
    M = BagEmbedder().embed([MATH * 3])
    topic = store.neighbours('cl', M, area='Математика', scope='topic')
    assert topic and all(d['area'] == 'Математика' for d, _ in topic)
    every = store.neighbours('cl', M, scope='corpus')
    assert len(every) == 6


def test_analyze_article_per_paragraph(store):
    art = doc('Новая', MATH * 3, 'Совершенно иная мысль про садоводство, полив, грядки и урожай томатов. ' * 6)
    rep = analyze_article(art, store, 'cl')
    assert rep['area']['area'] == 'Математика'
    assert all(n['area'] == 'Математика' for n in rep['neighbours'])
    p0, p1 = rep['paragraphs']
    assert p0['support'] and p0['nearest']['area'] == 'Математика'
    assert p1['support'] is False                      # не с чем сравнить — не «новизна»
    assert rep['summary']['no_support'] == 0.5
    assert p0['verbatim']                               # дословно совпадает с документами корпуса


def test_exclude_self(store):
    d = store.documents('cl')[0]
    import json
    parsed = json.loads(store.db.execute('SELECT parsed FROM document WHERE id=?', (d['id'],)).fetchone()[0])
    rep = analyze_article(parsed, store, 'cl', exclude=d['id'])
    assert d['id'] not in [n['doc_id'] for n in rep['neighbours']]


def test_year_filter(store):
    import json as _json
    for k, y in enumerate((2010, 2020, None)):
        store.add_document('cl', f'y{k}', doc(f'Год {y}', MATH * 3, MATH * 2), area='Математика', meta={'year': y})
    M = BagEmbedder().embed([MATH * 3])
    years = lambda **kw: sorted(str(d['year']) for d, _ in store.neighbours('cl', M, scope='corpus', **kw) if d['name'].startswith('y'))
    assert years(year_max=2015) == ['2010', 'None']
    assert years(year_max=2015, include_undated=False) == ['2010']
    assert years() == ['2010', '2020', 'None']
    rep = analyze_article(doc('Новая', MATH * 3), store, 'cl', year=2015)
    assert rep['time']['mode'] == 'before' and rep['time']['excluded_later'] >= 1
    assert analyze_article(doc('Новая', MATH * 3), store, 'cl', year=2015, time_mode='all')['time']['mode'] == 'all'
