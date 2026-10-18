"""Прогон парсера статей по папке: XML (формат книг) + JSON (абзацы с ролями) + сводка.

    venv/Scripts/python.exe scripts/parse_articles.py [IN_DIR] [OUT_DIR]
По умолчанию IN_DIR=articles, OUT_DIR=articles_parsed.
"""
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding='utf-8')

from app.services.article_parser import parse_article_pdf, to_xml_nodes  # noqa: E402
from app.services.xml_builder import build_tree_structure, dict_to_xml  # noqa: E402

in_dir = sys.argv[1] if len(sys.argv) > 1 else 'articles'
out_dir = sys.argv[2] if len(sys.argv) > 2 else 'articles_parsed'
os.makedirs(out_dir, exist_ok=True)

for path in sorted(glob.glob(os.path.join(in_dir, '*.pdf'))):
    res = parse_article_pdf(path)
    base = os.path.splitext(os.path.basename(path))[0][:60]
    nodes, seq = to_xml_nodes(res)
    with open(os.path.join(out_dir, base + '.xml'), 'w', encoding='utf-8') as f:
        f.write(dict_to_xml(build_tree_structure(nodes), seq))
    with open(os.path.join(out_dir, base + '.json'), 'w', encoding='utf-8') as f:
        json.dump(res, f, ensure_ascii=False, indent=1)

    m = res['meta']
    body = sum(len(t) for s in res['sections'] for t in s['paragraphs'])
    print('=' * 80)
    print(f'{base}  pages={res["pages"]}  body_chars={body}')
    print(f'  title   : {m["title"][:110]}')
    print(f'  authors : {m["authors"][:8]}')
    print(f'  udk     : {m["udk"][:60]}')
    print(f'  abstract: {len(m["abstract"])} симв. «{m["abstract"][:90]}…»')
    print(f'  keywords: {m["keywords"][:8]}')
    print(f'  refs    : {len(m["references"])}  first: {m["references"][0][:70] if m["references"] else "-"}')
    print('  sections:')
    for s in res['sections']:
        chars = sum(len(t) for t in s['paragraphs'])
        print(f'    {"  " * (s["level"] - 1)}[p{s["page"]}] {s["title"][:70]}  ({len(s["paragraphs"])} абз., {chars} симв.)')
    for w in res['warnings']:
        print(f'  ⚠ {w}')
