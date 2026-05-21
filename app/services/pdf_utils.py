import re
import fitz


def get_all_text(doc):
    full_text = ""
    for page in doc:
        text = page.get_text().replace('\x00', '').replace('\x0c', ' ')
        full_text += text + "\n"
    return full_text


def get_clean_title(title: str) -> str:
    if not title: return ""
    clean = re.sub(
        r'^(?:Глава|Chapter|Часть|Раздел|§|Введение|Предисловие|Заключение|[IVXLCDM]+\.|[0-9]+(?:\.[0-9]+)*\.?)\s*', '',
        title, flags=re.IGNORECASE)
    clean = re.sub(r'[\.\s\…\t]+$', '', clean).strip()
    return clean


def find_toc_boundary(full_text, sequence):
    if not sequence: return 0
    limit = min(int(len(full_text) * 0.20), 100000)
    last_toc_pos = 0
    for item in sequence[-5:]:
        title = get_clean_title(item['title'])
        if len(title) < 5: continue
        match = re.search(re.escape(title), full_text[:limit], re.IGNORECASE)
        if match and match.end() > last_toc_pos:
            last_toc_pos = match.end()
    return last_toc_pos if last_toc_pos > 0 else 3000


def _search_with_confidence(full_text, full_title, clean_title, current_pos, start_pos):
    """Пробует стратегии поиска от точных к нечётким. Возвращает dict с confidence или None."""

    # --- Стратегия 1: точное совпадение полного заголовка ---
    for title in [full_title, clean_title]:
        idx = full_text.find(title, current_pos)
        if idx == -1:
            idx = full_text.find(title, start_pos)
        if idx != -1:
            return {
                'start': idx,
                'end': idx + len(title),
                'confidence': 1.0,
                'strategy': 'exact'
            }

    # --- Стратегия 2: tokenized regex (текущий алгоритм) ---
    for title in [full_title, clean_title]:
        tokens = re.findall(r'[a-zA-Zа-яА-Я0-9§]+', title)
        if len(tokens) < 2:
            continue
        pattern_str = r'[\s\W]*?'.join([re.escape(t) for t in tokens])
        pattern = re.compile(pattern_str, re.IGNORECASE | re.DOTALL)
        match = pattern.search(full_text, current_pos)
        if not match:
            match = pattern.search(full_text, start_pos)
        if match:
            return {
                'start': match.start(),
                'end': match.end(),
                'confidence': 0.85,
                'strategy': 'tokenized_regex'
            }

    # --- Стратегия 3: первые N значимых слов (>=4) ---
    words = re.findall(r'[a-zA-Zа-яА-Я]{3,}', clean_title)
    if len(words) >= 4:
        partial_tokens = words[:4]
        pattern_str = r'[\s\W]{0,15}'.join([re.escape(w) for w in partial_tokens])
        pattern = re.compile(pattern_str, re.IGNORECASE | re.DOTALL)
        match = pattern.search(full_text, current_pos)
        if not match:
            match = pattern.search(full_text, start_pos)
        if match:
            return {
                'start': match.start(),
                'end': match.end(),
                'confidence': 0.6,
                'strategy': 'partial_words'
            }

    # --- Стратегия 4: числовой префикс + первое слово ---
    num_prefix = re.match(r'^(\d+(?:\.\d+)*\.?)\s+(\w+)', clean_title)
    if num_prefix:
        pattern = re.compile(
            re.escape(num_prefix.group(1)) + r'[\s\W]{0,5}' + re.escape(num_prefix.group(2)),
            re.IGNORECASE
        )
        match = pattern.search(full_text, current_pos)
        if not match:
            match = pattern.search(full_text, start_pos)
        if match:
            return {
                'start': match.start(),
                'end': match.end(),
                'confidence': 0.4,
                'strategy': 'num_prefix'
            }

    return None


def find_real_indices(full_text, sequence):
    start_pos = find_toc_boundary(full_text, sequence)
    indices_map = []
    current_pos = start_pos

    for item in sequence:
        full_title = item['title'].strip()
        if not full_title:
            continue
        clean_title = get_clean_title(full_title)

        result = _search_with_confidence(full_text, full_title, clean_title, current_pos, start_pos)

        if result:
            indices_map.append({
                "item": item,
                "start_idx": result['start'],
                "end_idx": result['end'],
                "confidence": result['confidence'],
                "strategy": result['strategy']
            })
            current_pos = result['end']
        else:
            indices_map.append({
                "item": item,
                "start_idx": -1,
                "end_idx": -1,
                "confidence": 0.0,
                "strategy": "not_found"
            })

    # Сохраняем исходный порядок из sequence — сортировка по start_idx ломает иерархию
    found = {m['item']['title']: m for m in indices_map if m['start_idx'] != -1}
    not_found = [m for m in indices_map if m['start_idx'] == -1]

    ordered = []
    for seq_item in sequence:
        title = seq_item['title'].strip()
        if title in found:
            ordered.append(found[title])

    # Не найденные вставляем по номеру страницы (fallback)
    for nf in not_found:
        page = nf['item'].get('page') or 9999
        insert_at = len(ordered)
        for i, o in enumerate(ordered):
            op = o.get('item', {}).get('page') or 0
            if op > page:
                insert_at = i
                break
        ordered.insert(insert_at, nf)

    return ordered


def clean_footer_header(full_text):
    lines = full_text.split('\n')
    if len(lines) < 60: return full_text
    counts = {}
    for l in lines:
        s = l.strip()
        if len(s) > 20: counts[s] = counts.get(s, 0) + 1
    junk = {l for l, c in counts.items() if c > 4}
    return "\n".join([l for l in lines if l.strip() not in junk])