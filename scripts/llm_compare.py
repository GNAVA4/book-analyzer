"""Сравнение локальных LLM на задаче уровня утверждений (app/analysis/claims.py), без кэша.

    venv/Scripts/python.exe scripts/llm_compare.py run MODEL_ID      # прогон одной загруженной модели
    venv/Scripts/python.exe scripts/llm_compare.py report MODEL_A MODEL_B

Набор (одинаковый для всех моделей):
  - синтетика: исходный абзац, его пересказ, «вода» (.cache/synthetic_gen.json) — вода должна
    получить низкую плотность содержательных утверждений;
  - 7 пар из .cache/report/pairs.json: «та же мысль» (мивары 2011→2013), «та же тема» (одна
    школа), повтор внутри статьи — доля утверждений A, высказанных в B, должна быть высокой
    у self/inner и низкой у school;
  - 6 абзацев разных областей из data/corpus.db — замер скорости на длинных абзацах.
Пишет .cache/llm_compare_<model>.json: время и токены каждого вызова, ответы, метрики.
"""
import json
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding='utf-8')

from app.analysis import claims as C  # noqa: E402


class TimedLLM(C.ClaimsLLM):
    """Без кэша; запоминает время и число токенов каждого вызова."""
    def __init__(self, model):
        super().__init__(cache_path=os.devnull, model=model)
        self.log = []

    def complete(self, user_text, use_cache=False):
        prompt = (f'<|im_start|>user\n{user_text}<|im_end|>\n'
                  f'<|im_start|>assistant\n<think>\n\n</think>\n\n')
        t = time.time()
        r = self.http.post(C.LLM_URL, json={'model': self.model, 'prompt': prompt, 'temperature': 0,
                                            'max_tokens': C.MAX_TOKENS, 'stop': ['<|im_end|>']})
        r.raise_for_status()
        js = r.json()
        dt = time.time() - t
        u = js.get('usage', {})
        text = js['choices'][0]['text'].strip()
        self.log.append({'sec': round(dt, 2), 'prompt_tokens': u.get('prompt_tokens'),
                         'completion_tokens': u.get('completion_tokens'), 'text': text})
        print(f'  {dt:6.1f} с  {u.get("completion_tokens")} ток.', flush=True)
        return text


def corpus_paragraphs(n=6):
    db = sqlite3.connect('data/corpus.db')
    out = []
    for (parsed,) in db.execute('SELECT parsed FROM document ORDER BY id'):
        d = json.loads(parsed)
        ps = [t for s in d['sections'] for t in s['paragraphs'] if 700 <= len(t) <= 1300]
        if ps:
            out.append(ps[len(ps) // 2])
    step = max(1, len(out) // n)
    return out[::step][:n]


def run(model):
    llm = TimedLLM(model)
    res = {'model': model, 'synthetic': {}, 'pairs': [], 'corpus': []}
    gen = json.load(open('.cache/synthetic_gen.json', encoding='utf-8'))
    for k in ('source', 'paraphrase', 'water'):
        print(k)
        cl = C.extract_claims(llm, gen[k])
        res['synthetic'][k] = {'claims': cl, 'profile': C.claim_profile(cl, len(gen[k]))}
    for p in json.load(open('.cache/report/pairs.json', encoding='utf-8')):
        print(p['id'])
        a, b = C.extract_claims(llm, p['a_text']), C.extract_claims(llm, p['b_text'])
        sa = [c for c in a if c['type'] in C.SUBSTANTIVE]
        sb = [c for c in b if c['type'] in C.SUBSTANTIVE]
        m = C.match_claims(llm, sa, sb) if sa and sb else []
        score = (sum(x['verdict'] == 'да' for x in m) + 0.5 * sum(x['verdict'] == 'частично' for x in m)) / len(m) if m else None
        res['pairs'].append({'id': p['id'], 'kind': p['kind'], 'a': a, 'b': b, 'match': m, 'score': score})
    for t in corpus_paragraphs():
        print('corpus', len(t))
        cl = C.extract_claims(llm, t)
        res['corpus'].append({'chars': len(t), 'claims': cl, 'profile': C.claim_profile(cl, len(t))})
    res['log'] = llm.log
    tok = sum(x['completion_tokens'] or 0 for x in llm.log)
    sec = sum(x['sec'] for x in llm.log)
    res['totals'] = {'calls': len(llm.log), 'sec': round(sec, 1), 'completion_tokens': tok,
                     'tok_per_sec': round(tok / sec, 1) if sec else None,
                     'sec_per_extract': round(sum(x['sec'] for x in llm.log if 'Список A' not in x['text']) / len(llm.log), 1)}
    path = f'.cache/llm_compare_{model.replace("/", "_")}.json'
    json.dump(res, open(path, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print(json.dumps(res['totals'], ensure_ascii=False))


def report(*models):
    for m in models:
        r = json.load(open(f'.cache/llm_compare_{m.replace("/", "_")}.json', encoding='utf-8'))
        t, s = r['totals'], r['synthetic']
        print(f'\n=== {m}: {t["calls"]} вызовов, {t["sec"]} с, {t["tok_per_sec"]} ток/с')
        for k in ('source', 'paraphrase', 'water'):
            p = s[k]['profile']
            print(f'  {k:10s} утв. {p["claims"]:2d}  плотность {p["substantive_per_1000"]:.2f}  доля общих {p["general_share"]:.2f}')
        for kind in ('self', 'school', 'inner'):
            sc = [p['score'] for p in r['pairs'] if p['kind'] == kind and p['score'] is not None]
            print(f'  пары {kind:6s}: {[round(x, 2) for x in sc]}  среднее {sum(sc) / len(sc) if sc else float("nan"):.2f}')
        dens = [c['profile']['substantive_per_1000'] for c in r['corpus']]
        print(f'  корпус: плотность {[round(x, 1) for x in dens]}')


if __name__ == '__main__':
    if sys.argv[1] == 'run':
        run(sys.argv[2])
    else:
        report(*sys.argv[2:])
