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


def find_real_indices(full_text, sequence):
    start_pos = find_toc_boundary(full_text, sequence)
    indices_map = []
    current_pos = start_pos

    for item in sequence:
        full_title = item['title'].strip()
        if not full_title: continue
        clean_title = get_clean_title(full_title)

        search_variants = [full_title, clean_title]
        found_match = None
        for title_to_search in search_variants:
            tokens = re.findall(r'[a-zA-Zа-яА-Я0-9§]+', title_to_search)
            if not tokens: continue
            pattern_str = r"[\s\W]*?".join([re.escape(t) for t in tokens])
            pattern = re.compile(pattern_str, re.IGNORECASE | re.DOTALL)

            match = pattern.search(full_text, current_pos)
            if not match: match = pattern.search(full_text, start_pos)

            if match:
                found_match = match
                break

        if found_match:
            indices_map.append({"item": item, "start_idx": found_match.start(), "end_idx": found_match.end()})
            current_pos = found_match.end()

    indices_map.sort(key=lambda x: x['start_idx'])
    return indices_map


def clean_footer_header(full_text):
    lines = full_text.split('\n')
    if len(lines) < 60: return full_text
    counts = {}
    for l in lines:
        s = l.strip()
        if len(s) > 20: counts[s] = counts.get(s, 0) + 1
    junk = {l for l, c in counts.items() if c > 4}
    return "\n".join([l for l in lines if l.strip() not in junk])