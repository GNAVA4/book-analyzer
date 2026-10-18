"""Синтетическая проверка смыслового и лексического слоёв: в чистую статью вставляются
известные дефекты, метрики обязаны на них откликнуться.

    venv/Scripts/python.exe scripts/synthetic_check.py gen    BASE.json DONOR.json  # нужна LLM
    venv/Scripts/python.exe scripts/synthetic_check.py check  BASE.json DONOR.json  # нужна embed-модель

Вставки (все — в позднюю часть статьи, далеко от источника):
  dup        — дословная копия абзаца из начала статьи;
  paraphrase — пересказ того же абзаца LLM (другими словами, тот же смысл);
  water      — «водянистый» абзац LLM на тему статьи (общие слова, без новых фактов);
  borrowed   — абзац из другой статьи корпуса (DONOR).
Генерация LLM кэшируется в .cache/synthetic_gen.json — шаги можно разнести по времени,
чтобы не держать LLM и embed-модель в GPU одновременно (правило ≤90%).
"""
import copy
import json
import os
import sys

import httpx
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding='utf-8')

LLM_URL = 'http://127.0.0.1:1234/v1/chat/completions'
LLM_MODEL = 'qwen2.5-7b-instruct'
GEN_CACHE = '.cache/synthetic_gen.json'

mode, base_path, donor_path = sys.argv[1], sys.argv[2], sys.argv[3]
with open(base_path, encoding='utf-8') as f:
    base = json.load(f)
with open(donor_path, encoding='utf-8') as f:
    donor = json.load(f)


def pick_source(parsed):
    """Абзац-источник: первый содержательный абзац (400–1200 симв.) во 2-м+ разделе."""
    for si, sec in enumerate(parsed['sections'][1:], 1):
        for pi, t in enumerate(sec['paragraphs']):
            if 400 <= len(t) <= 1200:
                return si, pi, t
    raise SystemExit('нет подходящего абзаца')


si, pi, src = pick_source(base)
_, _, borrowed = pick_source(donor)
topic = base['meta']['title']


def llm(prompt: str) -> str:
    # trust_env=False: системный прокси Windows (socks4) ломает запросы к localhost
    with httpx.Client(timeout=300, trust_env=False) as http:
        r = http.post(LLM_URL, json={
            'model': LLM_MODEL, 'temperature': 0.7, 'max_tokens': 700,
            'messages': [{'role': 'user', 'content': prompt}]})
    r.raise_for_status()
    return clean_llm(r.json()['choices'][0]['message']['content'])


def clean_llm(text: str) -> str:
    """qwen2.5 иногда съезжает в китайский посреди ответа: режем до первого иероглифа
    и оставляем только законченные предложения."""
    import re
    m = re.search(r'[　-鿿＀-￯]', text)
    if m:
        text = text[:m.start()]
        end = max(text.rfind('.'), text.rfind('!'), text.rfind('?'))
        text = text[:end + 1] if end > 0 else text
    return text.strip()


if mode == 'gen':
    gen = {
        'source': src,
        'paraphrase': llm('Перескажи этот абзац научной статьи своими словами на русском языке. '
                          'Сохрани весь смысл и все факты, но не повторяй исходные формулировки, '
                          'меняй порядок изложения и лексику. Выведи только пересказ, без пояснений.\n\n'
                          + src),
        'water': llm(f'Напиши на русском языке один абзац (5–7 предложений) для научной статьи на тему '
                     f'«{topic}». Абзац должен состоять из общих слов и банальностей: актуальность темы, '
                     f'важность технологий в современном мире, перспективы развития — без конкретных фактов, '
                     f'чисел, методов и новых утверждений. Выведи только абзац.'),
    }
    os.makedirs('.cache', exist_ok=True)
    with open(GEN_CACHE, 'w', encoding='utf-8') as f:
        json.dump(gen, f, ensure_ascii=False, indent=1)
    print('ИСТОЧНИК:\n', src, '\n\nПЕРЕСКАЗ:\n', gen['paraphrase'], '\n\nВОДА:\n', gen['water'])
    raise SystemExit(0)

# ---------------------------- check ----------------------------
from app.analysis.lexical import containment, shingles  # noqa: E402
from app.analysis.semantic import Embedder, make_chunks, novelty, redundant_pairs  # noqa: E402

with open(GEN_CACHE, encoding='utf-8') as f:
    gen = json.load(f)
assert gen['source'] == src, 'кэш генерации от другого абзаца — перезапустите gen'
inserts = {'dup': src, 'paraphrase': gen['paraphrase'], 'water': gen['water'], 'borrowed': borrowed}
emb = Embedder()
donor_M = emb.embed([c['text'] for c in make_chunks(donor) if not c['formula']])


def analyse(parsed, marker):
    ch = make_chunks(parsed)
    M = emb.embed([c['text'] for c in ch])
    # вставка — ПОСЛЕДНИЙ фрагмент с маркером (для дубля первый — это сам источник)
    ins = next((c['idx'] for c in reversed(ch) if marker and marker[:120] in c['text']), None)
    src_idx = next(c['idx'] for c in ch if src[:120] in c['text'])
    pairs = redundant_pairs(M, ch)
    keep = np.array([not c['formula'] for c in ch])
    centroid = M[keep].mean(axis=0)
    centroid /= np.linalg.norm(centroid)
    return ch, M, ins, src_idx, pairs, centroid


ch0, M0, _, src0, pairs0, cen0 = analyse(base, None)
cen_sims0 = M0 @ cen0
print(f'База: {len(ch0)} фрагментов, повторов ≥0.82: {len(pairs0)}; близость к центру статьи: '
      f'p50={np.percentile(cen_sims0, 50):.3f} p90={np.percentile(cen_sims0, 90):.3f}\n')
print(f'{"вставка":11} {"сходство с источником":>21} {"дословно":>8} {"повтор найден":>13} '
      f'{"новизна vs донор":>16} {"к центру":>8} {"макс. внутри":>12}')
results = []
for name, text in inserts.items():
    v = copy.deepcopy(base)
    target = v['sections'][-1]['paragraphs']          # в последний раздел — далеко от источника
    target.insert(max(0, len(target) - 1), text)
    ch, M, ins, src_idx, pairs, cen = analyse(v, text)
    sim_src = float(M[ins] @ M[src_idx])
    lex = containment(shingles(text), shingles(src))
    found = any({a, b} == {ins, src_idx} for a, b, _ in pairs)
    nov_donor = float(novelty(M[ins:ins + 1], donor_M)[0])
    others = [k for k in range(len(ch)) if abs(k - ins) > 1]
    max_inside = float((M[others] @ M[ins]).max())
    print(f'{name:11} {sim_src:>21.3f} {lex:>8.2f} {"да" if found else "нет":>13} {nov_donor:>16.3f} '
          f'{float(M[ins] @ cen):>8.3f} {max_inside:>12.3f}')
    results.append({'insert': name, 'sim_source': sim_src, 'lexical': lex, 'repeat_found': found,
                    'novelty_vs_donor': nov_donor, 'sim_center': float(M[ins] @ cen),
                    'max_inside': max_inside, 'text': text})
with open('.cache/synthetic_result.json', 'w', encoding='utf-8') as f:
    json.dump({'base': base['meta']['title'], 'donor': donor['meta']['title'], 'source': src,
               'center_p50': float(np.percentile(cen_sims0, 50)), 'inserts': results},
              f, ensure_ascii=False, indent=1)
