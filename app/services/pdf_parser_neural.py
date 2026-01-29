import fitz
import re
from .pdf_utils import get_all_text, find_real_indices, clean_footer_header
from .toc_parser import HeuristicParser, toc_to_linear_sequence
from .llm_engine import llm_client


async def parse_pdf_neural(file_path, progress_callback=None) -> tuple:
    doc = fitz.open(file_path)

    if progress_callback: await progress_callback(5, "Поиск оглавления...")
    toc_raw = ""
    for i in range(min(20, len(doc))): toc_raw += doc[i].get_text() + "\n"
    parser = HeuristicParser()
    toc_tree = parser.parse_toc(toc_raw)
    sequence = toc_to_linear_sequence(toc_tree)

    full_text = get_all_text(doc)
    full_text = clean_footer_header(full_text)
    mapped = find_real_indices(full_text, sequence)

    final_nodes = []
    total = len(mapped)

    for i, curr in enumerate(mapped):
        if progress_callback:
            pct = int(10 + (i / total) * 85)
            await progress_callback(pct, f"Нейро-чистка: {curr['item']['title'][:30]}")

        start, end = curr['end_idx'], (mapped[i + 1]['start_idx'] if i + 1 < len(mapped) else len(full_text))
        raw_chunk = full_text[start:end].strip()

        if len(raw_chunk) > 10:
            clean_content = await llm_client.process_large_text(raw_chunk, is_start=False)
        else:
            clean_content = ""

        final_nodes.append({
            "title": curr['item']['title'],
            "content": clean_content,
            "level": curr['item'].get('level', 1),
            "page": curr['item'].get('page', 0)
        })

    doc.close()
    return final_nodes, sequence