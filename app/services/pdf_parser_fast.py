import fitz
import re
from .pdf_utils import get_all_text, find_real_indices, clean_footer_header, get_clean_title
from .toc_parser import HeuristicParser, toc_to_linear_sequence


def parse_pdf_fast(file_path) -> tuple:
    doc = fitz.open(file_path)

    toc_raw = ""
    for i in range(min(25, len(doc))): toc_raw += doc[i].get_text() + "\n"
    parser = HeuristicParser()
    toc_tree = parser.parse_toc(toc_raw)
    sequence = toc_to_linear_sequence(toc_tree)

    full_text = get_all_text(doc)
    full_text = clean_footer_header(full_text)

    mapped = find_real_indices(full_text, sequence)
    final_nodes = []

    for i in range(len(mapped)):
        curr = mapped[i]
        start = curr['end_idx']
        end = mapped[i + 1]['start_idx'] if i + 1 < len(mapped) else len(full_text)

        raw_content = full_text[start:end].strip()
        clean_content = re.sub(r'(\w+)-\n\s*(\w+)', r'\1\2', raw_content)
        clean_content = re.sub(r'\n{3,}', '\n\n', clean_content)

        final_nodes.append({
            "title": curr['item']['title'],
            "content": clean_content,
            "level": curr['item'].get('level', 1),
            "page": curr['item'].get('page', 0)
        })

    doc.close()
    return final_nodes, sequence