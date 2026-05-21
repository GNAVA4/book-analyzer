import fitz
import asyncio
from .doc_type_detector import detect_pdf, DocType
from .pdf_utils import get_all_text, find_real_indices, find_toc_boundary
from .toc_parser import HeuristicParser, toc_to_linear_sequence
from .confidence_scoring import score_toc_extraction, count_low_confidence
from .config import TOC_CONFIDENCE_FALLBACK_THRESHOLD, CONFIDENCE_THRESHOLD
from .selective_llm import process_chunk, process_missing_chapters


async def parse_pdf_neural(file_path, progress_callback=None) -> tuple:
    """Нейросетевой парсинг PDF с selective LLM (только проблемные чанки)."""
    doc = fitz.open(file_path)

    # 1. Детекция типа
    doc_type_info = detect_pdf(file_path)
    if doc_type_info['type'] == DocType.ENCRYPTED:
        raise ValueError("PDF зашифрован и не может быть обработан")
    
    if doc_type_info['type'] == DocType.PDF_SCAN:
        raise ValueError("PDF является сканированным документом (нет текстового слоя). Требуется OCR.")

    # 2. ToC extraction с confidence
    if progress_callback:
        await progress_callback(5, "Поиск оглавления...")
    
    toc_raw = ""
    for i in range(min(20, len(doc))):
        toc_raw += doc[i].get_text() + "\n"
    
    parser = HeuristicParser()
    toc_tree = parser.parse_toc(toc_raw)
    sequence = toc_to_linear_sequence(toc_tree)
    toc_confidence = score_toc_extraction(toc_tree)

    # Fallback ToC через LLM если confidence < порога
    if toc_confidence < TOC_CONFIDENCE_FALLBACK_THRESHOLD and not sequence:
        async def fallback():
            return await parser.extract_toc_via_llm(toc_raw)
        sequence = await fallback()
        toc_confidence = min(1.0, len(sequence) / max(len(sequence), 3)) if sequence else 0.0

    # 3. Mapping с confidence (4 стратегии)
    full_text = get_all_text(doc)
    mapped = find_real_indices(full_text, sequence)

    # 4. Обработка не найденных глав через LLM
    llm_refinements = 0
    low_conf_items = [m for m in mapped if m['confidence'] == 0.0]
    if low_conf_items:
        mapped, llm_refinements = await process_missing_chapters(mapped, doc)

    # 5. Подсчёт для прогресс-бара (сколько чанков пойдёт в LLM)
    total = len(mapped)
    llm_chunks = count_low_confidence(mapped, CONFIDENCE_THRESHOLD)
    processed = 0
    llm_processed = 0

    final_nodes = []

    for i in range(total):
        curr = mapped[i]

        # Определяем режим для прогресса
        is_llm = curr['confidence'] < CONFIDENCE_THRESHOLD and curr['confidence'] != 0.0
        
        if progress_callback:
            mode = "LLM" if is_llm else "алгоритм"
            pct = int(10 + (processed / total) * 85)
            title_short = curr['item']['title'][:30] if curr.get('item') else "Unknown"
            await progress_callback(pct, f"[{mode}] {title_short} (conf: {curr['confidence']:.2f})")

        # Вырезаем сырой чанк из full_text
        if curr.get('start_idx') == -1:
            raw_chunk = ""
        else:
            start = curr['end_idx'] if curr.get('start_idx') != -1 else 0
            end = (mapped[i + 1]['start_idx'] 
                   if (i + 1 < total and mapped[i + 1].get('start_idx', -1) != -1)
                   else len(full_text))
            raw_chunk = full_text[start:end] if curr.get('start_idx') != -1 else ""

        node = await process_chunk(curr, raw_chunk, mapped, i)
        final_nodes.append(node)
        
        processed += 1
        if is_llm:
            llm_processed += 1

    doc.close()
    
    metadata = {
        "toc_confidence": toc_confidence,
        "llm_refinements": llm_refinements,
        "total_chapters": total,
        "llm_chunks_used": llm_processed,
        "low_confidence_count": count_low_confidence(mapped, CONFIDENCE_THRESHOLD)
    }

    return final_nodes, sequence, metadata
