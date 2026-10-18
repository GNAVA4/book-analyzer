"""
Анализ одной статьи относительно именованного корпуса — по абзацам, разделам и статье целиком.

Быстрый проход (секунды, только эмбеддинги и шинглы; LLM не нужна):
  1. тема статьи: указана или предсказана по ближайшим размеченным документам корпуса;
  2. с чем сравниваем: NEIGHBOURS ближайших документов той же темы (corpus.neighbours);
  3. по фрагментам → абзацам:
     - близость к корпусу = max cos к фрагментам соседей; новизна = 1 − близость;
     - опора: если даже ближайший фрагмент корпуса далёк (< SUPPORT_SIM) — «не с чем сравнить»,
       новизну такого абзаца не трактуем как новизну (session 019: новизна ≈ одиночество);
     - ближайший фрагмент корпуса (документ + цитата) — чтобы человек проверил сам;
     - смысловой повтор внутри статьи (semantic.redundant_pairs);
     - дословные совпадения с соседями и внутри статьи (lexical.find_reuse).
Метрики считаются по фрагментам (300–1500 знаков), показываются по абзацам: абзац получает
значения своего фрагмента (короткие абзацы склеены с соседями — помечено `shared_chunk`).
"""
import json

import numpy as np

from app.analysis.corpus import CorpusStore, doc_vectors
from app.analysis.lexical import REUSE_THRESHOLD, body_units, find_reuse, self_citation_share
from app.analysis.semantic import is_formula, make_chunks, redundant_pairs
from app.analysis.water import eligible as water_eligible, lookup as water_lookup, text_hash

# Порог «есть опора в корпусе» для фрагмента. Калибровка session 020 (scripts/corpus_calibrate.py,
# 119 статей КиберЛенинки, 7 областей): лучшая близость фрагмента к статьям ЧУЖИХ областей —
# медиана 0.487, p90 0.561; к своей области — медиана 0.528. Ниже p90 «чужих» совпадение не
# отличить от текста на постороннюю тему → «не с чем сравнить», а не «ново».
SUPPORT_SIM = 0.56
SNIPPET = 320


def _para_list(parsed: dict) -> list:
    out = []
    for si, sec in enumerate(parsed['sections']):
        for t in sec['paragraphs']:
            out.append({'i': len(out), 'section': si, 'text': t})
    return out


def analyze_article(parsed: dict, store: CorpusStore, corpus: str, scope: str = 'topic',
                    area: str = None, exclude: int = None) -> dict:
    chunks = make_chunks(parsed)
    M = store.embedder.embed([c['text'] for c in chunks]) if chunks else np.zeros((0, 1024), np.float32)
    keep = [i for i, c in enumerate(chunks) if not c['formula']]
    Mk = doc_vectors(chunks, M)

    pred = store.predict_area(corpus, Mk, exclude=exclude)
    area_info = ({'area': area, 'source': 'указана', 'confidence': 1.0, 'votes': pred['votes']}
                 if area else {'area': pred['area'], 'source': 'авто', 'confidence': pred['confidence'],
                               'votes': pred['votes']})
    neigh = store.neighbours(corpus, Mk, area=area_info['area'], scope=scope, exclude=exclude)

    # --- фрагменты против соседей ---
    ref_vecs, ref_meta = [], []
    for d, _cov in neigh:
        for ch, v in zip(d['chunks'], d['Mk']):
            ref_vecs.append(v)
            ref_meta.append((d, ch))
    R = np.vstack(ref_vecs) if ref_vecs else np.zeros((0, M.shape[1] if len(M) else 1), np.float32)
    chunk_res = {}
    for i in keep:
        if len(R):
            sims = R @ M[i]
            j = int(np.argmax(sims))
            best = float(sims[j])
            d, ch = ref_meta[j]
            nearest = {'doc_id': d['id'], 'title': d['title'], 'area': d['area'], 'year': d['year'],
                       'url': d['url'], 'section': ch['section'], 'sim': round(best, 3),
                       'text': ch['text'][:SNIPPET]}
        else:
            best, nearest = 0.0, None
        chunk_res[i] = {'sim': best, 'novelty': 1 - best, 'support': best >= SUPPORT_SIM, 'nearest': nearest}
    red = redundant_pairs(M, chunks) if len(M) else []

    # --- абзацы ---
    paras = _para_list(parsed)
    by_para = {}
    for ci, c in enumerate(chunks):
        for g in c['paras']:
            by_para.setdefault(g, []).append(ci)
    repeat_of = {}
    for i, j, s in red:
        for a, b in ((i, j), (j, i)):
            for g in chunks[a]['paras']:
                for h in chunks[b]['paras']:
                    if g != h:
                        repeat_of.setdefault(g, {})[h] = max(s, repeat_of.get(g, {}).get(h, 0))

    # дословные совпадения: внутри статьи и с соседями (единицы — абзацы, как в lexical)
    units = body_units(parsed)
    off = 1 if parsed['meta'].get('abstract') else 0
    verbatim = {}
    for i, j, c_ab, c_ba in find_reuse(units, units, same_doc=True):
        for a, b, c in ((i, j, c_ab), (j, i, c_ba)):
            if a >= off and c >= REUSE_THRESHOLD:
                # совпадение с аннотацией — норма (она пересказывает статью), не повтор
                if b >= off:
                    verbatim.setdefault(a - off, []).append({'where': 'эта статья', 'para': b - off,
                                                             'share': round(c, 2)})
    for d, _cov in neigh:
        other = body_units(json.loads(d['parsed']))
        for i, j, c_ab, _c in find_reuse(units, other, same_doc=False):
            if i >= off and c_ab >= REUSE_THRESHOLD:
                verbatim.setdefault(i - off, []).append({'where': d['title'], 'doc_id': d['id'],
                                                         'share': round(c_ab, 2),
                                                         'text': other[j]['text'][:SNIPPET]})

    out_paras = []
    for p in paras:
        cis = by_para.get(p['i'], [])
        res = [chunk_res[c] for c in cis if c in chunk_res]
        rec = {'i': p['i'], 'section': p['section'], 'text': p['text'], 'chars': len(p['text']),
               'formula': is_formula(p['text']), 'chunks': cis,
               'shared_chunk': any(len(chunks[c]['paras']) > 1 for c in cis)}
        if res:
            best = max(res, key=lambda r: r['sim'])
            rec.update({'sim': round(float(np.mean([r['sim'] for r in res])), 3),
                        'novelty': round(float(np.mean([r['novelty'] for r in res])), 3),
                        'support': any(r['support'] for r in res), 'nearest': best['nearest']})
        else:
            rec.update({'sim': None, 'novelty': None, 'support': None, 'nearest': None,
                        'skip': 'формулы' if any(chunks[c]['formula'] for c in cis) else 'слишком короткий'})
        rec['repeats'] = [{'para': h, 'sim': round(s, 3)}
                          for h, s in sorted(repeat_of.get(p['i'], {}).items(), key=lambda x: -x[1])]
        rec['verbatim'] = verbatim.get(p['i'], [])
        out_paras.append(rec)

    # «вода»: профили утверждений уже проверенных абзацев (app/analysis/water.py)
    prof = water_lookup(store.db, [p['text'] for p in out_paras])
    for p in out_paras:
        p['water_eligible'] = water_eligible(p['text'])
        p['water'] = prof.get(text_hash(p['text'])) if p['water_eligible'] else None

    # --- разделы и статья ---
    def agg(ps: list) -> dict:
        scored = [p for p in ps if p['novelty'] is not None]
        chars = sum(p['chars'] for p in ps) or 1
        sup = [p for p in scored if p['support']]
        return {
            'paragraphs': len(ps), 'chars': sum(p['chars'] for p in ps),
            'scored': len(scored),
            'novelty': round(float(np.mean([p['novelty'] for p in sup])), 3) if sup else None,
            'no_support': round(len([p for p in scored if not p['support']]) / len(scored), 3) if scored else None,
            'repeat_share': round(sum(p['chars'] for p in ps if p['repeats']) / chars, 3),
            'verbatim_share': round(sum(p['chars'] for p in ps if p['verbatim']) / chars, 3),
            **water_agg(ps),
        }

    def water_agg(ps: list) -> dict:
        elig = [p for p in ps if p['water_eligible']]
        checked = [p for p in elig if p['water']]
        cc = sum(p['chars'] for p in checked) or 1
        # новизна по СОДЕРЖАТЕЛЬНЫМ абзацам: есть опора в корпусе и абзац не водянистый
        # (session 019: вода на редкую тему выглядела «новой»)
        subst = [p for p in checked if p['support'] and not p['water']['watery'] and p['novelty'] is not None]
        return {
            'water_eligible': len(elig), 'water_checked': len(checked),
            'watery_share': round(sum(p['chars'] for p in checked if p['water']['watery']) / cc, 3) if checked else None,
            'density': round(float(np.median([p['water']['density'] for p in checked])), 2) if checked else None,
            'novelty_substantive': round(float(np.mean([p['novelty'] for p in subst])), 3) if subst else None,
        }

    sections = []
    for si, sec in enumerate(parsed['sections']):
        sections.append({'i': si, 'title': sec['title'], 'level': sec.get('level', 1),
                         **agg([p for p in out_paras if p['section'] == si])})
    meta = parsed['meta']
    return {
        'meta': {'title': meta.get('title', ''), 'authors': meta.get('authors', []),
                 'abstract': meta.get('abstract', ''), 'keywords': meta.get('keywords', []),
                 'references': len(meta.get('references', [])), 'lang': parsed.get('lang', 'ru'),
                 'needs_ocr': parsed.get('needs_ocr', False), 'warnings': parsed.get('warnings', [])},
        'corpus': corpus, 'scope': scope, 'area': area_info,
        'neighbours': [{'doc_id': d['id'], 'title': d['title'], 'area': d['area'], 'year': d['year'],
                        'url': d['url'], 'coverage': round(c, 3)} for d, c in neigh],
        'summary': {**agg(out_paras), 'self_citation': round(self_citation_share(parsed), 3),
                    'chunks': len(chunks), 'formula_chunks': len(chunks) - len(keep)},
        'sections': sections,
        'paragraphs': out_paras,
        'thresholds': {'support_sim': SUPPORT_SIM, 'reuse': REUSE_THRESHOLD},
    }
