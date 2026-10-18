"""Компактная сводка разбора статей: по строке на статью (JSON из scripts/parse_articles.py).

    venv/Scripts/python.exe scripts/parse_summary.py [PARSED_DIR] [PREFIX]
"""
import glob
import json
import os
import sys

sys.stdout.reconfigure(encoding='utf-8')
parsed_dir = sys.argv[1] if len(sys.argv) > 1 else 'articles_parsed'
prefix = sys.argv[2] if len(sys.argv) > 2 else ''
print(f'{"файл":34} {"авт":>3} {"анн":>4} {"ключ":>4} {"лит":>3} {"разд":>4} {"тело":>6}  предупреждения')
for f in sorted(glob.glob(os.path.join(parsed_dir, prefix + '*.json'))):
    with open(f, encoding='utf-8') as fh:
        d = json.load(fh)
    m = d['meta']
    body = sum(len(t) for s in d['sections'] for t in s['paragraphs'])
    w = '; '.join(x[:40] for x in d['warnings'])
    print(f'{os.path.basename(f)[:34]:34} {len(m["authors"]):>3} {len(m["abstract"]):>4} {len(m["keywords"]):>4} '
          f'{len(m["references"]):>3} {len(d["sections"]):>4} {body:>6}  {w}')
    print(f'{"":35}«{m["title"][:90]}»')
    print(f'{"":35}разделы: {" | ".join(s["title"][:28] for s in d["sections"][:9])}')
