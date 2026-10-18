"""
Парсер одиночной научной статьи (PDF без оглавления) — этап 2, plan_001 фаза 1a.

В отличие от книг, у статьи нет оглавления, зато заголовки стоят прямо в тексте —
структура восстанавливается по вёрстке (PyMuPDF spans), без поиска по тексту и без моделей.

Конвейер:
  1. extract_lines     — строки с признаками вёрстки (кегль, жирность, капс, координаты);
                         строки на одной высоте склеиваются (выключка по ширине дробит их).
  2. mark_noise        — колонтитулы и номера страниц (верх/низ страницы + повтор).
  3. build_paragraphs  — абзацы по отступу/интервалу/короткой строке, перенос слов,
                         кандидаты в заголовки (отдельная строка жирным/крупнее/капсом).
  4. split_articles    — в одном PDF бывает хвост чужой статьи и начало следующей
                         (выгрузки из журналов): режем по блокам «Аннотация/Ключевые слова».
  5. assign_zones      — шапка (название, авторы, аннотация, ключевые слова),
                         тело (разделы), хвост (литература, сведения об авторах, англ. дубли).

Выход parse_article_pdf: dict с meta / sections / paragraphs / warnings.
to_xml_nodes превращает его в формат xml_builder (тот же XML-формат дерева, что у книг).
"""
import re
import statistics
from collections import Counter
from dataclasses import dataclass, field

import fitz

# --- Пороги вёрстки (в pt). Подобраны на 5 статьях articles/ (2026-10-17). ---
NOISE_BAND_FRACTION = 0.09      # верх/низ страницы, где живут колонтитулы
SAME_LINE_DY = 2.0              # строки ближе по вертикали — одна строка (выключка)
# Двухколоночная страница: промежуток, разделяющий ≥40% рядов на участке от первой до последней
# разделённой строки (и не меньше 8 рядов).
# В одноколоночном тексте случайный x попадает в пробел между словами примерно в 10–20%
# рядов; в двух колонках — почти во всех, кроме рядов во всю ширину (шапка, рисунки).
COLUMN_ROW_SHARE = 0.4
COLUMN_MIN_ROWS = 8
COLUMN_RUN_GAP = 3       # разрыв в серии разделённых строк (подпись/формула во всю ширину)
PARA_GAP_FACTOR = 1.6           # интервал > 1.6 × обычного → новый абзац
INDENT_MIN, INDENT_MAX = 6, 60  # абзацный отступ первой строки
SHORT_LINE_SLACK = 40           # строка короче правого поля на 40pt → конец абзаца
HEADING_MAX_CHARS = 200
HEADING_MAX_LINES = 4
HEADING_FOLLOW_MIN_CHARS = 100  # за настоящим заголовком идёт текст, а не ячейки таблицы
RUNIN_MAX_CHARS = 80            # «Введение. Текст…» — жирный заголовок в начале абзаца
SPAN_SPACE_FACTOR = 0.15        # зазор > 0.15 кегля между спанами = пробел
SMALL_FONT_RATIO = 0.75         # кегль < 0.75 основного — сноски, копирайты, мелочь таблиц
SCRIPT_SWITCH_LATIN = 0.6       # строка «латиница» vs «кириллица» (англ. дубли названия)
TABLE_GAP_FACTOR = 3.0          # пустота > 3 кеглей между кусками одной строки = ячейки таблицы
RUNNING_HEADER_MAX_CHARS = 100  # колонтитул вне полосы: короткая строка …
RUNNING_HEADER_MIN_PAGES = 3    # … на одной высоте на ≥3 страницах
HEADING_MIN_ALPHA = 0.6         # в заголовке ≥60% непробельных символов — буквы (не формула)
TITLE_MIN_SCORE = 3.0           # название без ключевых слов: рядом авторы (3) или капс+крупнее (4)
NEXT_ARTICLE_MIN_GAP = 5        # следующая статья в файле — не ближе 5 абзацев к предыдущей
TITLE_MIN_WORDS = 3             # название короче 3 слов — скорее рубрика журнала
UNLABELED_ABSTRACT_MAX = 1500   # аннотация без метки: ≤3 абзаца, ≤1500 симв., дальше англ. блок
BODY_LATIN_DROP = 0.8           # абзац тела русской статьи с >80% латиницы — англ. дубль, не текст
BODY_LANG_LATIN = 0.6           # >60% латиницы во всём, кроме шапки, — статья на английском
TEXT_LAYER_MIN_CHARS_PER_PAGE = 200  # меньше — скан без текстового слоя, нужен OCR

_INLINE_HYPHEN = re.compile(r'(?<=[а-яё])- (?=[а-яё])')
_TOC_LINE = re.compile(r'(\.{4,}|…{2,})\s*\d{1,4}$')        # «Введение ........ 3»
_TOC_TITLE = re.compile(r'^(содержание|оглавление)\.?$', re.I)
_CAPTION = re.compile(r'^(рис(унок|\.)|таблица|табл\.|figure|fig\.|table)\s*\d', re.I)
_LIST_ITEM = re.compile(r'^\(?\d{1,2}\)\s')
_LIST_CONT = re.compile(r'^(\(?\d{1,2}\)|[а-яa-z]\)|[–—•·-])\s')   # «1) …», «а) …», «– …»
_NUMBERED = re.compile(r'^(\d+(?:\.\d+)*)\.?\s+\S')
_NUM_ONLY = re.compile(r'^\d+(?:\.\d+)*\.?$')
_PAGE_NUM = re.compile(r'^(\d{1,4}|\d{1,4}\s*\|\s*\d{1,4}|[-–—]\s*\d{1,4}\s*[-–—])$')

# Якоря зон (начало абзаца, без учёта регистра)
_A_UDK = re.compile(r'^(удк|ббк|doi|issn)\b', re.I)
_A_ABSTRACT = re.compile(r'^(аннотация|резюме|реферат)\s*[.:]?\s*', re.I)
_A_ABSTRACT_EN = re.compile(r'^(abstract|summary)\s*[.:]?\s*', re.I)
_A_KEYWORDS = re.compile(r'^ключевые\s+слова(\s+и\s+фразы)?\s*[.:]?\s*', re.I)
_A_KEYWORDS_EN = re.compile(r'^key\s*-?\s*words(\s+and\s+phrases)?\s*[.:]?\s*', re.I)
_A_CITE = re.compile(r'^(для\s+цитирования|for\s+citation)', re.I)
_A_REFS = re.compile(
    r'^(список\s+(использованной\s+|цитируемой\s+)?(литературы|информационных\s+источников|'
    r'использованных\s+источников|источников)'
    r'|библиографический\s+список|библиографические\s+ссылки|литература|references|bibliography)\b',
    re.I)
_A_AUTHORS_INFO = re.compile(
    r'^(информация\s+об\s+авторах|сведения\s+об\s+авторах|об\s+авторах|information\s+about\s+the\s+authors)',
    re.I)
_A_RECEIVED = re.compile(r'^(статья|работа)\s+поступила|^the\s+article\s+was\s+submitted', re.I)
_REF_ITEM = re.compile(r'^(\[\d{1,3}\]|\d{1,3}\.)\s*\S')
_ANCHOR_START = re.compile(
    r'^(удк|doi|аннотация|резюме|abstract|ключевые\s+слова|key\s*-?\s*words|для\s+цитирования|'
    r'for\s+citation|список\s+(литературы|источников|информационных|использованных)|'
    r'библиографический\s+список|библиографические\s+ссылки|'
    r'информация\s+об\s+авторах|сведения\s+об\s+авторах|статья\s+поступила|работа\s+поступила)\b',
    re.I)

_RUNIN_KNOWN = re.compile(
    r'^(введение|заключение|выводы?|результаты(\s+исследования)?|обсуждение(\s+результатов)?|'
    r'материалы\s+и\s+методы|методы(\s+исследования)?|постановка\s+задачи|цель\s+работы)\s*[.:]\s+(?=\S)',
    re.I)

_KNOWN_SECTIONS = re.compile(
    r'^(введение|заключение|выводы?|результаты|обсуждение|методы|материалы\s+и\s+методы|'
    r'постановка\s+задачи|основная\s+часть|благодарности)\b', re.I)


@dataclass
class Line:
    page: int
    text: str
    size: float
    bold: bool
    caps: bool
    x0: float
    x1: float
    y0: float
    y1: float
    lead_bold: str = ''     # ведущие выделенные спаны (для «Введение. Текст…»)
    noise: bool = False
    small: bool = False
    table: bool = False     # строка таблицы: между кусками строки большие пустоты


@dataclass
class Para:
    lines: list
    text: str = ''
    heading: bool = False
    runin_title: str = ''   # заголовок в начале абзаца
    role: str = 'body'
    page: int = 0
    size: float = 0.0
    bold: bool = False
    caps: bool = False
    extra: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# 1. Строки
# ---------------------------------------------------------------------------

def _is_bold(span) -> bool:
    return bool(span['flags'] & 16) or bool(re.search(r'bold|BX', span['font'], re.I))


def _is_italic(span) -> bool:
    return bool(span['flags'] & 2) or bool(re.search(r'italic|oblique', span['font'], re.I))


def _is_caps(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    return len(letters) >= 4 and sum(c.isupper() for c in letters) / len(letters) > 0.9


def _join_spans(spans) -> str:
    """Пробел между спанами — только если между ними есть зазор. Иначе капитель
    («В» + «ВЕДЕНИЕ») и пунктуация («ТЕКСТОВ» + «,») обрастают лишними пробелами."""
    out, prev = '', None
    for sp in spans:
        t = sp['text']
        if prev is not None:
            gap = sp['bbox'][0] - prev['bbox'][2]
            if gap > SPAN_SPACE_FACTOR * sp['size'] and not out.endswith(' ') and not t.startswith(' '):
                out += ' '
        out += t
        prev = sp
    return re.sub(r'\s+', ' ', out).strip()


_MOJIBAKE = re.compile(r'[À-ÿ]')
_MOJIBAKE_RUN = re.compile(r'[À-ÿ]+(?:[ .,;:()-]+[À-ÿ]+)*')
_LETTER_SPACED = re.compile(r'(?<!\S)(?:\w ){2,}\w(?!\S)')


def _repair_text(text: str) -> str:
    """Чинит два дефекта текстового слоя старых PDF:
    - cp1251, прочитанный как latin-1: «Îïðåäåëåíèå» → «Определение»;
    - разрядка: «П р и м е р» → «Пример»."""
    if len(_MOJIBAKE.findall(text)) >= 3:
        # чиним по участкам: в строке бывает настоящая кириллица/формулы вперемешку
        # с перекодированными словами («Γ∼(xi è îáðàòíîå íå÷åòêîå»)
        def fix(m):
            try:
                return m.group(0).encode('latin-1').decode('cp1251')
            except (UnicodeEncodeError, UnicodeDecodeError):
                return m.group(0)
        text = _MOJIBAKE_RUN.sub(fix, text)
    return _LETTER_SPACED.sub(lambda m: m.group(0).replace(' ', ''), text)


def _find_gutter(rows: list, width: float):
    """x межколонного промежутка или None. Строка (ряд фрагментов одной высоты) «за колонки»,
    если в ней есть текст слева и справа от x, а сам x не накрыт ни одним фрагментом. В
    одноколоночном тексте такой x случаен (пробел между словами) и набирает мало рядов."""
    if len(rows) < COLUMN_MIN_ROWS:
        return None
    best, best_x = 0, None
    for x in range(int(width * 0.3), int(width * 0.7)):
        hits = []
        for k, row in enumerate(rows):
            left = right = False
            for b, _ in row:
                if b[0] < x < b[2]:
                    break
                left |= b[2] <= x
                right |= b[0] >= x
            else:
                if left and right:
                    hits.append(k)
        # долю считаем на участке, где колонки есть: самая длинная серия разделённых строк с
        # разрывами ≤ COLUMN_RUN_GAP. На первой странице над колонками идут шапка и аннотация во
        # всю ширину (и колонтитул, тоже «разделённый») — по всей странице колонок было 30% строк,
        # и строки двух колонок перемешивались (s023, разметка user)
        run, runs = [], []
        for k in hits:
            if run and k - run[-1] > COLUMN_RUN_GAP:
                runs.append(run)
                run = []
            run.append(k)
        if run:
            runs.append(run)
        for r in runs:
            if len(r) >= COLUMN_MIN_ROWS and len(r) >= COLUMN_ROW_SHARE * (r[-1] - r[0] + 1) and len(r) > best:
                best, best_x = len(r), x
    return best_x


def _column_order(rows: list, width: float) -> list:
    """Ряды → части строк в порядке чтения. Двухколоночный участок (ряды, не накрывающие
    промежуток) читается сначала левой колонкой, потом правой; ряд во всю ширину (название,
    подпись под широким рисунком) разрывает участок."""
    g = _find_gutter(rows, width)
    if g is None:
        return rows
    out, left, right = [], [], []
    for row in rows:
        if any(b[0] < g < b[2] for b, _ in row):
            out += left + right + [row]
            left, right = [], []
            continue
        lpart = [f for f in row if f[0][2] <= g]
        rpart = [f for f in row if f[0][0] >= g]
        if lpart:
            left.append(lpart)
        if rpart:
            right.append(rpart)
    return out + left + right


def extract_lines(doc) -> tuple[list, dict]:
    lines, geo = [], {}
    for pi, page in enumerate(doc):
        raw = []
        for b in page.get_text('dict', sort=True)['blocks']:
            for l in b.get('lines', []):
                spans = [s for s in l['spans'] if s['text'].strip()]
                if spans:
                    raw.append((l['bbox'], spans))
        # склейка строк одной высоты (выключка по ширине дробит строку на слова) — в пределах
        # колонки: в двухколоночной вёрстке строки левой и правой колонки на одной высоте
        # иначе сливались в одну («социально-эконо- данные, разделен…», session 020)
        raw.sort(key=lambda r: (round(r[0][1]), r[0][0]))
        rows = []
        for bbox, spans in raw:
            if rows and abs(rows[-1][0][0][1] - bbox[1]) <= SAME_LINE_DY:
                rows[-1].append((bbox, spans))
            else:
                rows.append([(bbox, spans)])
        merged = []
        for part in _column_order(rows, page.rect.width):
            bbox = (min(b[0] for b, _ in part), min(b[1] for b, _ in part),
                    max(b[2] for b, _ in part), max(b[3] for b, _ in part))
            merged.append((bbox, [s for _, ss in part for s in ss]))
        for bbox, spans in merged:
            xs = sorted((s['bbox'][0], s['bbox'][2]) for s in spans)
            max_gap = max((b[0] - a[1] for a, b in zip(xs, xs[1:])), default=0.0)
            spans.sort(key=lambda s: s['bbox'][0])
            text = _repair_text(_join_spans(spans))
            if not text:
                continue
            by_size, bold_chars = Counter(), 0
            for s in spans:
                n = len(s['text'].strip())
                by_size[round(s['size'], 1)] += n
                bold_chars += n if _is_bold(s) else 0
            # ведущие выделенные спаны (жирный ИЛИ курсив) — заголовок в начале абзаца:
            # «Введение.» жирным (80.pdf) или «Реализация проекта.» курсивом (о-применении)
            lead = []
            for s in spans:
                if not (_is_bold(s) or _is_italic(s)):
                    break
                lead.append(s['text'].strip())
            total = max(1, sum(by_size.values()))
            lines.append(Line(
                page=pi, text=text, size=by_size.most_common(1)[0][0],
                bold=bold_chars / total > 0.6, caps=_is_caps(text),
                x0=bbox[0], x1=bbox[2], y0=bbox[1], y1=bbox[3],
                lead_bold=' '.join(lead).strip(),
                table=max_gap > TABLE_GAP_FACTOR * by_size.most_common(1)[0][0],
            ))
        geo[pi] = (page.rect.width, page.rect.height)
    return lines, geo


# ---------------------------------------------------------------------------
# 2. Колонтитулы
# ---------------------------------------------------------------------------

def _norm_noise(text: str) -> str:
    return re.sub(r'\d+', '#', text.lower()).strip()


def mark_noise(lines: list, geo: dict) -> None:
    """Колонтитул = строка в верхней/нижней полосе страницы, повторяющаяся на ≥2
    страницах (цифры нормализованы), либо голый номер страницы. Строки в теле не трогаем:
    «О.О. ВАРЛАМОВ» как автор на 1-й странице остаётся, как колонтитул — уходит."""
    def in_band(l):
        h = geo[l.page][1]
        return l.y0 < h * NOISE_BAND_FRACTION or l.y1 > h * (1 - NOISE_BAND_FRACTION)

    pages_of, at_y = {}, {}
    for l in lines:
        if in_band(l):
            pages_of.setdefault(_norm_noise(l.text), set()).add(l.page)
        if len(l.text) <= RUNNING_HEADER_MAX_CHARS:
            at_y.setdefault((_norm_noise(l.text), round(l.y0 / 4)), set()).add(l.page)
    first_last = set()
    for pi in geo:
        pl = [l for l in lines if l.page == pi]
        if pl:
            first_last.update((id(pl[0]), id(pl[-1])))
    for l in lines:
        key_y = (_norm_noise(l.text), round(l.y0 / 4))
        if len(at_y.get(key_y, ())) >= RUNNING_HEADER_MIN_PAGES:
            # одна и та же строка на одной высоте на ≥3 страницах — колонтитул вне полосы
            # («Ортогональные и биортогональные базисы» в ode__o-teh). Заголовок раздела
            # «Доказательство» на разных страницах стоит на разной высоте — не трогаем.
            l.noise = True
        elif _PAGE_NUM.match(l.text) and (in_band(l) or id(l) in first_last):
            l.noise = True         # номер страницы — первая/последняя строка страницы
        elif in_band(l) and len(pages_of.get(_norm_noise(l.text), ())) >= 2:
            l.noise = True


# ---------------------------------------------------------------------------
# 3. Абзацы и заголовки
# ---------------------------------------------------------------------------

def _join(prev: str, nxt: str) -> str:
    if prev.endswith('-') and nxt[:1].islower():
        return prev[:-1] + nxt          # перенос слова: «ис-» + «кусственного»
    return prev + ' ' + nxt


def _style(l: Line, body: float):
    return (l.bold, l.caps, round(l.size))


def _heading_style(l: Line, body: float) -> bool:
    return l.bold or l.size > body + 0.5 or l.caps


def _plausible_heading(text: str, n_lines: int) -> bool:
    t = text.strip()
    if not t or len(t) > HEADING_MAX_CHARS or n_lines > HEADING_MAX_LINES:
        return False
    if t[0].islower() or t.endswith((',', ';', ':')) or t[0] in '({[«"':
        return False
    if _CAPTION.match(t) or _LIST_ITEM.match(t):
        return False
    if t.endswith('.') and len(t) > 60 and not _NUMBERED.match(t):
        return False                    # жирное ПРЕДЛОЖЕНИЕ в тексте, не заголовок
    if sum(c.isalpha() for c in t) < 3:
        return False
    non_space = [c for c in t if not c.isspace()]
    if '=' in t or sum(c.isalpha() for c in non_space) / len(non_space) < HEADING_MIN_ALPHA:
        return False                    # формула: «F :=NW+NB+W», «4 4 η η 3 η …»
    if not re.search(r'[а-яёa-z]{3,}', t, re.I):
        return False                    # нет ни одного слова из 3+ букв: «D~ 1 о D~ 2»
    return True


def build_paragraphs(lines: list, geo: dict) -> tuple[list, float]:
    content = [l for l in lines if not l.noise]
    if not content:
        return [], 0.0
    sizes = Counter()
    for l in content:
        sizes[l.size] += len(l.text)
    body = sizes.most_common(1)[0][0]

    # поля и интерлиньяж по основному кеглю
    left, right, gaps = {}, {}, []
    for pi in geo:
        bl = [l for l in content if l.page == pi and abs(l.size - body) < 0.6]
        if bl:
            xs0 = sorted(l.x0 for l in bl)
            xs1 = sorted(l.x1 for l in bl)
            left[pi] = xs0[len(xs0) // 10]
            right[pi] = xs1[int(len(xs1) * 0.9)]
    for a, b in zip(content, content[1:]):
        if a.page == b.page and abs(a.size - body) < 0.6 and abs(b.size - body) < 0.6:
            d = b.y0 - a.y0
            if 0 < d < body * 3:
                gaps.append(d)
    leading = statistics.median(gaps) if gaps else body * 1.2

    paras, cur = [], []

    def flush():
        if cur:
            paras.append(Para(lines=list(cur)))
            cur.clear()

    # мелкий кегль помечаем, но не выкидываем: в шапке аннотация/УДК бывают мелкими
    # (тегирование: аннотация 8pt, ключевые слова 7pt при теле 10pt). Из ТЕЛА мелочь
    # (сноски, копирайты, ячейки таблиц) убирается в assign_zones.
    for l in content:
        if l.size < body * SMALL_FONT_RATIO:
            l.small = True

    def latin(l):
        return len([c for c in l.text if c.isalpha()]) >= 10 and _latin_ratio(l.text) > SCRIPT_SWITCH_LATIN

    for i, l in enumerate(content):
        prev = content[i - 1] if i else None
        nxt = content[i + 1] if i + 1 < len(content) else None
        new = prev is None
        if prev is not None:
            hs_prev, hs_cur = _heading_style(prev, body), _heading_style(l, body)
            rm = right.get(prev.page, prev.x1)
            # отступ первой строки: строка правее предыдущей, а следующая возвращается левее.
            # Висячий отступ списка (все строки пункта на одном x) абзацы не дробит.
            first_indent = (INDENT_MIN < l.x0 - prev.x0 < INDENT_MAX and nxt is not None
                            and nxt.page == l.page and l.x0 - nxt.x0 > INDENT_MIN and not hs_cur)
            if l.page != prev.page:
                # перенос абзаца через страницу: продолжаем, если прошлая строка не кончилась
                new = prev.x1 < rm - SHORT_LINE_SLACK or prev.text.endswith(('.', ':', '!', '?'))
                new = new or hs_prev != hs_cur
            else:
                # интерлиньяж растёт с кеглем: строки названия 16pt при теле 10pt стоят
                # реже, но это один абзац («Обучение с подкреплением… / параметров… / рынках»)
                scale = max(1.0, min(l.size, prev.size) / body)
                new = (l.y0 - prev.y0 > leading * PARA_GAP_FACTOR * scale
                       or first_indent
                       or (prev.x1 < rm - SHORT_LINE_SLACK and prev.text.endswith(('.', ':', '!', '?', ';')))
                       or abs(l.size - prev.size) > 1.0
                       or hs_prev != hs_cur
                       or (hs_cur and _style(l, body) != _style(prev, body)))
            # строка оглавления «ЗАКЛЮЧЕНИЕ ....... 82» — всегда конец абзаца (иначе следующая
            # страница «ВВЕДЕНИЕ» прилипала к оглавлению, ВКР session 021)
            new = (new or bool(_ANCHOR_START.match(l.text)) or latin(l) != latin(prev)
                   or l.table != prev.table or bool(_TOC_LINE.search(prev.text)))
        if new:
            flush()
        cur.append(l)
    flush()

    for p in paras:
        text = p.lines[0].text
        for l in p.lines[1:]:
            text = _join(text, l.text)
        # перенос внутри строки текстового слоя: «конфигу- рация», «маршру- тизаторы» (s023, user:
        # «криво написанные слова»); дефис+пробел+строчная после строчной — в русском тексте перенос
        p.text = _INLINE_HYPHEN.sub('', text.strip())
        p.page = p.lines[0].page
        p.size = statistics.median(l.size for l in p.lines)
        p.bold = all(l.bold for l in p.lines)
        p.caps = all(l.caps for l in p.lines)
        p.extra['small'] = all(l.small for l in p.lines)
        p.extra['table'] = sum(l.table for l in p.lines) * 2 > len(p.lines)
        p.heading = (all(_heading_style(l, body) for l in p.lines) and not p.extra['table']
                     and _plausible_heading(p.text, len(p.lines)))
        first = p.lines[0]
        lead = first.lead_bold
        if (not p.heading and lead and lead != first.text and len(lead) <= RUNIN_MAX_CHARS
                and lead.endswith(('.', ':')) and _plausible_heading(lead.rstrip('.:'), 1)):
            p.runin_title = lead.rstrip('.: ')
        elif not p.heading:
            m = _RUNIN_KNOWN.match(p.text)
            if m:
                p.runin_title = m.group(0).strip().rstrip('.:').strip()
                p.extra['runin_len'] = m.end()

    # «1.» отдельной строкой + «Описание …» → один заголовок
    merged = []
    for p in paras:
        if (merged and merged[-1].heading and p.heading and _NUM_ONLY.match(merged[-1].text)
                and merged[-1].page == p.page):
            merged[-1].text = merged[-1].text + ' ' + p.text
            merged[-1].lines += p.lines
            continue
        merged.append(p)
    paras = merged

    # заголовок подтверждается тем, что за ним идёт текст (а не ячейки таблицы);
    # идём с конца, чтобы цепочка «1. Раздел / 1.1. Подраздел / текст» подтверждалась целиком
    ok_next = False
    for p in reversed(paras):
        if p.heading:
            known = bool(_KNOWN_SECTIONS.match(p.text) or _NUMBERED.match(p.text))
            if not (ok_next or known):
                p.heading = False
            ok_next = p.heading
        else:
            ok_next = len(p.text) >= HEADING_FOLLOW_MIN_CHARS
    return paras, body


# ---------------------------------------------------------------------------
# 4. Несколько статей в одном PDF
# ---------------------------------------------------------------------------

def _latin_ratio(text: str) -> float:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    return sum('a' <= c.lower() <= 'z' for c in letters) / len(letters)


def _is_reference_like(p: Para) -> bool:
    return bool(_REF_ITEM.match(p.text)) or bool(_A_REFS.match(p.text))


def _title_candidates(paras: list, body_size: float) -> list:
    """Кандидаты в название статьи без опоры на «Ключевые слова» (старые журналы:
    УДК / авторы / НАЗВАНИЕ / текст): выделенный (капс/жирный/крупнее) кириллический
    абзац 15–300 симв. Возвращает [(idx, score, authors_near)]."""
    out = []
    for i, p in enumerate(paras):
        t = p.text
        styled = p.caps or p.bold or p.size > body_size + 0.5
        if (not styled or len(t) < 15 or len(t) > 300 or _latin_ratio(t) > 0.5
                or _NAME.search(t) or _AFFIL.search(t) or _ANCHOR_START.match(t)
                or _KNOWN_SECTIONS.match(t) or _CAPTION.match(t)):
            continue
        near = paras[max(0, i - 2): i] + paras[i + 1: i + 3]
        authors_near = any(_NAME.search(q.text) and len(q.text) < 300 for q in near)
        score = 3 * authors_near + 2 * p.caps + 2 * (p.size > body_size + 0.5) + p.bold - 0.05 * i
        if len(t.split()) < TITLE_MIN_WORDS:
            score -= 2                  # рубрика журнала («Краткие сообщения»), не название
        out.append((i, score, authors_near))
    return out


def _find_title_idx(paras: list, body_size: float) -> int | None:
    """Название = лучший кандидат в первой половине файла (рядом с авторами — сильнее всего)."""
    n = len(paras)
    cands = [c for c in _title_candidates(paras, body_size) if c[0] < max(15, n // 2)]
    best = max(cands, key=lambda c: c[1], default=None)
    return best[0] if best and best[1] >= TITLE_MIN_SCORE else None


def _header_start(paras: list, t: int) -> int:
    """От названия назад по УДК / строкам авторов (не дальше 3 абзацев)."""
    s = t
    while s > 0 and t - s < 3 and (_A_UDK.match(paras[s - 1].text)
                                   or (_NAME.search(paras[s - 1].text) and len(paras[s - 1].text) < 200)):
        s -= 1
    return s


def split_articles(paras: list, body_size: float = 0.0) -> list:
    """Возвращает список (start, end) индексов абзацев для каждой статьи в файле.
    Статья = блок шапки вокруг русских «Ключевые слова». Начало статьи — откатываемся
    от аннотации назад по коротким абзацам шапки (название/авторы/аффилиация/УДК) до
    длинного абзаца тела или пункта литературы предыдущей статьи.
    Без ключевых слов — опора на название (_find_title_idx): всё до УДК/авторов перед
    названием — чужой текст (хвост предыдущей статьи журнала)."""
    kw = [i for i, p in enumerate(paras) if _A_KEYWORDS.match(p.text)]
    if not kw:
        t = _find_title_idx(paras, body_size)
        if t is None:
            return [(0, len(paras))]
        # следующие статьи журнала в том же файле: ещё одно «авторы + НАЗВАНИЕ» дальше по тексту
        starts = [_header_start(paras, t)]
        for i, _score, authors_near in _title_candidates(paras, body_size):
            p = paras[i]
            # название следующей статьи — КАПС или крупнее основного текста; жирная подпись
            # к таблице рядом с колонтитулом авторов статьёй не считается
            strong = p.caps or p.size > body_size + 0.5
            if i > t + NEXT_ARTICLE_MIN_GAP and authors_near and strong:
                s = _header_start(paras, i)
                if s > starts[-1] + NEXT_ARTICLE_MIN_GAP:
                    starts.append(s)
        return [(s, starts[k + 1] if k + 1 < len(starts) else len(paras)) for k, s in enumerate(starts)]
    if len(kw) == 1:
        return [(0, len(paras))]
    starts = []
    for k in kw:
        j = k
        # к аннотации (или к абзацу перед ключевыми словами)
        while j > 0 and not _A_ABSTRACT.match(paras[j].text) and k - j < 3:
            j -= 1
        # назад по шапке
        while j > 0:
            q = paras[j - 1]
            if _is_reference_like(q) or (len(q.text) > 250 and not q.heading):
                break
            if starts and j - 1 <= starts[-1]:
                break
            j -= 1
        starts.append(j)
    bounds = []
    for n, s in enumerate(starts):
        e = starts[n + 1] if n + 1 < len(starts) else len(paras)
        bounds.append((s, e))
    return bounds


# ---------------------------------------------------------------------------
# 5. Зоны и сборка
# ---------------------------------------------------------------------------

_AFFIL = re.compile(r'университет|институт|академи|имени|\bим\.|кафедр|ФГБОУ|ФГУП|ООО|e-mail', re.I)
_NAME = re.compile(r'[А-ЯЁ]\.\s?[А-ЯЁ]\.\s?[А-ЯЁ](?:[а-яё]+|[А-ЯЁ]+\b)|[А-ЯЁ][а-яё]+\s+[А-ЯЁ]\.\s?[А-ЯЁ]\.')


_REF_LOOK = re.compile(r'//|\bС\.\s*\d|\bМ\.\s*:|\bP\.\s*\d|изд|\b(19|20)\d{2}\b|\bпод\s+ред|\bvol\b|\bpp?\.', re.I)


def _unlabeled_refs_start(paras: list, front_end: int) -> int | None:
    """Литература без заголовка: во второй половине статьи абзац «1. …», похожий на
    библиографическое описание (//, «С. 12», «М.:», год, «Изд-во»), и за ним «2. …»."""
    n = len(paras)
    for i in range(max(front_end, n // 2), n):
        t = paras[i].text
        if re.match(r'^\[?1[\].]\s', t) and _REF_LOOK.search(t):
            rest = ' '.join(p.text for p in paras[i: i + 3])
            if re.search(r'(^|\s)\[?2[\].]\s', rest) or i >= n - 2:
                return i
    return None


def _author_key(name: str) -> str:
    """Ключ автора без учёта порядка и регистра: фамилия + инициалы."""
    surname = max(re.findall(r'[А-ЯЁа-яё]{2,}', name), key=len, default='').lower()
    initials = ''.join(sorted(re.findall(r'([А-ЯЁ])\.', name)))
    return surname + '|' + initials


def _strip_label(rx, text: str) -> str:
    return rx.sub('', text, count=1).strip()


def _level(title: str) -> int:
    m = _NUMBERED.match(title)
    if m:
        return min(3, m.group(1).count('.') + 1)
    return 1


def assign_zones(paras: list, body_size: float) -> dict:
    meta = {'title': '', 'authors': [], 'udk': '', 'abstract': '', 'keywords': [],
            'references': []}
    n = len(paras)
    kw_idx = next((i for i, p in enumerate(paras) if _A_KEYWORDS.match(p.text)), None)
    abs_idx = next((i for i, p in enumerate(paras) if _A_ABSTRACT.match(p.text)), None)
    refs_idx = next((i for i, p in enumerate(paras) if _A_REFS.match(p.text)
                     and (p.heading or len(p.text) < 120)), None)

    # аннотация и ключевые слова В КОНЦЕ, после литературы («Доклады АМАН»: тело → ЛИТЕРАТУРА →
    # ABSTRACT/Key words → АННОТАЦИЯ/Ключевые слова). Иначе шапкой считалось всё до ключевых
    # слов — вместе с телом (15 из 50 статей КиберЛенинки, session 020). Шапка в начале тогда —
    # только название и авторы (ветка «без меток»), а хвостовой блок разбираем отдельно.
    tail_start = tail_abs = tail_kw = None
    if (refs_idx is not None and kw_idx is not None and refs_idx < kw_idx
            and (abs_idx is None or refs_idx < abs_idx)):
        tail_abs, tail_kw = abs_idx, kw_idx
        tail_start = next((i for i in range(refs_idx + 1, n)
                           if _A_ABSTRACT_EN.match(paras[i].text) or _A_ABSTRACT.match(paras[i].text)
                           or i == kw_idx), kw_idx)
        kw_idx = abs_idx = None

    # конец шапки: после ключевых слов + англ. дублей / «Для цитирования»
    front_end = 0
    if kw_idx is not None:
        front_end = kw_idx + 1
        # после ключевых слов идут англ. дубли, «Для цитирования», «©», URL — до первого
        # кириллического заголовка / заголовка-в-абзаце / длинного кириллического абзаца
        while front_end < n:
            p = paras[front_end]
            cyr = _latin_ratio(p.text) <= 0.5
            if _ANCHOR_START.match(p.text) or p.text.startswith('©'):
                front_end += 1
                continue
            if cyr and (p.heading or p.runin_title or len(p.text) > 300):
                break
            front_end += 1
    elif abs_idx is not None:
        front_end = abs_idx + 1
    unlabeled_abstract, title_idx = [], None
    if kw_idx is None and abs_idx is None:
        # старый журнал без меток: шапка = до названия + авторы/аффилиации после него;
        # 1–3 кириллических абзаца сразу за ними, за которыми идёт англ. блок, — аннотация
        title_idx = _find_title_idx(paras, body_size)
        if title_idx is not None:
            front_end = title_idx + 1
            while front_end < n and len(paras[front_end].text) < 300 and (
                    _NAME.search(paras[front_end].text) or _AFFIL.search(paras[front_end].text)):
                front_end += 1
            j, chars = front_end, 0
            while (tail_start is None and j < n and j - front_end < 3 and _latin_ratio(paras[j].text) <= 0.5
                   and not paras[j].heading):
                chars += len(paras[j].text)
                j += 1
                if j < n and _latin_ratio(paras[j].text) > 0.5 and chars <= UNLABELED_ABSTRACT_MAX:
                    unlabeled_abstract = list(range(front_end, j))
                    front_end = j
                    break
            # англ. блок после русской шапки; при аннотации в конце его здесь нет, а латиница
            # в начале тела — формулы («∂y = 0, 0 < γ < 1»), их не отрезаем
            while tail_start is None and front_end < n and (_latin_ratio(paras[front_end].text) > 0.5
                                                            or _ANCHOR_START.match(paras[front_end].text)):
                front_end += 1
    if refs_idx is None:
        refs_idx = _unlabeled_refs_start(paras, front_end)
    back_start = refs_idx if refs_idx is not None and refs_idx >= front_end else n

    # --- шапка ---
    front = paras[:front_end] if front_end else []
    for p in front:
        p.role = 'front'
    udk = [p for p in front if _A_UDK.match(p.text)]
    udk.sort(key=lambda p: not p.text.lower().startswith('удк'))
    if udk:
        meta['udk'] = udk[0].text
        udk[0].role = 'udk'
    if abs_idx is not None and abs_idx < front_end:
        texts, j = [], abs_idx
        while j < front_end and not _A_KEYWORDS.match(paras[j].text):
            texts.append(paras[j].text)
            paras[j].role = 'abstract'
            j += 1
        meta['abstract'] = _strip_label(_A_ABSTRACT, ' '.join(texts))
    elif kw_idx is not None and kw_idx > 0 and not paras[kw_idx - 1].heading:
        # аннотация без метки — абзац перед «Ключевые слова» (izkab586)
        paras[kw_idx - 1].role = 'abstract'
        meta['abstract'] = paras[kw_idx - 1].text
    elif unlabeled_abstract:
        for j in unlabeled_abstract:
            paras[j].role = 'abstract'
        meta['abstract'] = ' '.join(paras[j].text for j in unlabeled_abstract)
    if kw_idx is not None:
        paras[kw_idx].role = 'keywords'
        raw = _strip_label(_A_KEYWORDS, paras[kw_idx].text)
        meta['keywords'] = [k.strip(' .') for k in re.split(r'[;,]', raw) if k.strip(' .')]
    for p in front:
        if p.role == 'front' and (_latin_ratio(p.text) > 0.5 or _A_CITE.match(p.text)):
            p.role = 'front_foreign'

    # название: самый крупный/жирный/капсовый абзац шапки до аннотации
    cand = [p for p in front[: (abs_idx if abs_idx is not None and abs_idx < front_end else len(front))]
            if p.role == 'front' and len(p.text) >= 15 and not _NAME.search(p.text)
            and not _A_UDK.match(p.text) and _latin_ratio(p.text) < 0.5]
    if title_idx is not None:
        best = paras[title_idx]         # найдено по соседству с авторами — надёжнее кегля
        best.role = 'title'             # («Краткие сообщения» крупнее, но это рубрика)
        meta['title'] = best.text
    elif cand:
        best = max(cand, key=lambda p: (p.size, p.caps, p.bold, len(p.text)))
        best.role = 'title'
        meta['title'] = best.text
    for p in front:
        if p.role != 'front' or _latin_ratio(p.text) > 0.5:
            continue
        names = []
        for l in p.lines:              # построчно: в абзаце «авторы + аффилиация» вместе
            if not _AFFIL.search(l.text):
                names += _NAME.findall(l.text)
        if names:
            # «О.О. Варламов» (шапка) и «Варламов О.О.» («Для цитирования») — один автор
            seen = {_author_key(a) for a in meta['authors']}
            for nm in names:
                if _author_key(nm) not in seen:
                    meta['authors'].append(nm)
                    seen.add(_author_key(nm))
            if len(p.text) < 300:
                p.role = 'authors'

    # --- хвостовая шапка (аннотация/ключевые слова после литературы) ---
    tail_end = n
    if tail_start is not None:
        tail_end = tail_start
        for p in paras[tail_start:]:
            p.role = 'back'
        if tail_abs is not None:
            j, texts = tail_abs, []
            while j < n and j != tail_kw:
                texts.append(paras[j].text)
                paras[j].role = 'abstract'
                j += 1
            meta['abstract'] = _strip_label(_A_ABSTRACT, ' '.join(texts))
        paras[tail_kw].role = 'keywords'
        raw = _strip_label(_A_KEYWORDS, paras[tail_kw].text)
        meta['keywords'] = [k.strip(' .') for k in re.split(r'[;,]', raw) if k.strip(' .')]

    # --- хвост ---
    refs, other = [], []
    for p in paras[back_start:tail_end]:
        if _A_AUTHORS_INFO.match(p.text) or _A_RECEIVED.match(p.text):
            p.role = 'back'
            other.append(p)
            continue
        # длинный латинский абзац после литературы — англ. блок (об авторах и т.п.), НО не
        # транслитерация описания («Anikin Yu. A. … — 2012. — Vol. 52»): у неё признаки ссылки
        if other or (p.role != 'references' and _latin_ratio(p.text) > 0.5 and not _REF_ITEM.match(p.text)
                     and refs and len(p.text) > 300 and not _REF_LOOK.search(p.text)):
            p.role = 'back'
            other.append(p)
            continue
        p.role = 'references'
        refs.append(p)
    if refs and _A_REFS.match(refs[0].text):
        refs[0].role = 'references_title'
        refs = refs[1:]
    # пункты литературы: новый пункт начинается с «[n]» / «n.»
    # новый пункт — только если его номер = предыдущий + 1: строка «3. - С.687-694.»
    # внутри описания журнала тоже начинается с «число.», но номер не по порядку
    # и того же стиля нумерации, что первый пункт («[n]» или «n.»)
    items, last_no, style = [], 0, None
    ref_lines = [l for p in refs for l in p.lines]
    # о нумерации судим по ПЕРВОЙ строке списка: перенос «… Vol. 65.» тоже начинается с «число.»
    if ref_lines and not _REF_ITEM.match(ref_lines[0].text):
        # без нумерации (CRM): пункт = строка у левого края списка, продолжение — с отступом
        left_of = {}
        for l in ref_lines:
            left_of[l.page] = min(left_of.get(l.page, l.x0), l.x0)
        for l in ref_lines:
            if l.x0 <= left_of[l.page] + INDENT_MIN / 2 or not items:
                items.append(l.text)
            else:
                items[-1] = _join(items[-1], l.text)
        refs = []                       # уже разобрано, нумерованная ветка ниже не нужна
    for p in refs:
        for l in p.lines:
            m = re.match(r'^(\[)?(\d{1,3})[\].]', l.text)
            st = bool(m.group(1)) if m else None
            if (m and int(m.group(2)) == last_no + 1 and style in (None, st)) or not items:
                items.append(l.text)
                if m:
                    last_no, style = int(m.group(2)), st
            else:
                items[-1] = _join(items[-1], l.text)
    meta['references'] = [re.sub(r'\s*↑\s*\d+(\s*,\s*\d+)*\s*\.?', '', t).strip() for t in items]

    # --- тело ---
    sections, cur = [], None
    body_paras = paras[front_end:back_start]
    for p in body_paras:
        # оглавление курсовых/ВКР: заголовок «СОДЕРЖАНИЕ» и строки с отточием — не текст работы
        if _TOC_TITLE.match(p.text.strip()) or _TOC_LINE.search(p.text):
            p.role = 'toc'
            continue
        if _latin_ratio(p.text) > BODY_LATIN_DROP and len(p.text) > 30:
            p.role = 'foreign_lang'     # англ. аннотация/название посреди тела — не текст статьи
            continue
        if p.heading and _NAME.search(p.text) and len(p.text) < 120:
            p.role = 'noise'            # «В.Н. Бугорский, А.Г. Сергиенко» — колонтитул с авторами
            continue
        if p.heading:
            p.role = 'heading'
            cur = {'title': p.text, 'level': _level(p.text), 'page': p.page + 1, 'paragraphs': []}
            sections.append(cur)
            continue
        if p.runin_title:
            p.role = 'heading_runin'
            cur = {'title': p.runin_title, 'level': 1, 'page': p.page + 1, 'paragraphs': []}
            sections.append(cur)
            cut = p.extra.get('runin_len') or len(p.lines[0].lead_bold)
            body_text = p.text[cut:].strip()
            if body_text:
                cur['paragraphs'].append(body_text)
            continue
        if _CAPTION.match(p.text):
            p.role = 'caption'
            continue
        if p.extra.get('small'):
            p.role = 'small'
            continue
        if p.extra.get('table'):
            p.role = 'table'
            continue
        p.role = 'body'
        if cur is None:
            cur = {'title': '(без заголовка)', 'level': 1, 'page': p.page + 1, 'paragraphs': []}
            sections.append(cur)
        # пункт списка после ввода «…задачи:» / предыдущего пункта «…;» — часть того же абзаца:
        # по одному пункту абзац слишком короток и для смыслового вектора, и для чтения (s021)
        if (cur['paragraphs'] and _LIST_CONT.match(p.text)
                and cur['paragraphs'][-1].rstrip().endswith((':', ';'))):
            cur['paragraphs'][-1] += '\n' + p.text
            continue
        cur['paragraphs'].append(p.text)
    return {'meta': meta, 'sections': sections}


def _translit(s: str) -> str:
    table = dict(zip('абвгдеёжзийклмнопрстуфхцчшщъыьэюя',
                     ['a', 'b', 'v', 'g', 'd', 'e', 'e', 'zh', 'z', 'i', 'y', 'k', 'l', 'm', 'n', 'o',
                      'p', 'r', 's', 't', 'u', 'f', 'h', 'ts', 'ch', 'sh', 'sch', '', 'y', '', 'e',
                      'yu', 'ya']))
    return ''.join(table.get(c, c) for c in s.lower())


def _slug_overlap(title: str, filename: str) -> float:
    t = set(re.findall(r'[a-z]{4,}', _translit(title)))
    f = set(re.findall(r'[a-z]{4,}', filename.lower()))
    return len(t & f) / len(t) if t else 0.0


def parse_article_pdf(path: str) -> dict:
    doc = fitz.open(path)
    lines, geo = extract_lines(doc)
    n_pages = len(doc)
    doc.close()
    warnings = []
    text_chars = sum(len(l.text) for l in lines)
    if text_chars < TEXT_LAYER_MIN_CHARS_PER_PAGE * n_pages:
        return {'file': path, 'pages': n_pages, 'body_font': 0.0,
                'meta': {'title': '', 'authors': [], 'udk': '', 'abstract': '', 'keywords': [],
                         'references': []},
                'sections': [], 'paragraphs': [], 'needs_ocr': True,
                'warnings': [f'нет текстового слоя ({text_chars} симв. на {n_pages} стр.) — скан, нужен OCR']}
    mark_noise(lines, geo)
    paras, body_size = build_paragraphs(lines, geo)

    bounds = split_articles(paras, body_size)
    articles = []
    for s, e in bounds:
        part = paras[s:e]
        res = assign_zones(part, body_size)
        body_chars = sum(len(t) for sec in res['sections'] for t in sec['paragraphs'])
        articles.append((s, e, res, body_chars))

    main = 0
    if len(articles) > 1:
        fname = path.replace('\\', '/').rsplit('/', 1)[-1]
        by_slug = [(_slug_overlap(a[2]['meta']['title'], fname), i) for i, a in enumerate(articles)]
        best_slug = max(by_slug)
        main = best_slug[1] if best_slug[0] >= 0.5 else max(range(len(articles)), key=lambda i: articles[i][3])
        others = [a[2]['meta']['title'][:60] for i, a in enumerate(articles) if i != main]
        warnings.append(f'в файле {len(articles)} статьи; выбрана «{articles[main][2]["meta"]["title"][:60]}», '
                        f'отброшены: {others}')
    s0 = bounds[0][0]
    if s0 > 0 or (len(articles) > 1 and bounds[main][0] > 0):
        lead = paras[: bounds[main][0]]
        lead_chars = sum(len(p.text) for p in lead if p not in paras[bounds[main][0]:bounds[main][1]])
        if lead_chars:
            warnings.append(f'перед статьёй {lead_chars} симв. чужого текста (хвост предыдущей статьи) — отброшено')

    s, e, res, body_chars = articles[main]
    for p in paras:
        if not (s <= paras.index(p) < e):
            p.role = 'foreign'

    # язык текста статьи — по всему, что не шапка (у русских журналов бывает русская шапка
    # при английском тексте: 5 из 20 статей CRM). Проект анализирует только русский (ADR 004).
    rest = ' '.join(p.text for p in paras[s:e] if p.role not in ('front', 'title', 'authors', 'udk',
                                                                'abstract', 'keywords'))
    lang = 'en' if len(rest) > 500 and _latin_ratio(rest) > BODY_LANG_LATIN else 'ru'
    if lang == 'en':
        warnings.append('текст статьи на английском (на русском только шапка) — из анализа исключена')
        res['sections'] = []
    if not res['sections'] and lang == 'ru':
        warnings.append('тело статьи не найдено')
    if not res['meta']['references']:
        warnings.append('список литературы не найден')
    if len(res['sections']) == 1 and res['sections'][0]['title'] == '(без заголовка)':
        warnings.append('заголовки разделов не найдены — тело одним блоком')

    return {
        'file': path,
        'pages': n_pages,
        'body_font': body_size,
        'meta': res['meta'],
        'sections': res['sections'],
        'paragraphs': [{'page': p.page + 1, 'role': p.role, 'text': p.text} for p in paras],
        'needs_ocr': False,
        'lang': lang,
        'warnings': warnings,
    }


def to_xml_nodes(parsed: dict) -> tuple[list, list]:
    """Формат xml_builder: final_nodes + sequence (NavigationTable). Абзацы через пустую
    строку — граница абзаца сохраняется для слоя анализа."""
    nodes, seq = [], []
    m = parsed['meta']
    if m['abstract']:
        nodes.append({'title': 'Аннотация', 'content': m['abstract'], 'level': 1, 'page': 1})
    for sec in parsed['sections']:
        nodes.append({'title': sec['title'], 'content': '\n\n'.join(sec['paragraphs']),
                      'level': sec['level'], 'page': sec['page']})
        seq.append({'title': sec['title'], 'page': sec['page'], 'level': sec['level']})
    if m['references']:
        nodes.append({'title': 'Список литературы', 'content': '\n'.join(m['references']),
                      'level': 1, 'page': 0})
    return nodes, seq
