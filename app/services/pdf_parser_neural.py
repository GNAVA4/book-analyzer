"""
Гибридный neural-парсер PDF.

Использует многоуровневые pipeline'ы из toc_builder и mapping_pipeline:
  - ToC извлекается через каскад: эвристика → LLM → OCR → deep_scan → expand
  - Маппинг секций: эвристика → page-hint → embedding → LLM → verify-order
  - Очистка: алгоритм → LLM при низком confidence

Сам файл — тонкий координатор, вся отказоустойчивая логика в _builder / _pipeline.
"""

import fitz
from .pdf_utils import (
    get_all_text,
    clean_footer_header,
    fast_clean_chunk,
    get_confidence_stats,
    check_document_readability,
)
from .llm_engine import llm_client, LLM_BOUNDARY_CONTEXT
from .ocr_engine import ocr_client
from .toc_builder import build_toc
from .toc_validator import validate_toc_via_ocr
from .mapping_pipeline import map_sequence


# Чанки с confidence НИЖЕ этого порога идут в LLM для очистки.
# 0.85 — компромисс: exact (1.0), tokenized_regex (0.85), page_hint_exact (0.90)
# идут через алгоритм (они надёжные); rescue-стратегии (0.40-0.80) и
# partial / num_prefix идут через LLM, где это реально нужно.
# Поднимать до 0.90+ не имеет смысла: LLM-clean часто возвращает оригинал
# из-за context errors / CJK retry — зря тратит время без улучшения качества.
CONFIDENCE_THRESHOLD = 0.85


_GARBAGE_NOTICE = (
    "[ДОКУМЕНТ НЕЧИТАЕМ]\n"
    "Текст содержит артефакты плохого OCR или нечитаемый шрифт.\n"
    "OCR через glm-ocr был запущен, но не смог восстановить текст."
)


async def parse_pdf_neural(
    file_path: str,
    progress_callback=None,
    deep_scan: bool = True,
    use_ocr: bool = True,
    llm_expand: bool = True,
    validate_toc_ocr: bool = True,
) -> tuple:
    """
    Гибридный режим: многоуровневый pipeline с отказоустойчивыми fallback'ами.

    Параметры:
      deep_scan         — LLM-сканирование по chunks при отсутствии ToC (МЕДЛЕННО)
      use_ocr           — разрешать OCR через glm-ocr для нечитаемых документов
      llm_expand        — расширять верхнеуровневый ToC поиском глав внутри частей
      validate_toc_ocr  — после извлечения ToC прогнать OCR ±5 стр. вокруг
                          оглавления для верификации (стоит ~1 минуту, читаемые
                          PDF; для OCR-derived ToC пропускается)

    Возвращает (final_nodes, sequence, meta).
    meta содержит: toc_source, deep_scan_used, ocr_used, toc_validation (опц).
    """
    doc = fitz.open(file_path)

    # --- Этап 0: проверка читаемости ---
    if progress_callback:
        await progress_callback(3, "Проверка читаемости документа...")

    readability = check_document_readability(doc)
    print(f"[neural] Читаемость: {readability}")

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
            ], [], {"toc_source": "none", "reason": "unreadable", "ocr_used": False}

    # --- Этап 1: многоуровневое извлечение ToC ---
    def _extract_full_text() -> str:
        return ocr_text if ocr_text else get_all_text(doc)

    sequence, toc_source, ocr_text = await build_toc(
        doc,
        full_text_extractor=_extract_full_text,
        ocr_text=ocr_text,
        progress_cb=progress_callback,
        enable_ocr=use_ocr,
        enable_deep_scan=deep_scan,
        enable_llm_expand=llm_expand,
    )

    print(f"[neural] ToC source: {toc_source}, sections: {len(sequence)}")

    # --- Этап 1b: опциональная OCR-валидация ToC ---
    # Пропускаем когда ToC уже пришёл из OCR (тогда валидация = self-comparison).
    toc_validation = None
    if validate_toc_ocr and sequence and not toc_source.startswith('ocr'):
        try:
            toc_validation = await validate_toc_via_ocr(
                doc, sequence, progress_cb=progress_callback
            )
            print(f"[neural] ToC validation: {toc_validation}")
        except Exception as e:
            print(f"[neural] ToC validation failed: {e}")
            toc_validation = {"error": str(e)}

    # --- Этап 2: полный текст и очистка колонтитулов ---
    if progress_callback:
        await progress_callback(15, "Подготовка полного текста...")

    full_text = ocr_text if ocr_text else get_all_text(doc)
    full_text = clean_footer_header(full_text)
    total_pages = len(doc)

    # --- Safety net: если ToC pipeline ничего не нашёл, но текст есть ---
    # Без секций XML был бы пуст. Возвращаем единственную секцию со всем
    # текстом — пусть пользователь видит хоть какое-то содержимое и понимает
    # что pipeline провалился именно на этапе извлечения структуры.
    if not sequence and full_text and len(full_text.strip()) >= 100:
        if progress_callback:
            await progress_callback(95, "Структура не извлечена — возвращаем полный текст одной секцией")
        doc.close()
        return (
            [{
                "title": "Полный текст книги",
                "content": full_text,
                "level": 1,
                "page": 0,
                "confidence": 0.0,
                "match_strategy": "fallback_full_text",
            }],
            [{"title": "Полный текст книги", "level": 1, "page": None}],
            {
                "toc_source": toc_source,
                "deep_scan_used": deep_scan and "deep_scan" in toc_source,
                "ocr_used": ocr_text is not None,
                "fallback": "no_toc_extracted",
            },
        )

    # --- Этап 3: многоуровневый маппинг ---
    if progress_callback:
        await progress_callback(20, f"Маппинг {len(sequence)} секций...")

    mapped = await map_sequence(sequence, full_text, total_pages, progress_callback)
    stats = get_confidence_stats(mapped)
    print(f"[neural] Маппинг: {stats}")

    # --- Этап 4: нарезка контента + очистка ---
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
            pct = int(30 + (i / max(total, 1)) * 65)
            await progress_callback(pct, f"{mode_label}: {title[:40]}")

        if curr['start_idx'] == -1:
            final_nodes.append({
                "title": title,
                "content": "",
                "level": curr['item'].get('level', 1),
                "page": curr['item'].get('page', 0),
                "confidence": 0.0,
                "match_strategy": curr.get('match_strategy', 'not_found'),
            })
            continue

        # Текст-окно от конца этой секции до начала следующей в текстовом порядке
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
                "match_strategy": curr.get('match_strategy', ''),
            })
            continue

        # Очистка: алгоритм или LLM в зависимости от confidence
        if confidence >= CONFIDENCE_THRESHOLD:
            clean_content = fast_clean_chunk(raw_chunk)
        else:
            boundary_offset = await llm_client.fix_chapter_boundary(
                title, raw_chunk[:LLM_BOUNDARY_CONTEXT]
            )
            adjusted = boundary_offset > 0 and boundary_offset < len(raw_chunk)
            if adjusted:
                raw_chunk = raw_chunk[boundary_offset:]
            clean_content = (
                await llm_client.process_large_text(raw_chunk, is_start=not adjusted)
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
        "deep_scan_used": deep_scan and "deep_scan" in toc_source,
        "ocr_used": ocr_text is not None,
    }
    if toc_validation is not None:
        meta["toc_validation"] = toc_validation
    return final_nodes, sequence, meta
