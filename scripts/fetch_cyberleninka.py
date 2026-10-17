"""Скачивание статей КиберЛенинки по рубрикам (областям наук) для корпуса.

    venv/Scripts/python.exe scripts/fetch_cyberleninka.py OUT_DIR RUBRIC[:N] [RUBRIC[:N] ...]
        [--min-year 2015]
    пример: ... articles_cl economics-and-business:25 mathematics:25

Рубрика КиберЛенинки = область наук (классификатор ОЭСР) — она и становится меткой темы
документа в корпусе. Рядом с каждым PDF пишется <имя>.meta.json (title, area, year, url).

Как устроен сайт (DATA.md): страницы рубрики /article/c/<рубрика>/<N> по 20 статей, первые
страницы — старые статьи (2000-е), дальние — новые; поэтому ищем последнюю страницу и берём
случайные страницы из последней трети, фильтруя по году. Пауза между запросами — вежливость
к сайту (DATA.md: 1.5–2 с).
"""
import json
import os
import random
import re
import sys
import time

import httpx

sys.stdout.reconfigure(encoding='utf-8')

BASE = 'https://cyberleninka.ru'
PAUSE = 1.8                    # с между запросами (DATA.md)
PER_PAGE = 20
_ITEM = re.compile(r'<a href="/article/n/([^"]+)"><div class="title">(.*?)</div>.*?<span>(\d{4}) /', re.S)
AREAS = {
    'economics-and-business': 'Экономика и бизнес',
    'mathematics': 'Математика',
    'computer-and-information-sciences': 'Компьютерные и информационные науки',
    'electrical-electronic-information-engineering': 'Электротехника, электроника, ИТ',
    'educational-sciences': 'Науки об образовании',
    'physical-sciences': 'Физика',
    'law': 'Право',
    'psychology': 'Психологические науки',
    'sociology': 'Социологические науки',
    'mechanical-engineering': 'Механика и машиностроение',
}

# trust_env=False: системный прокси Windows (socks4) ломает httpx; curl ходит напрямую — так же
http = httpx.Client(timeout=60, follow_redirects=True, trust_env=False,
                    headers={'User-Agent': 'Mozilla/5.0 (research corpus; book-analyzer)'})


def get(url: str) -> httpx.Response:
    time.sleep(PAUSE)
    return http.get(url)


def list_page(rubric: str, n: int) -> list:
    r = get(f'{BASE}/article/c/{rubric}/{n}')
    if r.status_code != 200:
        return []
    return [(slug, re.sub(r'<[^>]+>', '', t).strip(), int(y)) for slug, t, y in _ITEM.findall(r.text)]


def last_page(rubric: str) -> int:
    lo, hi = 1, 2
    while list_page(rubric, hi):
        lo, hi = hi, hi * 2
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if list_page(rubric, mid):
            lo = mid
        else:
            hi = mid
    return lo


def fetch(out_dir: str, rubric: str, want: int, min_year: int) -> int:
    area = AREAS.get(rubric, rubric)
    last = last_page(rubric)
    print(f'[{rubric}] страниц: {last}', flush=True)
    have = {f.split('__', 1)[1][:-4] for f in os.listdir(out_dir)
            if f.startswith(rubric + '__') and f.endswith('.pdf')}
    got, tried_pages = len(have), 0
    while got < want and tried_pages < 40:
        tried_pages += 1
        page = random.randint(max(1, last * 2 // 3), last)
        for slug, title, year in list_page(rubric, page):
            if got >= want:
                break
            short = slug[:60]
            if year < min_year or short in have:
                continue
            r = get(f'{BASE}/article/n/{slug}/pdf')
            if r.status_code != 200 or not r.content.startswith(b'%PDF'):
                continue
            base = os.path.join(out_dir, f'{rubric}__{short}')
            with open(base + '.pdf', 'wb') as f:
                f.write(r.content)
            with open(base + '.meta.json', 'w', encoding='utf-8') as f:
                json.dump({'title': title, 'area': area, 'rubric': rubric, 'year': year,
                           'url': f'{BASE}/article/n/{slug}', 'source': 'cyberleninka'},
                          f, ensure_ascii=False, indent=1)
            have.add(short)
            got += 1
            print(f'  {got}/{want} {year} {title[:70]}', flush=True)
    return got


def main():
    argv = sys.argv[1:]
    min_year = 2015
    if '--min-year' in argv:
        k = argv.index('--min-year')
        min_year = int(argv[k + 1])
        argv = argv[:k] + argv[k + 2:]
    out_dir, specs = argv[0], argv[1:]
    os.makedirs(out_dir, exist_ok=True)
    for spec in specs:
        rubric, _, n = spec.partition(':')
        fetch(out_dir, rubric, int(n or 20), min_year)


if __name__ == '__main__':
    main()
