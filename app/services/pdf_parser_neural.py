import fitz
from .pdf_utils import (
    get_all_text,
    find_real_indices,
    clean_footer_header,
    fast_clean_chunk,
    get_confidence_stats,
    check_document_readability,
)
from .toc_parser import HeuristicParser, toc_to_linear_sequence
from .llm_engine import llm_client, LLM_BOUNDARY_CONTEXT

# Чанки с confidence НИЖЕ этого порога идут в LLM.
# 0.80: exact (1.0) и tokenized_regex (0.85) — только алгоритм.
# partial_words (0.60) и num_prefix (0.40) — LLM.
CONFIDENCE_THRESHOLD = 0.80

_GARBAGE_NOTICE = (
    "[ДОКУМЕНТ НЕЧИТАЕМ]\n"
    "Текст содержит артефакты плохого OCR или нечитаемый шрифт.\n"
    "Для таких документов необходим полноценный OCR (например, Tesseract или Vision LLM)."
)


async def parse_pdf_neural(file_path: str, progress_callback=None) -> tuple:
    """
    Гибридный режим: алгоритм + селективный LLM.

    Сначала проверяет читаемость документа.
    Если текст нечитаем — сразу возвращает предупреждение,
    не тратя время на LLM-вызовы с мусорным входом.

    Пороги:
      confidence >= 0.80  →  fast_clean_chunk (алгоритм)
      confidence <  0.80  →  fix_chapter_boundary + LLM
      confidence == 0.0   →  глава не найдена, пустой узел
    """
    doc = fitz.open(file_path)

    # --- Проверка читаемости ---
    if progress_callback:
        await progress_callback(3, "Проверка читаемости документа...")

    readability = check_document_readability(doc)
    print(f"[neural] Читаемость: {readability}")

    if not readability['is_readable']:
        doc.close()
        if progress_callback:
            await progress_callback(100, "Документ нечитаем — OCR требуется")
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
    if progress_callback:
        await progress_callback(5, "Поиск оглавления...")

    toc_raw = ""
    for i in range(min(20, len(doc))):
        toc_raw += doc[i].get_text() + "\n"

    parser = HeuristicParser()
    toc_tree = parser.parse_toc(toc_raw)
    sequence = toc_to_linear_sequence(toc_tree)

    # --- Полный текст и маппинг ---
    if progress_callback:
        await progress_callback(8, "Чтение и маппинг текста...")

    full_text = get_all_text(doc)
    full_text = clean_footer_header(full_text)
    mapped = find_real_indices(full_text, sequence)

    stats = get_confidence_stats(mapped)
    print(f"[neural] Маппинг: {stats}")

    llm_count = sum(1 for m in mapped if 0 < m['confidence'] < CONFIDENCE_THRESHOLD)
    algo_count = len(mapped) - llm_count
    print(f"[neural] Алгоритм: {algo_count}, LLM: {llm_count}")

    # --- Обрабатываем чанки ---
    final_nodes = []
    total = len(mapped)

    for i, curr in enumerate(mapped):
        confidence = curr['confidence']
        title = curr['item']['title']

        if progress_callback:
            if confidence == 0.0:
                mode_label = "⚠ не найдено"
            elif confidence >= CONFIDENCE_THRESHOLD:
                mode_label = f"алгоритм [{curr['match_strategy']}]"
            else:
                mode_label = f"LLM [{curr['match_strategy']} conf={confidence:.2f}]"
            pct = int(10 + (i / max(total, 1)) * 85)
            await progress_callback(pct, f"{mode_label}: {title[:40]}")

        # Глава не найдена
        if curr['start_idx'] == -1:
            print(f"[neural] Пропущена: «{title}»")
            final_nodes.append({
                "title": title,
                "content": "",
                "level": curr['item'].get('level', 1),
                "page": curr['item'].get('page', 0),
                "confidence": 0.0,
            })
            continue

        # Границы чанка — ищем следующий раздел в порядке текста, а не ToC.
        start = curr['end_idx']
        end = min(
            (m['start_idx'] for m in mapped if m['start_idx'] > start),
            default=len(full_text)
        )
        raw_chunk = full_text[start:end].strip()

        if not raw_chunk:
            final_nodes.append({
                "title": title,
                "content": "",
                "level": curr['item'].get('level', 1),
                "page": curr['item'].get('page', 0),
                "confidence": confidence,
            })
            continue

        # Ветвление по confidence
        if confidence >= CONFIDENCE_THRESHOLD:
            clean_content = fast_clean_chunk(raw_chunk)
        else:
            # Уточняем границу через LLM
            boundary_offset = await llm_client.fix_chapter_boundary(
                title, raw_chunk[:LLM_BOUNDARY_CONTEXT]
            )
            if boundary_offset and boundary_offset < len(raw_chunk):
                raw_chunk = raw_chunk[boundary_offset:]

            clean_content = (
                await llm_client.process_large_text(raw_chunk, is_start=True)
                if len(raw_chunk) > 10
                else ""
            )

        final_nodes.append({
            "title": title,
            "content": clean_content,
            "level": curr['item'].get('level', 1),
            "page": curr['item'].get('page', 0),
            "confidence": confidence,
            "match_strategy": curr.get('match_strategy', ''),
        })

    if progress_callback:
        await progress_callback(97, "Формирование XML...")

    doc.close()
    return final_nodes, sequence
