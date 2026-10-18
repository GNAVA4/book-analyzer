"""
Смысловой (эмбеддинговый) слой анализа — plan_001 фаза 1b.

В отличие от lexical.py ловит ПЕРЕСКАЗ: фрагменты, близкие по смыслу при разных словах.
Единица — фрагмент 300–1500 символов (короткие абзацы раздела склеиваются, длинные
режутся по предложениям): короткий абзац даёт шумный вектор, длинный — размытый.

Метрики (все на косинусной близости нормированных векторов):
  coverage(A→B)  = среднее по фрагментам A максимальной близости к фрагментам B —
                   «насколько содержание A уже есть в B» (асимметрично);
  novelty(a|C)   = 1 − max близость фрагмента a к фрагментам корпуса C (leave-one-out:
                   C = все остальные документы);
  redundancy(A)  = доля фрагментов A, у которых есть смысловой двойник внутри A
                   (не соседний фрагмент) с близостью ≥ порога.
Пороги откалиброваны по распределению близостей (см. session_015).
"""
import hashlib
import json
import os
import re

import httpx
import numpy as np

EMBED_MODEL = "text-embedding-qwen3-embedding-0.6b"
EMBED_URL = "http://127.0.0.1:1234/v1/embeddings"
EMBED_BATCH = 32
CHUNK_MIN, CHUNK_MAX = 300, 1500   # символов во фрагменте
# Двойник внутри документа. Калибровка session_015 (22 статьи, 5251 несоседняя пара):
# p95=0.70, p99=0.79; все пары ≥0.83 при ручном просмотре — реальный пересказ
# (Varlamov2024: теория ≈ результаты 0.947, 80: модель ≈ выводы 0.85).
REDUNDANT_SIM = 0.82
# Доля кириллических/латинских букв среди непробельных символов: проза ≈0.95 (медиана),
# p5 по 430 фрагментам = 0.70; всё ниже при просмотре — формулы, таблицы чисел, код.
FORMULA_LETTER_SHARE = 0.70
_SENT = re.compile(r'(?<=[.!?])\s+(?=[А-ЯЁA-Z])')


# ---------------------------------------------------------------------------
# Эмбеддинги с дисковым кэшем
# ---------------------------------------------------------------------------

class Embedder:
    def __init__(self, cache_path: str = '.cache/embeddings.jsonl', model: str = EMBED_MODEL):
        self.model = model
        self.cache_path = cache_path
        self.cache = {}
        if os.path.exists(cache_path):
            with open(cache_path, encoding='utf-8') as f:
                for line in f:
                    rec = json.loads(line)
                    self.cache[rec['k']] = rec['v']
        # trust_env=False: системный прокси Windows (socks4) иначе ломает запросы к localhost
        self.http = httpx.Client(timeout=120, trust_env=False)

    def _key(self, text: str) -> str:
        return hashlib.sha1((self.model + '\x00' + text).encode('utf-8')).hexdigest()

    def embed(self, texts: list) -> np.ndarray:
        missing = [t for t in dict.fromkeys(texts) if self._key(t) not in self.cache]
        if missing:
            os.makedirs(os.path.dirname(self.cache_path) or '.', exist_ok=True)
            with open(self.cache_path, 'a', encoding='utf-8') as f:
                for i in range(0, len(missing), EMBED_BATCH):
                    batch = missing[i:i + EMBED_BATCH]
                    r = self.http.post(EMBED_URL, json={'model': self.model, 'input': batch})
                    r.raise_for_status()
                    for t, d in zip(batch, sorted(r.json()['data'], key=lambda d: d['index'])):
                        v = [round(x, 6) for x in d['embedding']]
                        self.cache[self._key(t)] = v
                        f.write(json.dumps({'k': self._key(t), 'v': v}) + '\n')
        m = np.array([self.cache[self._key(t)] for t in texts], dtype=np.float32)
        m /= np.linalg.norm(m, axis=1, keepdims=True) + 1e-9
        return m


# ---------------------------------------------------------------------------
# Фрагменты
# ---------------------------------------------------------------------------

def make_chunks(parsed: dict) -> list:
    """Фрагменты тела статьи: [{section, text}]. Аннотация — отдельный фрагмент
    (она по определению пересказывает статью — помечаем, чтобы не считать её повтором)."""
    # paras — номера абзацев тела (сквозная нумерация по разделам), из которых собран фрагмент:
    # метрики считаются по фрагментам, а показываются по абзацам.
    chunks = []
    if parsed['meta'].get('abstract'):
        chunks.append({'section': 'Аннотация', 'text': parsed['meta']['abstract'], 'abstract': True,
                       'paras': []})
    g = -1
    for sec in parsed['sections']:
        buf, buf_paras = '', []
        for para in sec['paragraphs']:
            g += 1
            pieces = [para]
            if len(para) > CHUNK_MAX:                      # длинный абзац — по предложениям
                pieces, cur = [], ''
                for s in _SENT.split(para):
                    if cur and len(cur) + len(s) > CHUNK_MAX:
                        pieces.append(cur)
                        cur = ''
                    cur = (cur + ' ' + s).strip()
                if cur:
                    pieces.append(cur)
            for piece in pieces:
                buf = (buf + '\n' + piece).strip() if buf else piece
                buf_paras.append(g)
                if len(buf) >= CHUNK_MIN:
                    chunks.append({'section': sec['title'], 'text': buf, 'abstract': False,
                                   'paras': sorted(set(buf_paras))})
                    buf, buf_paras = '', []
        if buf:
            if chunks and chunks[-1]['section'] == sec['title'] and len(chunks[-1]['text']) + len(buf) <= CHUNK_MAX:
                chunks[-1]['text'] += '\n' + buf
                chunks[-1]['paras'] = sorted(set(chunks[-1]['paras'] + buf_paras))
            elif len(buf) >= CHUNK_MIN // 3:
                chunks.append({'section': sec['title'], 'text': buf, 'abstract': False,
                               'paras': sorted(set(buf_paras))})
    for i, c in enumerate(chunks):
        c['idx'] = i
        c['formula'] = is_formula(c['text'])
    return chunks


def is_formula(text: str) -> bool:
    """Фрагмент, состоящий в основном из формул (разбитых текстовым слоем на символы):
    его вектор отражает «похожесть мусора», а не смысл — из метрик исключаем.
    Ложные повторы ode__o-teh: «⎡ ′−η ζ +ζ⎤ …» ↔ «ζ + ′ζ −ζ …» = 0.87."""
    ns = [c for c in text if not c.isspace()]
    if not ns:
        return True
    letters = sum(1 for c in ns if re.match(r'[а-яёa-z]', c, re.I))
    return letters / len(ns) < FORMULA_LETTER_SHARE


# ---------------------------------------------------------------------------
# Метрики
# ---------------------------------------------------------------------------

def coverage(a: np.ndarray, b: np.ndarray) -> float:
    """Среднее по фрагментам A максимальной близости к фрагментам B."""
    return float((a @ b.T).max(axis=1).mean()) if len(a) and len(b) else 0.0


def novelty(a: np.ndarray, corpus: np.ndarray) -> np.ndarray:
    """1 − максимальная близость каждого фрагмента A к корпусу."""
    if not len(corpus):
        return np.ones(len(a))
    return 1.0 - (a @ corpus.T).max(axis=1)


def redundant_pairs(a: np.ndarray, chunks: list, thr: float = REDUNDANT_SIM) -> list:
    """Пары (i, j, sim) внутри документа: несоседние фрагменты с близостью ≥ thr,
    аннотация исключена (она обязана пересказывать текст)."""
    s = a @ a.T
    out = []
    for i in range(len(a)):
        for j in range(i + 2, len(a)):             # j ≥ i+2: соседи продолжают одну мысль
            if chunks[i].get('abstract') or chunks[j].get('abstract'):
                continue
            if chunks[i].get('formula') or chunks[j].get('formula'):
                continue
            if s[i, j] >= thr:
                out.append((i, j, float(s[i, j])))
    return sorted(out, key=lambda p: -p[2])
