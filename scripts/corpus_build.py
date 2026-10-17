"""Загрузка папки PDF-статей в именованный корпус (data/corpus.db).

    venv/Scripts/python.exe scripts/corpus_build.py CORPUS DIR [--area "Область"] [--desc "..."]
Тема документа: --area для всей папки, иначе <имя>.meta.json рядом с PDF (поле area —
так пишет scripts/fetch_cyberleninka.py), иначе — автопредсказание по размеченным документам.
Пропускаются: сканы (нужен OCR), английский текст, тело короче MIN_BODY.
Нужен LM Studio с моделью эмбеддингов (кэш .cache/embeddings.jsonl).
"""
import glob
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding='utf-8')

from app.analysis.corpus import CorpusStore  # noqa: E402
from app.services.article_parser import parse_article_pdf  # noqa: E402

# Тело короче ~1.5 страницы — тезисы/аннотации/обрывки разбора: шумные векторы, не статья.
MIN_BODY = 3000


def main():
    argv = sys.argv[1:]
    opts = {}
    for key in ('--area', '--desc'):
        if key in argv:
            k = argv.index(key)
            opts[key] = argv[k + 1]
            argv = argv[:k] + argv[k + 2:]
    corpus, in_dir = argv[0], argv[1]
    store = CorpusStore()
    cid = store.corpus_id(corpus, create=True)
    if '--desc' in opts:
        store.db.execute('UPDATE corpus SET description=? WHERE id=?', (opts['--desc'], cid))
        store.db.commit()
    have = {d['name'] for d in store.documents(corpus)}
    added, skipped = 0, []
    t0 = time.time()
    for path in sorted(glob.glob(os.path.join(in_dir, '*.pdf'))):
        name = os.path.splitext(os.path.basename(path))[0]
        if name in have:
            continue
        meta = {}
        if os.path.exists(os.path.join(in_dir, name + '.meta.json')):
            with open(os.path.join(in_dir, name + '.meta.json'), encoding='utf-8') as f:
                meta = json.load(f)
        try:
            parsed = parse_article_pdf(path)
        except Exception as e:                                  # битый PDF не должен рвать прогон
            skipped.append((name, f'ошибка разбора: {e}'))
            continue
        body = sum(len(t) for s in parsed['sections'] for t in s['paragraphs'])
        if parsed.get('needs_ocr'):
            skipped.append((name, 'скан'))
            continue
        if parsed.get('lang', 'ru') != 'ru':
            skipped.append((name, 'не русский'))
            continue
        if body < MIN_BODY:
            skipped.append((name, f'тело {body} знаков'))
            continue
        r = store.add_document(corpus, name, parsed, area=opts.get('--area') or meta.get('area'), meta=meta)
        added += 1
        print(f'+ [{r["area"]} {r["area_source"]} {r["area_conf"]:.2f}] {r["chunks"]:3d} фр.  '
              f'{(meta.get("title") or parsed["meta"]["title"])[:70]}', flush=True)
    print(f'\nДобавлено {added} за {time.time() - t0:.0f} с, пропущено {len(skipped)}:')
    for n, why in skipped:
        print(f'  - {n[:60]}: {why}')


if __name__ == '__main__':
    main()
