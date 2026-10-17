"""Наборы данных для отчёта «Смысловой анализ статей» (дашборд-артефакт).

    venv/Scripts/python.exe scripts/build_report_data.py [PARSED_DIR] [OUT_DIR]
Читает: разобранные статьи (JSON), .cache/semantic_report.json (scripts/semantic_articles.py),
.cache/synthetic_result.json (scripts/synthetic_check.py check). Эмбеддинги — из кэша.
Пишет OUT_DIR/{summary,articles,coverage,pairs,synthetic}.json — массивы строк.
"""
import glob
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding='utf-8')

from app.analysis.lexical import containment, shingles  # noqa: E402
from app.analysis.semantic import REDUNDANT_SIM, Embedder, make_chunks  # noqa: E402

parsed_dir = sys.argv[1] if len(sys.argv) > 1 else 'articles_parsed'
out_dir = sys.argv[2] if len(sys.argv) > 2 else '.cache/report'
os.makedirs(out_dir, exist_ok=True)

GROUPS = {
    'mivar': 'Мивары', 'nn': 'Нейросети, 2000-е', 'fuzzy': 'Нечёткая логика', 'expert': 'Экспертные системы',
    'ode': 'Дифференциальные уравнения', 'prob': 'Теория вероятностей', 'graph': 'Графы',
    'crm-llm': 'Большие языковые модели', 'crm-cnn': 'Свёрточные сети', 'crm-rl': 'Обучение с подкреплением',
    'crm-pinn': 'Физически информированные НС', 'crm-cvx': 'Выпуклая оптимизация',
    'crm-stab': 'Устойчивость разностных уравнений', 'crm-boltz': 'Уравнение Больцмана',
}


def group_of(name):
    return name.split('__')[0] if '__' in name else 'mivar'


def source_of(name):
    g = group_of(name)
    return 'прислано' if g == 'mivar' else ('КИМ, 2023–2025' if g.startswith('crm-') else 'КиберЛенинка')


def clip(t, n):
    t = ' '.join(t.split())
    return t if len(t) <= n else t[:n].rsplit(' ', 1)[0] + '…'


def sentence_case(t):
    """«МИВАРНЫЙ ПОДХОД К …» → «Миварный подход к …» (названия старых журналов набраны капсом)."""
    letters = [c for c in t if c.isalpha()]
    if letters and sum(c.isupper() for c in letters) / len(letters) > 0.8:
        return t[:1].upper() + t[1:].lower()
    return t


with open('.cache/semantic_report.json', encoding='utf-8') as f:
    sem = json.load(f)
sem_by = {d['name']: d for d in sem['docs']}

# --- статьи: все файлы, включая исключённые ---
articles, parsed = [], {}
for p in sorted(glob.glob(os.path.join(parsed_dir, '*.json'))):
    name = os.path.splitext(os.path.basename(p))[0]
    with open(p, encoding='utf-8') as f:
        d = json.load(f)
    parsed[name] = d
    s = sem_by.get(name)
    status = ('скан, нужен OCR' if d.get('needs_ocr') else
              'текст на английском' if d.get('lang') == 'en' else
              'проанализирована' if s else 'мало текста')
    row = {'id': name, 'title': clip(sentence_case(d['meta']['title'] or name), 110), 'group': GROUPS[group_of(name)],
           'source': source_of(name), 'status': status, 'authors': ', '.join(d['meta']['authors'][:4]),
           'references': len(d['meta']['references']), 'sections': len(d['sections'])}
    if s:
        row.update({'fragments': s['chunks'], 'novelty': round(s['novelty_mean'], 3),
                    'repeats': round(s['redundancy_share'], 3), 'self_citation': round(s['self_citation'], 3),
                    'nearest': s['nearest'], 'nearest_coverage': round(s['nearest_coverage'], 3)})
    row['short'] = clip(' '.join(row['title'].split()[:4]), 38)
    articles.append(row)
title_of = {a['id']: a['title'] for a in articles}
for a in articles:
    if 'nearest' in a:
        a['nearest'] = clip(title_of.get(a['nearest'], a['nearest']), 70)

# --- покрытие: длинный формат ---
names = sem['names']
cov = np.array(sem['coverage'])
coverage = [{'a': names[i], 'b': names[j], 'coverage': round(float(cov[i, j]), 3),
             'same_group': group_of(names[i]) == group_of(names[j])}
            for i in range(len(names)) for j in range(len(names)) if i != j]

# --- сводка ---
same = [c['coverage'] for c in coverage if c['same_group']]
cross = [c['coverage'] for c in coverage if not c['same_group']]
hits = sum(group_of(names[int(np.argmax(cov[i]))]) == group_of(names[i]) for i in range(len(names)))
summary = [{
    'files': len(articles),
    'analysed': sum(a['status'] == 'проанализирована' for a in articles),
    'english': sum(a['status'] == 'текст на английском' for a in articles),
    'scans': sum(a['status'] == 'скан, нужен OCR' for a in articles),
    'fragments': sum(a.get('fragments', 0) for a in articles),
    'groups': len({a['group'] for a in articles if a['status'] == 'проанализирована'}),
    'same_topic_coverage': round(float(np.mean(same)), 3),
    'cross_topic_coverage': round(float(np.mean(cross)), 3),
    'nearest_same_group': int(hits),
    'repeat_threshold': REDUNDANT_SIM,
}]

# --- примеры пар ---
emb = Embedder()


def top_pairs(na, nb, k, kind, note):
    A = [c for c in make_chunks(parsed[na]) if not c['formula']]
    B = [c for c in make_chunks(parsed[nb]) if not c['formula']]
    S = emb.embed([c['text'] for c in A]) @ emb.embed([c['text'] for c in B]).T
    out, ua, ub = [], set(), set()
    for s, i, j in sorted(((S[i, j], i, j) for i in range(len(A)) for j in range(len(B))), reverse=True):
        same_doc = na == nb
        if (i in ua or j in ub or i in ub or j in ua
                or (same_doc and (j - i < 2 or A[i]['abstract'] or A[j]['abstract']))):
            continue
        ua.add(i)
        ub.add(j)
        lex = max(containment(shingles(A[i]['text']), shingles(B[j]['text'])),
                  containment(shingles(B[j]['text']), shingles(A[i]['text'])))
        out.append({'id': f'{kind}-{len(out) + 1}', 'kind': kind, 'note': note,
                    'a_doc': title_of[na], 'b_doc': title_of[nb],
                    'a_section': A[i]['section'], 'b_section': B[j]['section'],
                    'similarity': round(float(s), 3), 'verbatim': round(float(lex), 3),
                    'a_text': clip(A[i]['text'], 420), 'b_text': clip(B[j]['text'], 420)})
        if len(out) == k:
            break
    return out


cvx = sorted(n for n in names if n.startswith('crm-cvx__o-nek') or n.startswith('crm-cvx__subg'))
pairs = (top_pairs('izkab586', '80', 3, 'self', 'Одни мысли, пересказанные заново (мивары 2011 → 2013)')
         + top_pairs(cvx[0], cvx[1], 2, 'school', 'Одна школа и узкая тема, утверждения разные')
         + top_pairs('VarlamovSoavtors_3_24', 'VarlamovSoavtors_3_24', 2, 'inner', 'Повтор внутри одной статьи'))

# --- синтетика ---
with open('.cache/synthetic_result.json', encoding='utf-8') as f:
    syn = json.load(f)
LABEL = {'dup': 'Дубль абзаца', 'paraphrase': 'Пересказ (LLM)', 'water': '«Вода» (LLM)',
         'borrowed': 'Абзац из чужой статьи'}
EXPECT = {'dup': 'найти повтор', 'paraphrase': 'найти повтор', 'water': 'отличить от текста',
          'borrowed': 'новизна ≈ 0 относительно источника'}
synthetic = []
for r in syn['inserts']:
    ok = r['repeat_found'] if r['insert'] in ('dup', 'paraphrase') else (
        r['novelty_vs_donor'] < 0.2 if r['insert'] == 'borrowed' else False)
    synthetic.append({'insert': LABEL[r['insert']], 'expected': EXPECT[r['insert']],
                      'similarity_to_source': round(r['sim_source'], 3), 'verbatim': round(r['lexical'], 3),
                      'novelty_vs_donor': round(r['novelty_vs_donor'], 3), 'similarity_to_center': round(r['sim_center'], 3),
                      'detected': 'да' if ok else 'нет'})
summary[0]['synthetic_base'] = syn['base']
summary[0]['center_p50'] = round(syn['center_p50'], 3)
summary[0]['synthetic_detected'] = sum(s['detected'] == 'да' for s in synthetic)
summary[0]['synthetic_total'] = len(synthetic)

# --- уровень утверждений (scripts/claims_check.py water / pairs), если считался ---
claims_pairs = []
if os.path.exists('.cache/claims_water.json'):
    with open('.cache/claims_water.json', encoding='utf-8') as f:
        cw = json.load(f)
    art_rows = [r for r in cw if r['kind'] == 'статья']
    dens = np.array([r['substantive_per_1000'] for r in art_rows])
    gsh = np.array([r['general_share'] for r in art_rows])
    w = next(r for r in cw if r['kind'] == 'ВСТАВКА' and r['section'] == 'water')
    summary[0].update({
        'claims_paragraphs': len(art_rows),
        'claims_density_p50': round(float(np.percentile(dens, 50)), 2),
        'claims_general_p50': round(float(np.percentile(gsh, 50)), 2),
        'water_claims': w['claims'], 'water_substantive': w['результат'] + w['факт'],
        'water_density': round(w['substantive_per_1000'], 2),
        'water_general_share': round(w['general_share'], 2),
        'water_density_below_share': round(float((dens > w['substantive_per_1000']).mean()), 2),
    })
if os.path.exists('.cache/claims_pairs.json'):
    with open('.cache/claims_pairs.json', encoding='utf-8') as f:
        for r in json.load(f):
            claims_pairs.append({'id': r['id'], 'kind': r['kind'], 'claims_a': r['claims_a'],
                                 'same': r['same'], 'partial': r['partial'],
                                 'shared_claims': round(r['shared_claims'], 2)})
    by_kind = {}
    for r in claims_pairs:
        k = by_kind.setdefault(r['kind'], [0.0, 0])
        k[0] += r['same'] + 0.5 * r['partial']
        k[1] += r['claims_a']
    for kind, (num, den) in by_kind.items():
        summary[0][f'shared_claims_{kind}'] = round(num / den, 2) if den else 0.0

# --- утверждения по корпусу (scripts/claims_corpus.py run) ---
watery = []
if os.path.exists('.cache/claims_corpus.json'):
    with open('.cache/claims_corpus.json', encoding='utf-8') as f:
        cc = json.load(f)
    by_art = {r['article']: r for r in cc['articles']}
    for a in articles:
        r = by_art.get(a['id'])
        if r:
            a['claim_density'] = round(r['density'], 2)
            a['general_share'] = round(r['general_share'], 3)
            a['watery_share'] = round(r['watery_share'], 3)
    dens = [r['density'] for r in cc['articles']]
    summary[0].update({'claims_articles': len(cc['articles']),
                       'claims_corpus_paragraphs': len(cc['paragraphs']),
                       'claims_corpus_density_median': round(float(np.median(dens)), 2) if dens else 0.0})
    # самые водянистые абзацы корпуса: есть утверждения, а содержательных меньше всего
    cand = [p for p in cc['paragraphs'] if p['claims'] >= 3 and len(p['text']) >= 300]
    cand.sort(key=lambda p: (p['substantive_per_1000'], -p['general_share']))
    for k, p in enumerate(cand[:6], 1):
        watery.append({'id': f'w{k}', 'article': title_of.get(p['article'], p['article']),
                       'section': p['section'], 'density': round(p['substantive_per_1000'], 2),
                       'general_share': round(p['general_share'], 2), 'claims': p['claims'],
                       'text': clip(p['text'], 520)})

# --- проверка согласованности метрик (scripts/consistency_check.py) ---
if os.path.exists(os.path.join(out_dir, 'consistency_summary.json')):
    with open(os.path.join(out_dir, 'consistency_summary.json'), encoding='utf-8') as f:
        summary[0].update(json.load(f))

for name, rows in [('summary', summary), ('articles', articles), ('coverage', coverage),
                   ('pairs', pairs), ('synthetic', synthetic), ('claims_pairs', claims_pairs),
                   ('watery', watery)]:
    path = os.path.join(out_dir, name + '.json')
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(rows, f, ensure_ascii=False)
    print(f'{name:10} {len(rows):5} строк  {os.path.getsize(path) // 1024:4} КБ')
print(json.dumps(summary[0], ensure_ascii=False, indent=1))
