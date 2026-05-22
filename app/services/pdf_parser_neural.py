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
from .embedding_engine import embedding_client
from .ocr_engine import ocr_client

# Чанки с confidence НИЖЕ этого порога идут в LLM.
# 0.80: exact (1.0) и tokenized_regex (0.85) — только алгоритм.
# partial_words (0.60) и num_prefix (0.40) — LLM.
CONFIDENCE_THRESHOLD = 0.80

# Если эвристика нашла ≤ этого числа пунктов ToC — пробуем LLM-fallback.
# Защищает от книг без явного оглавления (титул/копирайт сразу за обложкой).
TOC_HEURISTIC_MIN_SECTIONS = 3
# Сколько страниц передавать LLM для извлечения ToC.
# В LM Studio qwen2.5-7b часто загружается с context=4096 — это ~2500 chars
# для русского/кириллицы + ~500 на инструкции + 1024 на JSON-ответ.
# Если context увеличен (8K/32K) — можно поднять TOC_LLM_MAX_CHARS до 12_000+.
TOC_LLM_PAGES = 15
TOC_LLM_MAX_CHARS = 6_000

# Размер окна (в символах) для точечного LLM-поиска не найденных разделов.
# Окно центрируется вокруг ожидаемой позиции (между предыдущей и следующей
# найденной секциями) или вокруг страницы из ToC, если она известна.
LLM_LOCATE_WINDOW = 8_000

_GARBAGE_NOTICE = (
    "[ДОКУМЕНТ НЕЧИТАЕМ]\n"
    "Текст содержит артефакты плохого OCR или нечитаемый шрифт.\n"
    "Для таких документов необходим полноценный OCR (например, Tesseract или Vision LLM)."
)


async def parse_pdf_neural(
    file_path: str,
    progress_callback=None,
    deep_scan: bool = False,
    use_ocr: bool = True,
) -> tuple:
    """
    Гибридный режим: алгоритм + селективный LLM.

    Этапы:
      1. Проверка читаемости (детектор кракозябр).
      2. Эвристический ToC. Если найдено ≤ TOC_HEURISTIC_MIN_SECTIONS —
         LLM-fallback на извлечение ToC из первых страниц.
      3. Если deep_scan=True и оба способа дали мало — LLM генерирует
         структуру по chunks полного текста (МЕДЛЕННО, для книг без ToC).
      4. Маппинг секций с confidence-scoring.
      5. Очистка чанков: алгоритмически при confidence ≥ 0.80, через LLM иначе.

    Возвращает tuple (final_nodes, sequence, meta), где meta содержит
    диагностику (toc_source, прочее).
    """
    doc = fitz.open(file_path)

    # --- Проверка читаемости ---
    if progress_callback:
        await progress_callback(3, "Проверка читаемости документа...")

    readability = check_document_readability(doc)
    print(f"[neural] Читаемость: {readability}")

    # OCR-fallback: если документ нечитаем, но glm-ocr доступен — пробуем OCR.
    ocr_text: str | None = None
    if not readability['is_readable']:
        if use_ocr and await ocr_client.is_available():
            if progress_callback:
                await progress_callback(5, "Документ нечитаем — запуск OCR через glm-ocr...")
            try:
                ocr_text = await ocr_client.ocr_document(doc, progress_callback=progress_callback)
            except Exception as e:
                print(f"[neural] OCR failed: {e}")
                ocr_text = None

        if not ocr_text or len(ocr_text.strip()) < 100:
            doc.close()
            if progress_callback:
                await progress_callback(100, "Документ нечитаем, OCR не помог")
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
            ], [], {"toc_source": "none", "reason": "unreadable"}

    # --- ToC (эвристика) ---
    if progress_callback:
        await progress_callback(5, "Поиск оглавления (эвристика)...")

    # Если OCR прошёл — используем его текст; иначе берём извлечённый напрямую.
    if ocr_text:
        toc_raw = ocr_text[:30_000]
    else:
        toc_raw = ""
        for i in range(min(20, len(doc))):
            toc_raw += doc[i].get_text() + "\n"

    parser = HeuristicParser()
    toc_tree = parser.parse_toc(toc_raw)
    sequence = toc_to_linear_sequence(toc_tree)
    toc_source = "heuristic_ocr" if ocr_text else "heuristic"

    # --- LLM-fallback для оглавления ---
    # Если эвристика практически ничего не нашла (титульная страница без ToC,
    # сложная вёрстка), просим LLM извлечь структуру из первых страниц.
    if len(sequence) <= TOC_HEURISTIC_MIN_SECTIONS:
        if progress_callback:
            await progress_callback(
                7,
                f"Эвристика нашла {len(sequence)} пунктов — запрашиваю ToC у LLM..."
            )
        if ocr_text:
            llm_pages = ocr_text[:TOC_LLM_MAX_CHARS]
        else:
            llm_pages = ""
            for i in range(min(TOC_LLM_PAGES, len(doc))):
                llm_pages += doc[i].get_text() + "\n"
        llm_items = await llm_client.extract_toc_json(llm_pages[:TOC_LLM_MAX_CHARS])
        if len(llm_items) > len(sequence):
            # Нормализуем формат к тому, что использует find_real_indices
            sequence = [
                {
                    "title": str(it.get("title", "")).strip(),
                    "level": int(it.get("level", 1)) if it.get("level") else 1,
                    "page": int(it.get("page")) if str(it.get("page", "")).isdigit() else None,
                }
                for it in llm_items
                if it.get("title")
            ]
            toc_source = "llm"
            print(f"[neural] LLM-ToC: {len(sequence)} пунктов")

    print(f"[neural] ToC source: {toc_source}, sections: {len(sequence)}")

    # --- Deep LLM scan: документ совсем без ToC ---
    # Дорогой режим, включается флагом. LLM проходит по полному тексту
    # chunks и сама придумывает заголовки разделов.
    deep_scan_used = False
    if deep_scan and len(sequence) <= TOC_HEURISTIC_MIN_SECTIONS:
        if progress_callback:
            await progress_callback(
                8, "Deep-scan: LLM анализирует полный текст для построения структуры..."
            )
        # Полный текст потребуется ниже; читаем его сейчас один раз.
        full_text_for_scan = ocr_text if ocr_text else get_all_text(doc)
        proposed = await llm_client.propose_structure_from_text(full_text_for_scan)
        if len(proposed) > len(sequence):
            sequence = proposed
            toc_source = "llm_deep_scan"
            deep_scan_used = True
            print(f"[neural] Deep-scan: предложено {len(sequence)} разделов")

    # --- Полный текст и маппинг ---
    if progress_callback:
        await progress_callback(8, "Чтение и маппинг текста...")

    full_text = ocr_text if ocr_text else get_all_text(doc)
    full_text = clean_footer_header(full_text)
    mapped = find_real_indices(full_text, sequence)

    stats = get_confidence_stats(mapped)
    print(f"[neural] Маппинг: {stats}")

    # --- LLM-rescue: для каждого not_found пытаемся найти через LLM ---
    rescued = 0
    not_found_idxs = [i for i, m in enumerate(mapped) if m['start_idx'] == -1]
    if not_found_idxs:
        if progress_callback:
            await progress_callback(
                9, f"LLM-rescue: пытаюсь найти {len(not_found_idxs)} ненайденных секций..."
            )
        for i in not_found_idxs:
            curr = mapped[i]
            title = curr['item']['title']
            # Определяем окно: между концом предыдущей найденной и началом следующей
            prev_end = next(
                (mapped[j]['end_idx'] for j in range(i - 1, -1, -1)
                 if mapped[j]['start_idx'] != -1),
                0
            )
            next_start = next(
                (mapped[j]['start_idx'] for j in range(i + 1, len(mapped))
                 if mapped[j]['start_idx'] != -1),
                len(full_text)
            )
            # Если окно слишком большое, ограничиваем
            if next_start - prev_end > LLM_LOCATE_WINDOW:
                # Центр окна — если есть страница в ToC, аппроксимируем через
                # пропорциональную позицию относительно соседей; иначе берём prev_end
                window_start = prev_end
                window_end = min(prev_end + LLM_LOCATE_WINDOW, next_start)
            else:
                window_start = prev_end
                window_end = next_start

            window_text = full_text[window_start:window_end]

            # Этап 1: семантический поиск через embeddings (быстро, дёшево).
            emb_offset, emb_score = await embedding_client.locate_section(title, window_text)
            if emb_offset >= 0:
                abs_start = window_start + emb_offset
                abs_end = min(abs_start + len(title) + 20, window_end)
                mapped[i]['start_idx'] = abs_start
                mapped[i]['end_idx'] = abs_end
                mapped[i]['confidence'] = round(min(emb_score, 0.70), 2)
                mapped[i]['match_strategy'] = 'embedding_rescue'
                rescued += 1
                continue

            # Этап 2: LLM-rescue (дороже, но точнее на похожих формулировках).
            offset = await llm_client.locate_section_in_text(title, window_text)
            if offset >= 0:
                abs_start = window_start + offset
                abs_end = min(abs_start + len(title) + 20, window_end)
                mapped[i]['start_idx'] = abs_start
                mapped[i]['end_idx'] = abs_end
                mapped[i]['confidence'] = 0.55
                mapped[i]['match_strategy'] = 'llm_rescue'
                rescued += 1
        print(f"[neural] Rescue: {rescued}/{len(not_found_idxs)}")

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
    meta = {
        "toc_source": toc_source,
        "deep_scan_used": deep_scan_used,
        "ocr_used": ocr_text is not None,
    }
    return final_nodes, sequence, meta
