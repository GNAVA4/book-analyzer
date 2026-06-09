"""
Многоуровневый pipeline для извлечения оглавления.

Идея: каждый уровень — отдельная функция, возвращающая sequence и quality score.
Builder пробует уровни по очереди, останавливается на первом результате
с достаточным quality, либо собирает лучший из всех попыток.

Уровни (от дешёвого к дорогому):
    1. Эвристика (HeuristicParser)         — мгновенно, работает на 70% книг
    2. LLM extract_toc_json (первые страницы) — секунды, для книг с битой вёрсткой
    3. OCR + эвристика                       — минуты, для книг где ToC = картинка
    4. OCR + LLM                              — минуты, для нечитаемых документов
    5. Deep scan (LLM по chunks)              — десятки минут, последний шанс
    6. LLM expansion: добавить главы внутрь высокоуровневых частей
"""

import re
from difflib import SequenceMatcher
from typing import Callable, Awaitable

from .toc_parser import HeuristicParser, toc_to_linear_sequence
from .llm_engine import llm_client
from .ocr_engine import ocr_client
from .toc_validate import score_toc, is_valid, check_formal, check_cjk


# Паттерн «page-число + заглавная буква» — типичная склейка пунктов ToC
# в OCR-выводе («Заголовок1 21 Заголовок2 41 Заголовок3 63» одной строкой).
# Используется только когда таких переходов в строке несколько — это
# гарантирует что мы не режем обычную прозу вроде «...через 100 метров Иван...».
_STICKY_TOC_PAT = re.compile(r'\s(\d{1,4})\s(?=[А-ЯЁA-Z])')
# Хвостовой номер страницы после последнего заголовка в строке —
# одинокая «<NN>» в конце без следующей заглавной.
_TAIL_PAGE_PAT = re.compile(r'\s+(\d{1,4})\s*$')


def _split_sticky_toc_lines(text: str) -> str:
    """
    Разбивает строки, в которых OCR слепил несколько пунктов ToC.

    Эвристика: строка длиннее 60 символов и содержит >= 2 переходов вида
    «<NN> <Заглавная>». Тогда:
      1. Перед каждой заглавной (идущей после числа) вставляем `\\n`.
      2. Перед каждым page-числом ставим 3+ пробелов — это нужно чтобы
         item_pattern эвристики (`\\s{3,}` как сепаратор) распознал номер
         страницы. Без этого heuristic не парсил бы получившиеся подстроки
         с одним пробелом перед числом.

    Защита от ложных срабатываний на прозе: требуем И длину >60, И >= 2
    переходов — обычное предложение редко содержит две таких комбинации.
    """
    if not text:
        return text
    out_lines = []
    for line in text.split('\n'):
        if len(line) > 60 and len(_STICKY_TOC_PAT.findall(line)) >= 2:
            # Шаг 1: «<NN> <Cap>» → «   <NN>\n<Cap>»
            line = _STICKY_TOC_PAT.sub(r'   \1\n', line)
            # Шаг 2: для каждой получившейся под-строки, если она кончается
            # «text <NN>» с одним пробелом — поднимаем до 3 пробелов.
            sub_lines = []
            for sub in line.split('\n'):
                sub = _TAIL_PAGE_PAT.sub(r'   \1', sub)
                sub_lines.append(sub)
            line = '\n'.join(sub_lines)
        out_lines.append(line)
    return '\n'.join(out_lines)


# --- Пороги качества ---------------------------------------------------------

# Достаточное число секций чтобы считать ToC «качественным» (не использовать
# более дорогие уровни). Поднято с 8 до 12 — для quality-first режима лучше
# попросить LLM проверить даже когда эвристика что-то нашла.
TOC_GOOD_ENOUGH = 12

# Минимум секций, ниже которого ToC считается «недостаточным» и срабатывает
# LLM-fallback (даже если эвристика что-то нашла). Поднят с 3 до 5 —
# одна-две найденных секции это явно мусор (только ББК/УДК), но даже 3-4
# секции на крупной книге подозрительны.
TOC_MIN_USEFUL = 5

# Если ToC выглядит «верхнеуровневым» — все секции level 1 и их мало —
# попробовать LLM-expansion для поиска глав внутри частей.
TOC_HIGH_LEVEL_THRESHOLD = 6

# Контекст-лимиты под 8K-context LM Studio (qwen2.5-7b).
# 12000 chars ≈ 4800 tokens текста + 500 промпт + 2048 ответ = ~7300 tokens — влезает.
# ВАЖНО: модель в LM Studio должна быть загружена с ctx >= 8192.
# Если получаете "Context size exceeded" / "n_keep > n_ctx" — увеличьте ctx в LM Studio.
TOC_LLM_MAX_CHARS = 12_000
TOC_LLM_PAGES = 20
OCR_TOC_PAGES = 30  # сколько первых страниц OCR-ить для поиска ToC


# --- Уровни ------------------------------------------------------------------

def _heuristic(text: str) -> list:
    """Уровень 1: чисто алгоритмический парс."""
    parser = HeuristicParser()
    return toc_to_linear_sequence(parser.parse_toc(text))


async def _llm_from_text(text: str) -> list:
    """Уровень 2: LLM extract_toc_json от первых страниц."""
    items = await llm_client.extract_toc_json(text[:TOC_LLM_MAX_CHARS])
    return _reattach_numerical_prefixes(_normalize_llm_items(items), text)


async def _ocr_then_heuristic(doc, progress_cb=None) -> tuple[list, str]:
    """Уровень 3: OCR первых страниц + эвристика."""
    if not await ocr_client.is_available():
        return [], ""
    if progress_cb:
        await progress_cb(6, f"OCR первых {OCR_TOC_PAGES} стр. для поиска ToC...")
    ocr_text = await ocr_client.ocr_document(doc, progress_callback=progress_cb, max_pages=OCR_TOC_PAGES)
    # Перед heuristic разбиваем склеенные ToC-строки (типично для glm-ocr):
    # OCR может вернуть всю ToC одной строкой без переносов между пунктами.
    return _heuristic(_split_sticky_toc_lines(ocr_text)), ocr_text


async def _ocr_then_llm(ocr_text: str) -> list:
    """Уровень 4: эвристика по OCR-тексту провалилась — пробуем LLM."""
    if not ocr_text:
        return []
    items = await llm_client.extract_toc_json(ocr_text[:TOC_LLM_MAX_CHARS])
    return _reattach_numerical_prefixes(_normalize_llm_items(items), ocr_text)


async def _deep_scan(full_text: str, progress_cb=None) -> list:
    """Уровень 5: LLM проходит по chunks полного текста и предлагает структуру."""
    if progress_cb:
        await progress_cb(8, "Deep-scan: LLM анализирует полный текст...")
    items = await llm_client.propose_structure_from_text(full_text)
    return _normalize_llm_items(items)


async def _llm_expand_parts(sequence: list, full_text: str, progress_cb=None) -> list:
    """
    Уровень 6: если ToC слишком верхнеуровневый (только ЧАСТЬ I/II/III),
    просим LLM найти главы ВНУТРИ каждой части по тексту книги.

    Возвращает расширенный sequence, либо оригинал если расширение не удалось.
    """
    if not sequence or not full_text:
        return sequence

    # Берём окна между соседними секциями верхнего уровня
    expanded = []
    for i, item in enumerate(sequence):
        expanded.append(item)
        if item.get('level', 1) > 1:
            continue
        # Окно от страницы текущей секции до следующей секции верхнего уровня
        # (либо до конца текста). Эвристически переводим страницы в позиции —
        # пропорционально по объёму full_text.
        # Если страниц нет — этот шаг просто пропускаем.
        if item.get('page') is None:
            continue
        next_top = next(
            (sequence[j] for j in range(i + 1, len(sequence)) if sequence[j].get('level', 1) == 1),
            None
        )
        # Очень грубая аппроксимация позиции в тексте по странице.
        # Точнее это сделать сложно без реальных границ страниц.
        # Здесь нам важна качественная картина — поэтому берём кусок ~30K символов.
        try:
            # Возьмём ~20K char окно начиная от примерной позиции
            est_chars_per_page = len(full_text) / max(_max_page(sequence), 1)
            start_pos = int((item['page'] - 1) * est_chars_per_page)
            end_pos = int(next_top['page'] * est_chars_per_page) if next_top and next_top.get('page') else start_pos + 30_000
            window = full_text[start_pos:end_pos][:30_000]
        except Exception:
            continue

        if len(window) < 1000:
            continue

        if progress_cb:
            await progress_cb(10, f"LLM ищет главы в «{item['title'][:30]}»...")

        try:
            sub_items = await llm_client.propose_structure_from_text(window, chunk_size=5_000)
        except Exception:
            sub_items = []

        # Добавляем найденные подпункты как level 2 (потомки текущей части)
        for sub in sub_items:
            sub_title = sub.get('title', '').strip()
            if sub_title and sub_title.lower() != item['title'].lower():
                expanded.append({
                    'title': sub_title,
                    'level': max(item.get('level', 1) + 1, 2),
                    'page': None,
                })

    return expanded if len(expanded) > len(sequence) else sequence


# --- Post-processing ---------------------------------------------------------

def _dedup_and_order(sequence: list) -> list:
    """
    Удаляет дубли (title-only, нормализованный) и сортирует по странице,
    если страницы известны для большинства пунктов.

    Дедуп по title-only (а не (title, page)) — потому что один и тот же
    раздел может прийти из разных источников: heuristic с page=5,
    повторный заголовок в самой книге без page. Кейс Массель: «2. Принципы»
    в ToC + «2. ПРИНЦИПЫ» как заголовок страницы — это один раздел.

    Из дубликатов выбираем тот, у которого ЕСТЬ page (приоритетнее) и
    больший level (более детальный).

    Решает проблему «парсер захватил два оглавления подряд» (краткое +
    детальное в одной книге) — после дедупа и сортировки получаем единый
    упорядоченный список с подразделами после их родительских глав.
    """
    if not sequence:
        return sequence

    def _norm(t: str) -> str:
        # Lowercase + collapse whitespace — ловит регистр и переносы
        return re.sub(r'\s+', ' ', (t or '').lower()).strip()

    by_norm: dict = {}
    order: list = []
    for s in sequence:
        title = _norm(s.get('title'))
        if not title:
            continue
        if title not in by_norm:
            by_norm[title] = s
            order.append(title)
            continue
        # Дубликат — выбираем лучший вариант: с page > без, больший level выигрывает
        prev = by_norm[title]
        prev_has_page = isinstance(prev.get('page'), int)
        curr_has_page = isinstance(s.get('page'), int)
        if curr_has_page and not prev_has_page:
            by_norm[title] = s
        elif curr_has_page == prev_has_page and s.get('level', 1) > prev.get('level', 1):
            by_norm[title] = s

    deduped = [by_norm[t] for t in order]
    deduped = _drop_fuzzy_pageless_dupes(deduped)

    has_pages = sum(1 for s in deduped if isinstance(s.get('page'), int))
    if has_pages / max(len(deduped), 1) > 0.7:
        # Стабильная сортировка: страницы по возрастанию, None в конец
        deduped.sort(key=lambda s: (
            not isinstance(s.get('page'), int),
            s.get('page') if isinstance(s.get('page'), int) else 0,
        ))

    return deduped


# Порог fuzzy-merge для page-less дубликатов. Кейс ВКР: один и тот же заголовок
# приходит из ToC c страницей и повторно — из заголовка тела (без страницы),
# с однобуквенной OCR-разницей («ИЗУЧЕНЯЯ» vs «ИЗУЧЕНЯЮ»). Реальные разные
# главы на 82 символах титула не достигают 0.88 сходства.
_PAGELESS_DUP_THRESHOLD = 0.88


def _drop_fuzzy_pageless_dupes(items: list) -> list:
    """Drop page-less entries that fuzzy-match a paged entry's title.

    Targets OCR drift where the same heading appears twice — once in the ToC
    with a page, once as the chapter heading without a page — and the two OCR
    passes diverge by 1–2 letters, defeating exact-norm dedup.
    """
    if not items:
        return items

    def _norm(t: str) -> str:
        return re.sub(r'\s+', ' ', (t or '').lower()).strip()

    paged_norms = [_norm(s.get('title')) for s in items
                   if isinstance(s.get('page'), int)]
    paged_norms = [n for n in paged_norms if n]
    if not paged_norms:
        return items

    out = []
    for s in items:
        if isinstance(s.get('page'), int):
            out.append(s)
            continue
        t = _norm(s.get('title'))
        if not t:
            out.append(s)
            continue
        dup = False
        for p in paged_norms:
            longer = max(len(t), len(p))
            if longer < 8:
                continue
            # Длинное расхождение в длине → точно не один и тот же заголовок;
            # пропускаем дорогой ratio() ради скорости.
            if abs(len(t) - len(p)) > longer * 0.25:
                continue
            if SequenceMatcher(None, t, p).ratio() >= _PAGELESS_DUP_THRESHOLD:
                dup = True
                break
        if not dup:
            out.append(s)
    return out


# Иерархический номер в начале строки ToC + остаток-название + сепаратор + страница.
# Матчит «1.2.1 Абстракция .... 6», «§ 1.4 Паразитные связи   22», «3. DEFINITIONS    19».
# Сделано безопасно: prefix максимум 4 уровня, остаток до сепаратора (.{2+} / 3+ пробелов /
# многоточие / табы / подчёркивания) и финальный номер страницы 1-4 знака.
_NUMBERED_TOC_LINE = re.compile(
    r'^\s*'
    r'(§\s*)?(\d+(?:\.\d+){0,3}\.?)\s+'        # 1: § (опц), 2: «1.2.1.»
    r'(.+?)\s*'                                # 3: «Абстракция»
    r'(?:\.{2,}|\s{3,}|…|\t+|_{2,})'           # сепаратор (лидеры / пробелы / табы)
    r'\s*\d{1,4}\s*$',
    re.MULTILINE,
)


def _build_prefix_map(raw_text: str) -> dict:
    """Map: нормализованный bare-title → номерной префикс из строк ToC сырого текста.

    Используется как страховка: LLM может срезать «1.2.1» в title — мы достаём
    оригинальный префикс из той же строки сырого текста, по которой LLM работал.
    Берём ПЕРВОЕ вхождение каждого нормализованного bare-title (первая строка ≈ ToC,
    не повтор в теле книги).
    """
    out: dict = {}
    if not raw_text:
        return out
    for m in _NUMBERED_TOC_LINE.finditer(raw_text):
        sec = (m.group(1) or '').strip()
        num = m.group(2).rstrip('.')
        bare = m.group(3).strip()
        bare = re.sub(r'[.,;:\s]+$', '', bare)
        if not bare or len(bare) < 3:
            continue
        norm = re.sub(r'\s+', ' ', bare.lower()).strip()
        if norm not in out:
            prefix = (sec + ' ' + num).strip() if sec else num
            out[norm] = prefix
    return out


# Title уже начинается с цифры или §-нотации — префикс присутствует, не трогаем.
_HAS_NUMERIC_PREFIX = re.compile(r'^\s*(§\s*)?\d')


def _reattach_numerical_prefixes(items: list, raw_text: str) -> list:
    """Восстанавливает иерархический номерной префикс title, если LLM его срезал.

    LLM при `extract_toc_json` иногда возвращает «Абстракция», тогда как сырой
    текст содержит «1.2.1 Абстракция .... 6». Без префикса title слишком общий →
    `find_real_indices` находит первое попавшееся вхождение слова, контент мусор.

    Алгоритм:
      1. Строим из сырого текста map «bare-title-norm → prefix» по строкам с лидерами.
      2. Для каждого LLM-title: если уже начинается с цифры или § — пропускаем.
         Иначе нормализуем bare и смотрим в map. Если найден — приклеиваем префикс.

    Безопасно при дублях: первое вхождение фиксируется, поздние повторы (в теле
    книги) не перетирают. При промахе ничего не меняем.
    """
    pmap = _build_prefix_map(raw_text)
    if not pmap:
        return items
    out = []
    for it in items:
        title = (it.get('title') or '').strip()
        if not title or _HAS_NUMERIC_PREFIX.match(title):
            out.append(it)
            continue
        norm = re.sub(r'\s+', ' ', title.lower()).strip()
        prefix = pmap.get(norm)
        if prefix:
            new_it = dict(it)
            new_it['title'] = f"{prefix} {title}"
            out.append(new_it)
        else:
            out.append(it)
    return out


# --- Helpers -----------------------------------------------------------------

def _normalize_llm_items(items: list) -> list:
    """
    Приводит LLM-возвращённые items к стандартному формату sequence.

    Отвергает явно «склеенные» title (когда LLM засунула в один пункт
    целую часть с подразделами): >150 символов, или содержит несколько
    page-нумеров внутри (паттерн «текст 21 текст 41»).

    Также отвергает title с CJK/арабскими символами — это галлюцинация.
    """
    import re as _re
    multi_page_pat = _re.compile(r'\d{1,3}\s+\S.{3,}?\s+\d{1,3}')
    cjk_pat = _re.compile(r'[一-鿿぀-ヿ가-힯؀-ۿ]')

    out = []
    for it in items:
        title = str(it.get("title", "")).strip()
        if not title or len(title) < 2:
            continue
        if len(title) > 150:
            print(f"[toc_builder] skip overlong LLM title: {title[:60]}...")
            continue
        if multi_page_pat.search(title):
            print(f"[toc_builder] skip multi-page title (склейка подразделов): {title[:60]}...")
            continue
        if cjk_pat.search(title):
            print(f"[toc_builder] skip CJK in title: {title[:40]}...")
            continue
        level = it.get("level", 1)
        try:
            level = int(level) if level else 1
        except (TypeError, ValueError):
            level = 1
        # Клампим до 1..3 — система (heuristic, xml_builder) работает с тремя
        # уровнями; LLM иногда уводит глубоко вложенные пункты в level 4+.
        level = min(max(level, 1), 3)
        page_raw = it.get("page")
        page = int(page_raw) if str(page_raw).isdigit() else None
        out.append({"title": title, "level": level, "page": page})
    return out


def _max_page(sequence: list) -> int:
    pages = [s.get('page') for s in sequence if isinstance(s.get('page'), int)]
    return max(pages) if pages else 1


def _is_high_level_only(sequence: list) -> bool:
    """
    True если sequence содержит в основном верхнеуровневые пункты с малым
    количеством детализации.

    Условие чуть мягче чем «вообще нет уровней 2+»: допускаем одну-две
    «хвостовые» секции уровня 2 (например, «Примечания» как level 2),
    но если глав внутри частей нет — все ещё считаем high-level-only.
    """
    if len(sequence) > TOC_HIGH_LEVEL_THRESHOLD:
        return False
    deeper = sum(1 for s in sequence if s.get('level', 1) > 1)
    # Если глубоких секций >= 30% от общего числа — это уже нормальный ToC,
    # не нужно ничего расширять.
    return deeper / max(len(sequence), 1) < 0.3


# Строка-пункт ToC: «...текст... <дотлидеры/пробелы> 123» (инлайн-номер страницы)
_TOC_INLINE_PAGE = re.compile(r'.+?(?:\.{2,}|\s{2,}|\.\s+)\s*\d{1,4}\s*$')
_TOC_BARE_NUM = re.compile(r'^\s*\d{1,4}\s*$')


def _count_toc_entry_lines(raw_text: str) -> int:
    """
    Считает строки, похожие на пункт оглавления, в сыром тексте:
      - инлайн-номер: «Название .... 21», ИЛИ
      - голый номер на отдельной строке, перед которым идёт текстовая строка
        (формат «заголовок \\n страница» — Розенсон).
    Дешёвый, без моделей. Используется для оценки неполноты эвристики.
    """
    lines = [l.strip() for l in raw_text.split('\n')]
    lines = [l for l in lines if l]
    cnt = 0
    for i, l in enumerate(lines):
        if len(l) > 250:
            continue
        if _TOC_INLINE_PAGE.match(l):
            cnt += 1
        elif (_TOC_BARE_NUM.match(l) and i > 0 and len(lines[i - 1]) > 3
              and not _TOC_BARE_NUM.match(lines[i - 1])
              and not _TOC_INLINE_PAGE.match(lines[i - 1])):
            cnt += 1
    return cnt


def _looks_incomplete(seq: list, raw_text: str, ratio: float = 1.5) -> bool:
    """
    True если в сыром тексте ToC пунктов заметно больше, чем извлекла эвристика —
    значит эвристика потеряла подразделы (Розенсон, Клейнман). Без моделей.
    """
    if not seq:
        return True
    raw_entries = _count_toc_entry_lines(raw_text)
    return raw_entries > len(seq) * ratio


async def _llm_from_text_retry(text: str, attempts: int = 3) -> list:
    """
    LLM-извлечение ToC с retry на ФОРМАЛЬНЫЕ ошибки (CJK, пустые/длинные title,
    дубли, не-монотонные страницы). Grounding тут НЕ проверяем — это дорого
    (эмбеддер), его делает финальный отбор. Возвращает лучший по формальной чистоте.
    """
    best: list = []
    extra = ""
    raw_entries = _count_toc_entry_lines(text)
    # Ожидаемый минимум пунктов: LLM недетерминирован и иногда возвращает
    # обрезанный ToC (Клейнман: то 57, то 7). Если в сыром тексте видно ~N
    # строк-пунктов, а LLM вернул сильно меньше — это неполный ответ, ретраим.
    expected_min = max(8, int(raw_entries * 0.5))
    for _ in range(attempts):
        items = await llm_client.extract_toc_json(text[:TOC_LLM_MAX_CHARS], extra_instruction=extra)
        seq = _reattach_numerical_prefixes(_normalize_llm_items(items), text)
        if seq and len(seq) > len(best):
            best = seq
        cjk = check_cjk(seq) if seq else []
        formal = check_formal(seq, raw_entries) if seq else ['empty']
        too_few = bool(seq) and len(seq) < expected_min
        if seq and not cjk and not formal and not too_few:
            return seq  # формально чистый и достаточно полный — отдаём
        problems = []
        if cjk:
            problems.append("в заголовках были иностранные (CJK) символы — пиши на языке оригинала")
        if formal:
            problems.append("были формальные ошибки: " + "; ".join(formal[:3]))
        if too_few or not seq:
            problems.append(f"извлечено слишком мало пунктов ({len(seq)}); в оглавлении их около "
                            f"{raw_entries} — извлеки ВСЕ пункты, включая ВСЕ подразделы (1.1, 1.2, …)")
        extra = ". ".join(problems)
    return best


async def _select_best(candidates: list, full_text: str, raw_entry_count: int) -> tuple:
    """
    Скорит кандидатов (src, seq) и выбирает лучший ВАЛИДНЫЙ.
    Приоритет: валидные > невалидные; среди валидных — больше заземлённых пунктов,
    затем больше пунктов. Если валидных нет — берём кандидат с наибольшим
    числом заземлённых (по умолчанию — эвристику, она всегда первый кандидат).
    Возвращает (src, seq, score_dict).
    """
    scored = []
    for src, seq in candidates:
        if not seq:
            continue
        sc = await score_toc(seq, full_text, raw_entry_count)
        scored.append((src, seq, sc))
    if not scored:
        return ("none", [], None)
    valid = [c for c in scored if is_valid(c[2])]
    pool = valid if valid else scored
    best = max(pool, key=lambda c: (c[2]['n_grounded'], c[2]['n_total']))
    return best


# --- Главный pipeline --------------------------------------------------------

async def build_toc(
    doc,
    full_text_extractor: Callable[[], str],
    ocr_text: str | None = None,
    progress_cb: Callable[[int, str], Awaitable[None]] | None = None,
    enable_ocr: bool = True,
    enable_deep_scan: bool = False,
    enable_llm_expand: bool = True,
) -> tuple[list, str, str | None]:
    """
    Главный многоуровневый pipeline извлечения ToC.

    Возвращает (sequence, source, ocr_text):
        sequence  — список секций с {title, level, page}
        source    — какой уровень дал результат
        ocr_text  — полный OCR-текст, если OCR использовался (для повторного
                    использования в маппинге)
    """
    # Если OCR уже сделан выше по pipeline (например, документ unreadable) —
    # сразу работаем с OCR-текстом.
    if ocr_text:
        # Разбиваем склеенные ToC-строки перед эвристикой
        seq = _heuristic(_split_sticky_toc_lines(ocr_text[:50_000]))
        if len(seq) > TOC_MIN_USEFUL:
            return _dedup_and_order(seq), "ocr_heuristic", ocr_text
        seq = await _llm_from_text(ocr_text)
        if seq:
            return _dedup_and_order(seq), "ocr_llm", ocr_text
        # OCR есть, но никто ничего не нашёл — оставляем пустое
        return [], "none", ocr_text

    # --- Уровень 1: эвристика по обычно извлечённому тексту ---
    if progress_cb:
        await progress_cb(5, "ToC: эвристика...")
    raw_text = ""
    for i in range(min(20, len(doc))):
        raw_text += doc[i].get_text() + "\n"
    # PyMuPDF может вернуть ToC одной строкой если в PDF многоколоночная
    # вёрстка или пункты разделены табами. Разбиваем по page→capital
    # перед эвристикой — Иглмен-кейс.
    seq = _heuristic(_split_sticky_toc_lines(raw_text))

    # Полный, не-высокоуровневый и НЕ неполный эвристический ToC — берём как есть.
    if (len(seq) >= TOC_GOOD_ENOUGH and not _is_high_level_only(seq)
            and not _looks_incomplete(seq, raw_text)):
        return _dedup_and_order(seq), "heuristic", None

    # --- Умный fallback: эвристики не хватило (мало пунктов / только верхний
    # уровень / потеряны подразделы). Собираем кандидатов из разных источников
    # и выбираем ЛУЧШИЙ ВАЛИДИРОВАННЫЙ (formal + без CJK + grounding в тексте).
    # Источники вызываются ПОСЛЕДОВАТЕЛЬНО — нельзя держать LLM+OCR+эмбеддер
    # одновременно (GPU ≤90%).
    full_text = full_text_extractor()
    raw_entry_count = _count_toc_entry_lines(raw_text)
    candidates: list = [("heuristic", _dedup_and_order(seq))]

    # Уровень 2: LLM из первых страниц (с retry на формальные ошибки)
    if progress_cb:
        await progress_cb(6, "ToC: LLM extract_toc_json (smart)...")
    llm_seq = await _llm_from_text_retry(raw_text)
    if llm_seq:
        candidates.append(("llm", _dedup_and_order(llm_seq)))

    best_src, best_seq, best_score = await _select_best(candidates, full_text, raw_entry_count)

    # Уровень 3+4 (эскалация на OCR), если лучший пока невалиден или всё ещё неполон
    need_escalation = (best_score is None or not is_valid(best_score)
                       or _looks_incomplete(best_seq, raw_text))
    if enable_ocr and need_escalation:
        try:
            ocr_seq, ocr_text = await _ocr_then_heuristic(doc, progress_cb)
            if ocr_seq:
                candidates.append(("ocr_heuristic", _dedup_and_order(ocr_seq)))
            if ocr_text:
                ocr_llm_seq = await _ocr_then_llm(ocr_text)
                if ocr_llm_seq:
                    candidates.append(("ocr_llm", _dedup_and_order(ocr_llm_seq)))
            best_src, best_seq, best_score = await _select_best(candidates, full_text, raw_entry_count)
        except Exception as e:
            print(f"[toc_builder] OCR failed: {e}")
            ocr_text = None

    # Уровень 5: deep_scan — если ToC почти пуст
    if enable_deep_scan and len(best_seq) <= TOC_MIN_USEFUL:
        deep_seq = await _deep_scan(full_text if not ocr_text else ocr_text, progress_cb)
        if deep_seq:
            candidates.append(("deep_scan", _dedup_and_order(deep_seq)))
            best_src, best_seq, best_score = await _select_best(candidates, full_text, raw_entry_count)

    # Уровень 6: LLM-expansion для верхнеуровневого ToC (только ЧАСТЬ I/II/III без глав)
    if enable_llm_expand and _is_high_level_only(best_seq):
        expanded = await _llm_expand_parts(best_seq, full_text if not ocr_text else ocr_text, progress_cb)
        if len(expanded) > len(best_seq):
            best_seq = _dedup_and_order(expanded)
            best_src = f"{best_src}+expand"

    return best_seq, best_src, ocr_text
