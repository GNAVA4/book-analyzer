"""
Уровень утверждений (LLM) — plan_001 фаза 2, прототип.

Модель: qwen3.6-35b-a3b в LM Studio (думающая модель, контекст 4096 из-за 12 ГБ GPU).
Штатные способы выключить рассуждения LM Studio не передаёт (chat_template_kwargs,
reasoning_effort, /no_think — проверено, session 017: рассуждение съедает весь max_tokens).
Поэтому запрос идёт в /v1/completions с шаблоном ChatML, где блок <think></think> уже закрыт:
модель сразу пишет ответ (~3 с на абзац вместо ~50 с с рассуждением).

Две операции:
  extract_claims(абзац) → [{claim, type}], type ∈ CLAIM_TYPES:
      результат — новый результат/вывод авторов (полученные свойства, числа, сравнения);
      факт      — конкретное проверяемое знание: определение, метод, свойство, чужой результат;
      общее     — общие слова и оценки без конкретики («актуально», «позволяет повысить эффективность»);
      связка    — метатекст: анонс, план статьи, ссылка на разделы.
  match_claims(A, B) → по каждому утверждению A: высказано ли оно в B (то же утверждение,
      а не та же тема).
Ответы кэшируются в .cache/llm_claims.jsonl по sha1(модель + промпт); температура 0.
"""
import hashlib
import json
import os
import re

import httpx

LLM_URL = 'http://127.0.0.1:1234/v1/completions'
LLM_MODEL = 'qwen3.6-35b-a3b'
MAX_TOKENS = 900          # ответ без рассуждения: ~100–400 токенов; запас при контексте 4096
CLAIM_TYPES = ('результат', 'факт', 'общее', 'связка')
SUBSTANTIVE = ('результат', 'факт')

EXTRACT_PROMPT = """Разбери абзац научной статьи на атомарные утверждения (одна мысль — одно утверждение, своими словами, кратко).
Каждому утверждению дай тип:
- "результат" — новый результат или вывод авторов этой работы (что получено, доказано, измерено, предложено);
- "факт" — конкретное проверяемое знание: определение, описание метода, свойство, чужой результат со ссылкой;
- "общее" — общие слова и оценки без конкретики, которые нельзя проверить («актуально», «важно», «позволяет повысить эффективность», «перспективно»);
- "связка" — метатекст: анонс, план статьи, «в работе рассмотрено», «далее описано».
Ответ — только JSON-массив объектов {"claim": "...", "type": "..."}, без пояснений.

Абзац:
"""

# Построчный формат вместо JSON: на JSON-массив модель отвечала одним объектом (только A1),
# и остальные утверждения молча получали «нет» (session 017).
# Строгость (session 017): с мягкой формулировкой судья засчитывал «предлагаются методы
# первого порядка» ≈ «доказана линейная скорость сходимости» и сильно/слабо выпуклый случай
# как «частично» — тема принималась за утверждение.
MATCH_PROMPT = """Даны два списка утверждений из двух научных текстов.
Для КАЖДОГО утверждения из списка A определи, высказано ли ТО ЖЕ САМОЕ утверждение в списке B.
Утверждение то же самое, только если совпадают и объект, и условия, и то, что о нём утверждается.
Общая тема, общая область или похожие слова — это «нет».
да — B утверждает то же самое (возможно другими словами);
частично — B утверждает то же о том же объекте, но с другими условиями или только часть;
нет — в B этого утверждения нет; утверждения о разных объектах или классах (например, сильно и слабо выпуклые функции) — «нет».
Ответ — ровно по одной строке на каждое утверждение A, в формате:
A1 -> B3 да
A2 -> - нет
Без пояснений.

"""
_MATCH_LINE = re.compile(r'A\s*(\d+)\s*[-–>:]+\s*(?:B\s*(\d+)|-|—|нет)?\s*[,;:]?\s*(да|частично|нет)', re.I)


class ClaimsLLM:
    def __init__(self, cache_path: str = '.cache/llm_claims.jsonl', model: str = LLM_MODEL):
        self.model = model
        self.cache_path = cache_path
        self.cache = {}
        if os.path.exists(cache_path):
            with open(cache_path, encoding='utf-8') as f:
                for line in f:
                    rec = json.loads(line)
                    self.cache[rec['k']] = rec['v']
        # trust_env=False: системный прокси Windows (socks4) ломает запросы к localhost
        self.http = httpx.Client(timeout=600, trust_env=False)
        self.calls = 0

    def complete(self, user_text: str, use_cache: bool = True) -> str:
        prompt = (f'<|im_start|>user\n{user_text}<|im_end|>\n'
                  f'<|im_start|>assistant\n<think>\n\n</think>\n\n')
        key = hashlib.sha1((self.model + '\x00' + prompt).encode('utf-8')).hexdigest()
        if use_cache and key in self.cache:
            return self.cache[key]
        r = self.http.post(LLM_URL, json={'model': self.model, 'prompt': prompt, 'temperature': 0,
                                          'max_tokens': MAX_TOKENS, 'stop': ['<|im_end|>']})
        r.raise_for_status()
        text = r.json()['choices'][0]['text'].strip()
        self.calls += 1
        if use_cache:
            self.cache[key] = text
            os.makedirs(os.path.dirname(self.cache_path) or '.', exist_ok=True)
            with open(self.cache_path, 'a', encoding='utf-8') as f:
                f.write(json.dumps({'k': key, 'v': text}, ensure_ascii=False) + '\n')
        return text


def parse_json_array(text: str) -> list:
    """JSON-массив из ответа модели; при обрезке — спасаем целые объекты {...}."""
    m = re.search(r'\[.*\]', text, re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    out = []
    for obj in re.findall(r'\{[^{}]*\}', text):
        try:
            out.append(json.loads(obj))
        except json.JSONDecodeError:
            continue
    return out


def extract_claims(llm: ClaimsLLM, paragraph: str, use_cache: bool = True) -> list:
    items = parse_json_array(llm.complete(EXTRACT_PROMPT + paragraph, use_cache=use_cache))
    claims = []
    for it in items:
        if isinstance(it, dict) and it.get('claim'):
            t = str(it.get('type', '')).strip().lower()
            claims.append({'claim': str(it['claim']).strip(), 'type': t if t in CLAIM_TYPES else 'общее'})
    return claims


def claim_profile(claims: list, n_chars: int) -> dict:
    """Плотность содержательных утверждений на 1000 знаков и доли типов."""
    n = len(claims)
    by = {t: sum(c['type'] == t for c in claims) for t in CLAIM_TYPES}
    subst = by['результат'] + by['факт']
    return {'claims': n, **by,
            'substantive_per_1000': 1000 * subst / max(1, n_chars),
            'general_share': (by['общее'] + by['связка']) / n if n else 0.0}


def match_claims(llm: ClaimsLLM, a: list, b: list) -> list:
    """По каждому утверждению A — вердикт, высказано ли оно в B."""
    if not a or not b:
        return [{'a': i, 'b': None, 'verdict': 'нет'} for i in range(len(a))]
    la = '\n'.join(f'A{i + 1}. {c["claim"]}' for i, c in enumerate(a))
    lb = '\n'.join(f'B{j + 1}. {c["claim"]}' for j, c in enumerate(b))
    raw = llm.complete(MATCH_PROMPT + 'Список A:\n' + la + '\n\nСписок B:\n' + lb)
    out = {}
    for m in _MATCH_LINE.finditer(raw):
        ai = int(m.group(1)) - 1
        bi = int(m.group(2)) - 1 if m.group(2) else None
        if 0 <= ai < len(a) and ai not in out:
            out[ai] = {'a': ai, 'b': bi if bi is not None and 0 <= bi < len(b) else None,
                       'verdict': m.group(3).lower()}
    # утверждение без строки в ответе — «не оценено», а не «нет»: иначе сбой формата
    # выглядит как «общих мыслей нет»
    return [out.get(i, {'a': i, 'b': None, 'verdict': 'не оценено'}) for i in range(len(a))]
