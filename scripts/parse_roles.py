"""Отладка разбора статьи: роли абзацев подряд (JSON из scripts/parse_articles.py).

    venv/Scripts/python.exe scripts/parse_roles.py PARSED_JSON [FROM] [TO]
"""
import json
import sys
from collections import Counter

sys.stdout.reconfigure(encoding='utf-8')
with open(sys.argv[1], encoding='utf-8') as f:
    d = json.load(f)
lo = int(sys.argv[2]) if len(sys.argv) > 2 else 0
hi = int(sys.argv[3]) if len(sys.argv) > 3 else 40
print(Counter(p['role'] for p in d['paragraphs']))
for i, p in enumerate(d['paragraphs'][lo:hi], lo):
    print(f'{i:3} p{p["page"]:<2} {p["role"]:14} {p["text"][:105]}')
