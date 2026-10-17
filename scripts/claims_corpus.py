"""Уровень утверждений по корпусу статей (qwen3.6-35b-a3b, см. app/analysis/claims.py).

    venv/Scripts/python.exe scripts/claims_corpus.py plan  [PARSED_DIR]   # выборка и оценка времени
    venv/Scripts/python.exe scripts/claims_corpus.py run   [PARSED_DIR]   # прогон (возобновляемый: кэш)

Выборка — около половины статей: из каждой тематической группы первые max(1, ⌊n/2⌋) по
имени + обязательные (пары из отчёта и статья синтетической проверки). Для каждого абзаца тела
(≥ 200 знаков) — утверждения и их типы; по статье:
  плотность  = 1000 · Σ(результат + факт) / Σ знаков,
  доля общих = Σ(общее + связка) / Σ утверждений,
  водянистых абзацев = доля абзацев, где общих слов и связок не меньше половины утверждений.
Пишет .cache/claims_corpus.json; прогресс — в stdout (по строке на статью).
"""
import glob
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)

MIN_PARA = 200            # короче — подписи, обрывки формул: утверждений не даёт
WATERY_GENERAL_SHARE = 0.5  # абзац «водянистый», если общих слов и связок ≥ половины утверждений
SECONDS_PER_PARA = 6.7    # замер session 017
MUST = ('izkab586', '80', 'crm-cvx__o-nek', 'crm-cvx__subg', 'crm-llm__primenenie')

mode = sys.argv[1]
parsed_dir = sys.argv[2] if len(sys.argv) > 2 else 'articles_parsed'
docs = {}
for p in sorted(glob.glob(os.path.join(parsed_dir, '*.json'))):
    with open(p, encoding='utf-8') as f:
        d = json.load(f)
    if d.get('needs_ocr') or d.get('lang') == 'en' or not d['sections']:
        continue
    paras = [(s['title'], t) for s in d['sections'] for t in s['paragraphs'] if len(t) >= MIN_PARA]
    if len(paras) >= 2:
        docs[os.path.splitext(os.path.basename(p))[0]] = (d, paras)

groups = {}
for name in docs:
    groups.setdefault(name.split('__')[0] if '__' in name else 'mivar', []).append(name)
selected = set(n for n in docs if n.startswith(MUST))
for g, names in groups.items():
    for n in sorted(names)[:max(1, len(names) // 2)]:
        selected.add(n)
# половина каждой группы гарантирована; обрезать общий список нельзя — по алфавиту выпадали
# целые группы (nn, ode, prob)
selected = sorted(selected)

if mode == 'plan':
    total = sum(len(docs[n][1]) for n in selected)
    print(f'статей с текстом: {len(docs)}, выбрано: {len(selected)}, абзацев: {total}, '
          f'оценка ≈ {total * SECONDS_PER_PARA / 60:.0f} мин (без учёта кэша)')
    for n in selected:
        print(f'  {len(docs[n][1]):3} абз.  {n}')
    raise SystemExit(0)

from app.analysis.claims import ClaimsLLM, claim_profile, extract_claims  # noqa: E402

llm = ClaimsLLM()
t0 = time.time()
out = {'articles': [], 'paragraphs': []}
for k, name in enumerate(selected, 1):
    d, paras = docs[name]
    ts = time.time()
    subst = gen = n_claims = chars = watery = 0
    for sec, t in paras:
        cl = extract_claims(llm, t)
        p = claim_profile(cl, len(t))
        out['paragraphs'].append({'article': name, 'section': sec, 'text': t, **p,
                                  'claims_list': cl})
        subst += p['результат'] + p['факт']
        gen += p['общее'] + p['связка']
        n_claims += p['claims']
        chars += len(t)
        watery += p['claims'] > 0 and p['general_share'] >= WATERY_GENERAL_SHARE
    row = {'article': name, 'title': d['meta']['title'], 'paragraphs': len(paras), 'claims': n_claims,
           'density': 1000 * subst / max(1, chars), 'general_share': gen / max(1, n_claims),
           'watery_share': watery / len(paras)}
    out['articles'].append(row)
    with open('.cache/claims_corpus.json', 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False)
    print(f'[{k:2}/{len(selected)}] {time.time() - ts:5.0f} с  плотность {row["density"]:5.2f}  '
          f'общих {row["general_share"]:.2f}  водянистых абзацев {row["watery_share"]:.0%}  {name}')
print(f'готово: {len(selected)} статей, вызовов LLM {llm.calls}, {(time.time() - t0) / 60:.0f} мин')
