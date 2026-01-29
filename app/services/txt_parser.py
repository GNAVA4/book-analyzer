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

    for i in range(len(mapped)):
        curr = mapped[i]
        start = curr['end_idx']
        end = mapped[i + 1]['start_idx'] if i + 1 < len(mapped) else len(full_text)

        content = full_text[start:end].strip()
        final_nodes.append({
            "title": curr['item']['title'],
            "content": content,
            "level": curr['item'].get('level', 1),
            "page": 0
        })

    return final_nodes, sequence