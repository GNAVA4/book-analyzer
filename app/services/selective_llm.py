import re
from app.services.config import CONFIDENCE_THRESHOLD, LLM_REFINEMENT_THRESHOLD
from app.services.llm_engine import llm_client


def fast_clean(text: str) -> str:
    """Быстрая очистка без LLM для высококонфидентных чанков."""
    # Склеиваем переносы
    text = re.sub(r'(\w+)-\n\s*(\w+)', r'\1\2', text)
    # Убираем множественные переносы строк
    text = re.sub(r'\n{3,}', '\n\n', text)
    # Убираем строки-одиночки (номера страниц)
    lines = text.split('\n')
    lines = [l for l in lines if not re.match(r'^\s*\d{1,4}\s*$', l.strip())]
    return '\n'.join(lines)


async def process_chunk(curr_item, raw_chunk, mapped_items, i):
    """Обрабатывает один чанк: LLM cleanup или fast_clean.
    
    Returns:
        dict с cleaned content и metadata
    """
    confidence = curr_item['confidence']

    if confidence == 0.0:
        # Глава не найдена — пропускаем (обработка missing chapters на уровне парсера)
        return {
            "title": curr_item['item']['title'],
            "content": "",
            "level": curr_item['item'].get('level', 1),
            "page": curr_item['item'].get('page', 0),
            "confidence": 0.0,
            "strategy": "not_found"
        }

    # Проверяем что start_idx валиден
    if curr_item.get('start_idx') == -1:
        return {
            "title": curr_item['item']['title'],
            "content": "",
            "level": curr_item['item'].get('level', 1),
            "page": curr_item['item'].get('page', 0),
            "confidence": confidence,
            "strategy": curr_item.get('strategy', 'unknown')
        }

    start = curr_item['end_idx']
    
    # Находим начало следующей главы (первый валидный start_idx после текущей)
    next_start = len(raw_chunk)  # default: до конца текста
    for j in range(i + 1, min(i + 50, len(mapped_items))):
        ns = mapped_items[j].get('start_idx')
        if ns is not None and ns != -1 and ns > start:
            next_start = ns
            break

    end = next_start
    
    # Защита: если start >= end — чанк пустой или некорректный
    if start >= end:
        return {
            "title": curr_item['item']['title'],
            "content": "",
            "level": curr_item['item'].get('level', 1),
            "page": curr_item['item'].get('page', 0),
            "confidence": confidence,
            "strategy": curr_item.get('strategy', 'unknown') + "_empty_slice"
        }

    raw_chunk = raw_chunk[start:end].strip()

    if len(raw_chunk) < 10:
        return {
            "title": curr_item['item']['title'],
            "content": "",
            "level": curr_item['item'].get('level', 1),
            "page": curr_item['item'].get('page', 0),
            "confidence": confidence,
            "strategy": curr_item.get('strategy', 'unknown')
        }

    if confidence < LLM_REFINEMENT_THRESHOLD:
        # Низкий confidence: LLM уточняет начало главы
        boundary_hint = await llm_client.fix_boundary(
            curr_item['item']['title'], raw_chunk[:800]
        )
        if boundary_hint > 0 and boundary_hint < len(raw_chunk):
            raw_chunk = raw_chunk[boundary_hint:]

    # Определяем режим очистки
    if confidence < CONFIDENCE_THRESHOLD:
        # Ниже порога — LLM cleanup
        clean_content = await llm_client.process_large_text(raw_chunk, is_start=True)
        strategy = "llm_cleaned"
    else:
        # Выше порога — быстрая очистка без LLM
        clean_content = fast_clean(raw_chunk)
        strategy = curr_item.get('strategy', 'unknown')

    return {
        "title": curr_item['item']['title'],
        "content": clean_content,
        "level": curr_item['item'].get('level', 1),
        "page": curr_item['item'].get('page', 0),
        "confidence": confidence,
        "strategy": strategy
    }


async def process_missing_chapters(mapped_items, doc):
    """Обработка не найденных глав (confidence=0) через LLM.
    
    Returns:
        Обновлённый список mapped_items с вставленными главами
    """
    import fitz
    
    new_items = []
    llm_count = 0

    for i, item in enumerate(mapped_items):
        if item['confidence'] != 0.0:
            new_items.append(item)
            continue

        # Не найдена — пробуем LLM в ±3 страницы вокруг ожидаемой позиции
        expected_page = item['item'].get('page')
        
        if expected_page and isinstance(expected_page, (int, float)):
            page_idx = max(0, min(int(expected_page) - 1, len(doc) - 1))
            start_p = max(0, page_idx - 3)
            end_p = min(len(doc), page_idx + 4)
        else:
            # Нет страницы — ищем в начале книги (fallback)
            start_p = 0
            end_p = min(25, len(doc))

        context_text = ""
        for p in range(start_p, end_p):
            context_text += doc[p].get_text() + "\n"

        result = await llm_client.find_missing_chapter(
            item['item']['title'], context_text, expected_page
        )

        if result['found']:
            # Вставляем главу перед текущей позицией (которая была not_found)
            insert_idx = len(new_items)
            new_items.append({
                "item": item['item'],
                "start_idx": start_p * 10000 + result['start_offset'],
                "end_idx": None,  # Будет вычислено позже
                "confidence": 0.75,
                "strategy": "llm_found_missing"
            })
            llm_count += 1
        else:
            # LLM тоже не нашёл — оставляем как not_found (пропускается при сборке)
            new_items.append(item)

    # Пересортировка и коррекция end_idx
    new_items.sort(key=lambda x: x['start_idx'] if x.get('start_idx') != -1 else float('inf'))
    
    for i in range(len(new_items)):
        if new_items[i].get('end_idx') is None and i + 1 < len(new_items):
            next_start = new_items[i + 1].get('start_idx', float('inf'))
            new_items[i]['end_idx'] = next_start

    return new_items, llm_count
