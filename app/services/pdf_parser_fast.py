import fitz
import re
from .pdf_utils import (
    get_all_text,
    find_real_indices,
    clean_footer_header,
    fast_clean_chunk,
    get_confidence_stats,
    check_document_readability,
)
from .toc_parser import HeuristicParser, toc_to_linear_sequence

# Сообщение, которое вставляется в content нечитаемых документов
_GARBAGE_NOTICE = (
    "[ДОКУМЕНТ НЕЧИТАЕМ]\n"
    "Текст извлечён с артефактами (плохой OCR или нечитаемый шрифт).\n"
    "Используйте нейросетевой режим или конвертируйте PDF в читаемый формат."
)


def parse_pdf_fast(file_path: str) -> tuple:
    """
    Быстрый алгоритмический режим.

    Перед парсингом проверяет читаемость документа.
    Если текст — кракозябры, возвращает единственный узел с предупреждением
    вместо мусорного XML.
    """
    doc = fitz.open(file_path)

    # --- Проверка читаемости ---
    readability = check_document_readability(doc)
    print(f"[fast] Читаемость: {readability}")

    if not readability['is_readable']:
        doc.close()
        return [
            {
                "title": "Ошибка чтения документа",
                "content": (
                    f"{_GARBAGE_NOTICE}\n\n"
                    f"Диагностика: {readability['detail']}\n"
                    f"Доля мусорных символов: {readability['garbage_ratio']:.1%}"
                ),
                "level": 1,
                "page": 0,
                "confidence": 0.0,
            }
        ], []

    # --- ToC ---
    toc_raw = ""
    for i in range(min(25, len(doc))):
        toc_raw += doc[i].get_text() + "\n"

    parser = HeuristicParser()
    toc_tree = parser.parse_toc(toc_raw)
    sequence = toc_to_linear_sequence(toc_tree)

    # --- Полный текст ---
    full_text = get_all_text(doc)
    full_text = clean_footer_header(full_text)

    # --- Маппинг с confidence ---
    mapped = find_real_indices(full_text, sequence)
    stats = get_confidence_stats(mapped)
    print(f"[fast] Маппинг: {stats}")

    # --- Нарезка и очистка ---
    final_nodes = []

    for i, curr in enumerate(mapped):
        if curr['start_idx'] == -1:
            print(f"[fast] Пропущена: «{curr['item']['title']}»")
            final_nodes.append({
                "title": curr['item']['title'],
                "content": "",
                "level": curr['item'].get('level', 1),
                "page": curr['item'].get('page', 0),
                "confidence": 0.0,
            })
            continue

        start = curr['end_idx']
        # Ищем следующий раздел в порядке текста, а не ToC —
        # это корректно работает даже если предыдущие главы не найдены.
        end = min(
            (m['start_idx'] for m in mapped if m['start_idx'] > start),
            default=len(full_text)
        )

        raw_content = full_text[start:end].strip()
        clean_content = fast_clean_chunk(raw_content)

        final_nodes.append({
            "title": curr['item']['title'],
            "content": clean_content,
            "level": curr['item'].get('level', 1),
            "page": curr['item'].get('page', 0),
            "confidence": curr['confidence'],
            "match_strategy": curr['match_strategy'],
        })

    doc.close()
    return final_nodes, sequence
