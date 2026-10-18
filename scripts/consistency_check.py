"""Проверка метрик отчёта на противоречия.

    venv/Scripts/python.exe scripts/consistency_check.py [PARSED_DIR]

Читает .cache/report/articles.json (метрики статей), .cache/claims_corpus.json (утверждения по
абзацам), эмбеддинги — из кэша (.cache/embeddings.jsonl; модель не нужна, если всё посчитано).

1. Ранговые корреляции (Спирмен) между метриками статей, где есть уровень утверждений.
2. Статьи с противоречивыми сочетаниями (верхняя треть по одной метрике и «плохая» треть по другой).
3. Абзацы: новизна фрагмента, в который входит абзац, у водянистых и обычных абзацев.
4. Новизна vs «нет опоры»: высокая новизна при низком сходстве с ближайшей статьёй.
"""
import glob
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding='utf-8')

from app.analysis.semantic import Embedder, make_chunks, novelty  # noqa: E402

parsed_dir = sys.argv[1] if len(sys.argv) > 1 else 'articles_parsed'
with open('.cache/report/articles.json', encoding='utf-8') as f:
    arts = [a for a in json.load(f) if a['status'] == 'проанализирована']
with open('.cache/claims_corpus.json', encoding='utf-8') as f:
    cc = json.load(f)


def rank(v):
    v = np.asarray(v, dtype=float)
    order = v.argsort()
    r = np.empty(len(v))
    r[order] = np.arange(len(v))
    # средние ранги для равных значений
    for x in np.unique(v):
        m = v == x
        r[m] = r[m].mean()
    return r


def spearman(a, b):
    ra, rb = rank(a), rank(b)
    return float(np.corrcoef(ra, rb)[0, 1])


METRICS = [('novelty', 'новизна'), ('repeats', 'повторы'), ('self_citation', 'самоцит.'),
           ('nearest_coverage', 'сходство с ближ.'), ('claim_density', 'плотность утв.'),
           ('watery_share', 'водян. абзацы'), ('general_share', 'доля общих')]
sub = [a for a in arts if a.get('claim_density') is not None]
print(f'=== 1. Спирмен по {len(sub)} статьям с уровнем утверждений '
      f'(при n={len(sub)} |r| < ~0.47 статистически неотличим от нуля, p≈0.05) ===')
print(f'{"":18}' + ''.join(f'{lab[:10]:>11}' for _, lab in METRICS))
for k1, l1 in METRICS:
    row = f'{l1[:17]:18}'
    for k2, _ in METRICS:
        row += f'{spearman([a[k1] for a in sub], [a[k2] for a in sub]):>11.2f}' if k1 != k2 else f'{"—":>11}'
    print(row)

print('\n=== 2. Противоречивые сочетания (терцили по статьям с утверждениями) ===')


def tercile_hi(key):
    v = np.array([a[key] for a in sub])
    return np.percentile(v, 66.7)


def tercile_lo(key):
    v = np.array([a[key] for a in sub])
    return np.percentile(v, 33.3)


rules = [
    ('высокая новизна + низкая плотность утверждений', lambda a: a['novelty'] >= tercile_hi('novelty') and a['claim_density'] <= tercile_lo('claim_density')),
    ('высокая новизна + много водянистых абзацев', lambda a: a['novelty'] >= tercile_hi('novelty') and a['watery_share'] >= tercile_hi('watery_share') and a['watery_share'] > 0),
    ('высокая новизна + много смысловых повторов', lambda a: a['novelty'] >= tercile_hi('novelty') and a['repeats'] >= tercile_hi('repeats') and a['repeats'] > 0),
    ('высокая новизна + высокое самоцитирование', lambda a: a['novelty'] >= tercile_hi('novelty') and a['self_citation'] >= tercile_hi('self_citation') and a['self_citation'] > 0),
    ('высокая плотность + много повторов', lambda a: a['claim_density'] >= tercile_hi('claim_density') and a['repeats'] >= tercile_hi('repeats') and a['repeats'] > 0),
]
for name, fn in rules:
    hits = [a for a in sub if fn(a)]
    print(f'- {name}: {len(hits)}')
    for a in hits:
        print(f'    {a["short"][:40]:40} нов {a["novelty"]:.3f} плотн {a["claim_density"]:.2f} водян {a["watery_share"]:.0%} '
              f'повт {a["repeats"]:.0%} самоцит {a["self_citation"]:.0%} абзацев {next(x["paragraphs"] for x in cc["articles"] if x["article"] == a["id"])}')

print('\n=== 3. Абзацы: новизна у водянистых и обычных ===')
# новизна фрагмента leave-one-out по всем проанализированным статьям (как в отчёте)
docs = {}
for p in sorted(glob.glob(os.path.join(parsed_dir, '*.json'))):
    name = os.path.splitext(os.path.basename(p))[0]
    if name not in {a['id'] for a in arts}:
        continue
    with open(p, encoding='utf-8') as f:
        ch = [c for c in make_chunks(json.load(f)) if not c['formula']]
    docs[name] = ch
emb = Embedder()
M = {n: emb.embed([c['text'] for c in ch]) for n, ch in docs.items()}
nov = {}
for n in docs:
    others = np.vstack([M[m] for m in docs if m != n])
    nov[n] = novelty(M[n], others)
rows = []
for p in cc['paragraphs']:
    ch = docs.get(p['article'])
    if not ch:
        continue
    k = next((i for i, c in enumerate(ch) if p['text'][:120] in c['text']), None)
    if k is None:
        continue
    watery = p['claims'] > 0 and p['general_share'] >= 0.5
    rows.append((watery, float(nov[p['article']][k]), p['substantive_per_1000'], p['article'], p['text']))
w = np.array([r[1] for r in rows if r[0]])
o = np.array([r[1] for r in rows if not r[0]])
print(f'абзацев сопоставлено с фрагментами: {len(rows)} (водянистых {len(w)}, обычных {len(o)})')
print(f'новизна фрагмента: водянистые медиана {np.median(w):.3f} (p25 {np.percentile(w, 25):.3f}, p75 {np.percentile(w, 75):.3f}); '
      f'обычные медиана {np.median(o):.3f} (p25 {np.percentile(o, 25):.3f}, p75 {np.percentile(o, 75):.3f})')
print(f'Спирмен «плотность утверждений абзаца — новизна его фрагмента» по {len(rows)} абзацам: '
      f'{spearman([r[2] for r in rows], [r[1] for r in rows]):.2f}')
hi = sorted([r for r in rows if r[0]], key=lambda r: -r[1])[:3]
print('Водянистые абзацы с самой высокой новизной (кандидаты в противоречие):')
for r in hi:
    print(f'  нов {r[1]:.3f} плотн {r[2]:.2f} | {r[3][:30]} | {r[4][:150]}')

print('\n=== 4. Высокая новизна или «нет опоры»? (все 36 статей) ===')
nv = np.array([a['novelty'] for a in arts])
r_iso = spearman(nv, [a['nearest_coverage'] for a in arts])
print(f'Спирмен новизна — сходство с ближайшей статьёй: {r_iso:.2f}')
for a in sorted(arts, key=lambda a: -a['novelty'])[:5]:
    print(f'  нов {a["novelty"]:.3f}  сходство с ближайшей {a["nearest_coverage"]:.2f}  {a["group"][:22]:22} {a["short"][:40]}')

# --- данные для отчёта ---
corr_rows = []
for k1, l1 in METRICS:
    for k2, l2 in METRICS:
        if k1 < k2:
            corr_rows.append({'id': f'{k1}__{k2}', 'a': l1, 'b': l2, 'n': len(sub),
                              'spearman': round(spearman([a[k1] for a in sub], [a[k2] for a in sub]), 2)})
summary_add = {
    'novelty_isolation_r': round(r_iso, 2), 'novelty_isolation_n': len(arts),
    'para_density_novelty_r': round(spearman([r[2] for r in rows], [r[1] for r in rows]), 2),
    'para_matched': len(rows), 'watery_paras': int(len(w)),
    'watery_novelty_median': round(float(np.median(w)), 3), 'normal_novelty_median': round(float(np.median(o)), 3),
    'water_vs_general_r': round(spearman([a['claim_density'] for a in sub], [a['general_share'] for a in sub]), 2),
    'corr_n': len(sub),
}
examples = [{'id': f'c{k}', 'novelty': round(r[1], 3), 'density': round(r[2], 2),
             'article': next((a['title'] for a in arts if a['id'] == r[3]), r[3]), 'text': ' '.join(r[4].split())[:420]}
            for k, r in enumerate(hi, 1)]
with open('.cache/report/consistency.json', 'w', encoding='utf-8') as f:
    json.dump(corr_rows, f, ensure_ascii=False)
with open('.cache/report/contradictions.json', 'w', encoding='utf-8') as f:
    json.dump(examples, f, ensure_ascii=False)
with open('.cache/report/consistency_summary.json', 'w', encoding='utf-8') as f:
    json.dump(summary_add, f, ensure_ascii=False)
print('\nданные для отчёта: .cache/report/consistency.json, contradictions.json, consistency_summary.json')
