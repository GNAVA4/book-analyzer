"""Проверка уровня утверждений (qwen3.6-35b-a3b в LM Studio, см. app/analysis/claims.py).

    venv/Scripts/python.exe scripts/claims_check.py water  BASE.json      # «вода» на синтетике
    venv/Scripts/python.exe scripts/claims_check.py pairs                 # та же мысль / та же тема
    venv/Scripts/python.exe scripts/claims_check.py stability BASE.json N # разброс при повторах

water:     все абзацы тела BASE + вставки из .cache/synthetic_gen.json (пересказ, «вода»);
           профиль утверждений каждого абзаца; где «вода» в распределении статьи.
pairs:     пары фрагментов из .cache/report/pairs.json; доля утверждений A, высказанных в B.
stability: N повторов без кэша (temperature 0 у LM Studio не гарантирует детерминизм).
"""
import json
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding='utf-8')

from app.analysis.claims import SUBSTANTIVE, ClaimsLLM, claim_profile, extract_claims, match_claims  # noqa: E402

mode = sys.argv[1]
llm = ClaimsLLM()
t0 = time.time()

if mode == 'water':
    with open(sys.argv[2], encoding='utf-8') as f:
        base = json.load(f)
    with open('.cache/synthetic_gen.json', encoding='utf-8') as f:
        gen = json.load(f)
    paras = [(s['title'], t) for s in base['sections'] for t in s['paragraphs'] if len(t) >= 200]
    rows = []
    for sec, t in paras:
        rows.append(('статья', sec, t, claim_profile(extract_claims(llm, t), len(t))))
    for name in ('paraphrase', 'water'):
        t = gen[name]
        rows.append(('ВСТАВКА', name, t, claim_profile(extract_claims(llm, t), len(t))))
    art = [r[3] for r in rows if r[0] == 'статья']
    dens = np.array([p['substantive_per_1000'] for p in art])
    gen_share = np.array([p['general_share'] for p in art])
    print(f'абзацев статьи: {len(art)}, вызовов LLM: {llm.calls}, {time.time() - t0:.0f} с')
    print(f'плотность содержательных утверждений на 1000 знаков: p10={np.percentile(dens, 10):.2f} '
          f'p50={np.percentile(dens, 50):.2f} p90={np.percentile(dens, 90):.2f}')
    print(f'доля общих слов и связок: p50={np.percentile(gen_share, 50):.2f} p90={np.percentile(gen_share, 90):.2f}')
    for kind, name, t, p in rows:
        if kind == 'ВСТАВКА':
            print(f'\n[{name}] утверждений {p["claims"]}: результат {p["результат"]}, факт {p["факт"]}, '
                  f'общее {p["общее"]}, связка {p["связка"]}; плотность {p["substantive_per_1000"]:.2f} '
                  f'(ниже, чем у {(dens > p["substantive_per_1000"]).mean():.0%} абзацев статьи); '
                  f'доля общих {p["general_share"]:.2f} (выше, чем у {(gen_share < p["general_share"]).mean():.0%})')
    print('\nСамые «водянистые» абзацы самой статьи (для проверки глазами):')
    for kind, name, t, p in sorted([r for r in rows if r[0] == 'статья'], key=lambda r: r[3]['substantive_per_1000'])[:3]:
        print(f'  [{p["substantive_per_1000"]:.2f}/1000, общих {p["general_share"]:.2f}] §«{name[:40]}»: {t[:160]}')
    with open('.cache/claims_water.json', 'w', encoding='utf-8') as f:
        json.dump([{'kind': k, 'section': s, 'text': t, **p} for k, s, t, p in rows], f, ensure_ascii=False, indent=1)

elif mode == 'pairs':
    with open('.cache/report/pairs.json', encoding='utf-8') as f:
        pairs = json.load(f)
    out = []
    for p in pairs:
        # только содержательные: «статья получена 29 октября» и анонсы не мысли
        ca = [c for c in extract_claims(llm, p['a_text']) if c['type'] in SUBSTANTIVE]
        cb = [c for c in extract_claims(llm, p['b_text']) if c['type'] in SUBSTANTIVE]
        m = match_claims(llm, ca, cb)
        same = sum(x['verdict'] == 'да' for x in m)
        part = sum(x['verdict'] == 'частично' for x in m)
        unk = sum(x['verdict'] == 'не оценено' for x in m)
        rated = len(ca) - unk
        share = (same + 0.5 * part) / max(1, rated)
        out.append({**{k: p[k] for k in ('id', 'kind', 'similarity', 'verbatim')}, 'claims_a': len(ca),
                    'claims_b': len(cb), 'same': same, 'partial': part, 'shared_claims': share,
                    'matches': [{'a': ca[x['a']]['claim'], 'b': cb[x['b']]['claim'] if x['b'] is not None and x['b'] < len(cb) else None,
                                 'verdict': x['verdict']} for x in m]})
        out[-1]['unrated'] = unk
        print(f'{p["id"]:9} смысл {p["similarity"]:.3f} дословно {p["verbatim"]:.2f} | утверждений A {len(ca)}, '
              f'из них в B: да {same}, частично {part}, не оценено {unk} → общая доля {share:.2f}')
    print(f'вызовов LLM: {llm.calls}, {time.time() - t0:.0f} с')
    with open('.cache/claims_pairs.json', 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

elif mode == 'stability':
    with open(sys.argv[2], encoding='utf-8') as f:
        base = json.load(f)
    n = int(sys.argv[3]) if len(sys.argv) > 3 else 3
    with open('.cache/synthetic_gen.json', encoding='utf-8') as f:
        gen = json.load(f)
    paras = [t for s in base['sections'] for t in s['paragraphs'] if len(t) >= 400][:3] + [gen['water']]
    for t in paras:
        res = [claim_profile(extract_claims(llm, t, use_cache=False), len(t)) for _ in range(n)]
        print(f'утверждений {[r["claims"] for r in res]}  плотность {[round(r["substantive_per_1000"], 2) for r in res]}  '
              f'общих {[round(r["general_share"], 2) for r in res]} | {t[:70]}')
    print(f'вызовов LLM: {llm.calls}, {time.time() - t0:.0f} с')
