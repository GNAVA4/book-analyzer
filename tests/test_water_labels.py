"""Вода (хранение и сводки) и разметка — без LM Studio."""
import json
import time

from app.analysis import labels
from app.analysis.article_report import analyze_article
from app.analysis.water import WATERY_GENERAL_SHARE, eligible, lookup, profile, text_hash
from tests.test_corpus import MATH, doc, store  # noqa: F401 — фикстура store


def put_claims(db, text, claims):
    lookup(db, [])                                       # создаёт таблицу
    db.execute('INSERT OR REPLACE INTO para_claims VALUES (?,?,?,?,?)',
               (text_hash(text), 'test', len(text), json.dumps(claims, ensure_ascii=False), time.time()))
    db.commit()


def test_profile_watery_flag():
    general = [{'claim': 'Это актуально', 'type': 'общее'}, {'claim': 'В статье рассмотрено', 'type': 'связка'}]
    dense = [{'claim': 'Метод сходится', 'type': 'результат'}, {'claim': 'Норма ограничена', 'type': 'факт'}]
    assert profile(general, 400)['watery'] and not profile(dense, 400)['watery']
    mixed = profile(general[:1] + dense, 400)
    assert mixed['general_share'] < WATERY_GENERAL_SHARE and mixed['density'] == 5.0


def test_eligible():
    assert not eligible('Коротко.') and eligible(MATH * 3)


def test_report_attaches_water_and_substantive_novelty(store):  # noqa: F811
    p0, p1 = MATH * 3, MATH * 2 + 'Это очень актуально и важно для дальнейших исследований. ' * 4
    put_claims(store.db, p0, [{'claim': 'x', 'type': 'факт'}])
    put_claims(store.db, p1, [{'claim': 'y', 'type': 'общее'}, {'claim': 'z', 'type': 'связка'}])
    rep = analyze_article(doc('Новая', p0, p1), store, 'cl')
    a, b = rep['paragraphs']
    assert a['water'] and not a['water']['watery'] and b['water']['watery']
    s = rep['summary']
    assert s['water_checked'] == 2 and 0 < s['watery_share'] < 1
    assert s['novelty_substantive'] == a['novelty']      # водянистый абзац в новизну не входит


def test_labels_generate_once_and_save(store):  # noqa: F811
    n1 = labels.generate(store, ['cl'])
    n2 = labels.generate(store, ['cl'])
    assert n1 == n2 and n1.get('water', 0) > 0
    its = labels.items(store.db, 'water', 'Аня')
    assert its and all(i['value'] is None for i in its)
    labels.save(store.db, its[0]['id'], 'Аня', 'dense')
    labels.save(store.db, its[0]['id'], 'Аня', 'water')          # повторный ответ заменяет
    assert labels.items(store.db, 'water', 'Аня')[0]['value'] == 'water'
    assert labels.items(store.db, 'water', 'Боря')[0]['value'] is None
    rep = labels.report(store.db)
    assert rep['annotators'] == ['Аня'] and rep['water'][0]['human'] == 'water'
