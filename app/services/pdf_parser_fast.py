import fitz
import asyncio
from .doc_type_detector import detect_pdf, DocType
from .pdf_utils import get_all_text, find_real_indices, find_toc_boundary
from .toc_parser import HeuristicParser, toc_to_linear_sequence
from .confidence_scoring import score_toc_extraction
from .config import TOC_CONFIDENCE_FALLBACK_THRESHOLD
from .selective_llm import process_chunk, process_missing_chapters


async def parse_pdf_fast_async(file_path) -> tuple:
    """Асинхронный парсинг PDF с confidence scoring и selective LLM."""
    doc = fitz.open(file_path)

    # 1. Детекция типа (bail on encrypted)
    doc_type_info = detect_pdf(file_path)
    if doc_type_info['type'] == DocType.ENCRYPTED:
        raise ValueError("PDF зашифрован и не может быть обработан")
    
    if doc_type_info['type'] == DocType.PDF_SCAN:
        raise ValueError("PDF является сканированным документом (нет текстового слоя). Требуется OCR.")

    # 2. ToC extraction с confidence
    toc_raw = ""
    for i in range(min(25, len(doc))):
        toc_raw += doc[i].get_text() + "\n"
    
    parser = HeuristicParser()
    toc_tree = parser.parse_toc(toc_raw)
    sequence = toc_to_linear_sequence(toc_tree)
    toc_confidence = score_toc_extraction(toc_tree)

    # Fallback ToC через LLM если confidence < порога и ничего не найдено
    if toc_confidence < TOC_CONFIDENCE_FALLBACK_THRESHOLD and not sequence:
        sequence = await parser.extract_toc_via_llm(toc_raw)
        toc_confidence = min(1.0, len(sequence) / max(len(sequence), 3)) if sequence else 0.0

    # 3. Mapping с confidence (4 стратегии)
    full_text = get_all_text(doc)
    mapped = find_real_indices(full_text, sequence)

    # 4. Обработка не найденных глав через LLM
    llm_refinements = 0
    low_conf_items = [m for m in mapped if m['confidence'] == 0.0]
    if low_conf_items:
        mapped, llm_refinements = await process_missing_chapters(mapped, doc)

    # 5. Сборка результата через selective LLM
    final_nodes = []
    for i in range(len(mapped)):
        curr = mapped[i]
        
        if curr.get('start_idx') == -1:
            raw_chunk = ""
        else:
            start = curr['end_idx'] if curr.get('start_idx') != -1 else 0
            end = (mapped[i + 1]['start_idx'] 
                   if (i + 1 < len(mapped) and mapped[i + 1].get('start_idx', -1) != -1)
                   else len(full_text))
            raw_chunk = full_text[start:end] if curr.get('start_idx') != -1 else ""

        node = await process_chunk(curr, raw_chunk, mapped, i)
        final_nodes.append(node)

    doc.close()
    
    metadata = {
        "toc_confidence": toc_confidence,
        "llm_refinements": llm_refinements,
        "total_chapters": len(mapped),
        "low_confidence_count": sum(1 for m in mapped if m['confidence'] < 0.7)
    }

    return final_nodes, sequence, metadata


def parse_pdf_fast(file_path) -> tuple:
    """Синхронная обёртка для async парсера (для HTTP endpoint)."""
    import asyncio
    
    # Проверка синхронно на encrypted/scan без полного парсинга
    doc_type_info = detect_pdf(file_path)
    if doc_type_info['type'] == DocType.ENCRYPTED:
        raise ValueError("PDF зашифрован и не может быть обработан")
    
    if doc_type_info['type'] == DocType.PDF_SCAN:
        raise ValueError("PDF является сканированным документом (нет текстового слоя). Требуется OCR.")

    # Для обычного текста запускаем async версию
    return asyncio.run(parse_pdf_fast_async(file_path))
