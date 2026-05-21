from .pdf_utils import find_real_indices, get_clean_title
from .toc_parser import HeuristicParser, toc_to_linear_sequence


def parse_txt(file_path) -> tuple:
    full_text = ""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            full_text = f.read()
    except UnicodeDecodeError:
        with open(file_path, 'r', encoding='cp1251') as f:
            full_text = f.read()

    full_text = full_text.replace('\x00', '')

    parser = HeuristicParser()
    toc_tree = parser.parse_toc(full_text[:50000])
    sequence = toc_to_linear_sequence(toc_tree)

    mapped = find_real_indices(full_text, sequence)
    final_nodes = []

    for i, curr in enumerate(mapped):
        if curr['start_idx'] == -1:
            final_nodes.append({
                "title": curr['item']['title'],
                "content": "",
                "level": curr['item'].get('level', 1),
                "page": 0,
            })
            continue

        start = curr['end_idx']
        next_start = min(
            (m['start_idx'] for m in mapped if m['start_idx'] > start),
            default=len(full_text)
        )
        content = full_text[start:next_start].strip()
        final_nodes.append({
            "title": curr['item']['title'],
            "content": content,
            "level": curr['item'].get('level', 1),
            "page": 0,
        })

    return final_nodes, sequence