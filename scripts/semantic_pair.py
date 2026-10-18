"""Самые близкие фрагменты двух статей: смысловая близость + дословное совпадение.

    venv/Scripts/python.exe scripts/semantic_pair.py PARSED_A.json PARSED_B.json [N]
Показывает, пересказ это (смысл высокий, дословно ~0), копипаст (оба высокие) или
просто общая тема (смысл умеренный).
"""
import json
import sys

import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding='utf-8')

from app.analysis.lexical import containment, shingles  # noqa: E402
from app.analysis.semantic import Embedder, make_chunks  # noqa: E402

pa, pb = sys.argv[1], sys.argv[2]
n_show = int(sys.argv[3]) if len(sys.argv) > 3 else 5
with open(pa, encoding='utf-8') as f:
    A = [c for c in make_chunks(json.load(f)) if not c['formula']]
with open(pb, encoding='utf-8') as f:
    B = [c for c in make_chunks(json.load(f)) if not c['formula']]
emb = Embedder()
S = emb.embed([c['text'] for c in A]) @ emb.embed([c['text'] for c in B]).T
pairs = sorted(((S[i, j], i, j) for i in range(len(A)) for j in range(len(B))), reverse=True)
ua, ub, shown = set(), set(), 0
for s, i, j in pairs:
    if i in ua or j in ub:
        continue
    ua.add(i)
    ub.add(j)
    sa, sb = shingles(A[i]['text']), shingles(B[j]['text'])
    lex = max(containment(sa, sb), containment(sb, sa))
    print(f'[смысл {s:.3f} | дословно {lex:.2f}]  §«{A[i]["section"][:35]}»  ↔  §«{B[j]["section"][:35]}»')
    print('   A:', A[i]['text'][:300].replace('\n', ' '))
    print('   B:', B[j]['text'][:300].replace('\n', ' '))
    print()
    shown += 1
    if shown == n_show:
        break
