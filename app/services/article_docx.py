"""
Разбор DOCX-работы (курсовая, отчёт о НИР, статья) в тот же формат, что parse_article_pdf:
{meta, sections, paragraphs, warnings, needs_ocr, lang}.

В DOCX структура размечена стилями, поэтому вёрстка не нужна:
  - заголовки — стили «Heading N» / «Заголовок N» (уровень = N); если стилей нет — короткий
    абзац, целиком жирный, без точки в конце (уровень 1);
  - до первого заголовка — титул: название (первый жирный/крупный абзац), авторы (И.О. Фамилия);
  - «Содержание/Оглавление» и строки с отточием — пропускаются;
  - «Реферат/Аннотация» → meta.abstract; «Список литературы/источников…» → meta.references;
  - подписи «Рисунок N», «Таблица N», заглушки «[ … ]» и короткие центрированные строки
    (элементы рисунков) — не текст;
  - пункты списка присоединяются к абзацу-вводу («Задачи: – … – …»): по одному пункту абзац
    слишком короток для смыслового вектора и для чтения.
Таблицы пропускаются (числа, а не текст).
"""
import re

import docx
from docx.enum.text import WD_ALIGN_PARAGRAPH

from app.services.article_parser import _CAPTION, _NAME, _TOC_LINE, _TOC_TITLE, _latin_ratio, BODY_LANG_LATIN

_HEADING_STYLE = re.compile(r'^(heading|заголовок)\s*(\d)', re.I)
_ABSTRACT_TITLE = re.compile(r'^(реферат|аннотация|abstract)\.?$', re.I)
_REFS_TITLE = re.compile(r'^(список\s+(использованн\w+\s+)?(литературы|источников)|библиографи\w+|литература'
                         r'|список\s+литературы)', re.I)
_APPENDIX_TITLE = re.compile(r'^приложени[ея]\b', re.I)
_LIST_START = re.compile(r'^\s*([–—•·\-]|\d{1,2}[).]|[а-яa-z]\))\s')
_PLACEHOLDER = re.compile(r'^\[.*\]$|^\(источник:', re.I)
SHORT_CENTERED = 120     # знаков: центрированная строка короче — подпись/элемент рисунка, не текст
HEADING_MAX = 150        # жирный абзац длиннее — выделенный текст, а не заголовок


def _level(p) -> int:
    m = _HEADING_STYLE.match(p.style.name or '')
    if m:
        return int(m.group(2))
    if (p.style.name or '').lower() in ('title', 'название'):
        return 0
    return -1


def _all_bold(p) -> bool:
    runs = [r for r in p.runs if r.text.strip()]
    return bool(runs) and all(r.bold or (r.style and r.style.font.bold) for r in runs)


def parse_article_docx(path: str) -> dict:
    d = docx.Document(path)
    meta = {'title': '', 'authors': [], 'udk': '', 'abstract': '', 'keywords': [], 'references': []}
    sections, warnings = [], []
    front, cur, zone = [], None, 'front'          # zone: front | body | toc | abstract | refs | appendix
    has_styles = any(_level(p) > 0 for p in d.paragraphs)
    abstract_parts = []

    for p in d.paragraphs:
        t = re.sub(r'\s+', ' ', p.text).strip()
        if not t:
            continue
        lvl = _level(p)
        if lvl < 0 and not has_styles and _all_bold(p) and len(t) <= HEADING_MAX and not t.endswith(('.', ':')):
            lvl = 1
        if lvl == 0:                                   # стиль «Название»
            meta['title'] = meta['title'] or t
            continue
        if lvl > 0:
            if _TOC_TITLE.match(t):
                zone = 'toc'
            elif _ABSTRACT_TITLE.match(t):
                zone = 'abstract'
            elif _REFS_TITLE.match(t):
                zone = 'refs'
            elif _APPENDIX_TITLE.match(t):
                zone = 'appendix'
            else:
                zone = 'body'
                cur = {'title': t, 'level': lvl, 'page': 0, 'paragraphs': []}
                sections.append(cur)
            continue
        if zone == 'front':
            front.append((p, t))
            continue
        if zone in ('toc', 'appendix') or _TOC_LINE.search(t):
            continue
        if zone == 'abstract':
            abstract_parts.append(t)
            continue
        if zone == 'refs':
            meta['references'].append(t)
            continue
        centered = p.alignment == WD_ALIGN_PARAGRAPH.CENTER
        if _CAPTION.match(t) or _PLACEHOLDER.match(t) or (centered and len(t) < SHORT_CENTERED):
            continue
        is_item = 'list' in (p.style.name or '').lower() or bool(_LIST_START.match(t))
        if is_item and cur['paragraphs']:
            cur['paragraphs'][-1] += '\n' + t
        else:
            cur['paragraphs'].append(t)

    # титул: название — первый жирный/центрированный абзац, авторы — «И.О. Фамилия»
    for p, t in front:
        if _TOC_LINE.search(t):
            continue
        names = _NAME.findall(t)
        if names and len(t) < 300:
            for nm in names:
                if nm not in meta['authors']:
                    meta['authors'].append(nm)
            continue
        if not meta['title'] and (_all_bold(p) or p.alignment == WD_ALIGN_PARAGRAPH.CENTER) and len(t) >= 15:
            meta['title'] = re.sub(r'^\(\d+\)\s*', '', t)
            continue
        if not abstract_parts and len(t) > 200:
            abstract_parts.append(t)              # вводный абзац до первого заголовка
    meta['abstract'] = ' '.join(abstract_parts)
    if not meta['title']:
        meta['title'] = (d.core_properties.title or '').strip()

    body = ' '.join(t for s in sections for t in s['paragraphs'])
    lang = 'en' if len(body) > 500 and _latin_ratio(body) > BODY_LANG_LATIN else 'ru'
    if lang == 'en':
        warnings.append('текст на английском — из анализа исключён')
        sections = []
    if not sections:
        warnings.append('разделы не найдены (нет стилей заголовков и жирных заголовков)')
    if not meta['references']:
        warnings.append('список литературы не найден')
    paragraphs = [{'role': 'body', 'text': t} for s in sections for t in s['paragraphs']]
    return {'file': path, 'pages': 0, 'body_font': 0.0, 'meta': meta, 'sections': sections,
            'paragraphs': paragraphs, 'warnings': warnings, 'needs_ocr': False, 'lang': lang}
