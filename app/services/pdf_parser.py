import fitz
import re
from app.models import BookNode
from app.services.toc_parser import HeuristicParser, toc_to_linear_sequence
from app.services.llm_engine import llm_client


# --- ОБЩИЕ ФУНКЦИИ ---

def normalize(text):
    return re.sub(r'[\W_]+', '', text).lower()


def get_all_text(doc):
    """Весь текст книги в одну строку."""
    return "".join([page.get_text() + "\n" for page in doc])


def find_toc_boundary(full_text, sequence):
    """Находит конец оглавления, чтобы не искать заголовки внутри него."""
    if not sequence: return 0
    # Ищем заголовок 3-го или 5-го раздела (чтобы уйти от начала)
    check_item = sequence[min(len(sequence) - 1, 5)]
    clean_title = check_item['title'].strip()
    idx = full_text.find(clean_title)
    if idx != -1:
        return idx
    return 5000  # Fallback: пропускаем первые 5000 символов


def find_real_indices(full_text, sequence):
    """
    Находит индексы начала каждого раздела в тексте.
    """
    start_search = find_toc_boundary(full_text, sequence)
    indices_map = []

    current_pos = start_search

    for item in sequence:
        title = item['title'].strip()

        # 1. Точный поиск
        idx = full_text.find(title, current_pos)

        # 2. Если не нашли, ищем упрощенно (первые слова)
        if idx == -1:
            short_title = " ".join(title.split()[:3])  # Первые 3 слова
            if len(short_title) > 5:
                idx = full_text.find(short_title, current_pos)

        if idx != -1:
            indices_map.append({
                "item": item,
                "start_idx": idx
            })
            current_pos = idx + 1  # Сдвигаем поиск
        else:
            # Если не нашли, раздел пропускается (сливается с предыдущим)
            print(f"WARN: Раздел не найден в тексте: {title}")

    return indices_map


def clean_text_algorithmic(text, title):
    """
    Быстрая алгоритмическая очистка (без нейросети).
    """
    # 1. Удаляем сам заголовок из начала (если он там есть)
    if text.strip().startswith(title):
        text = text.strip()[len(title):].strip()

    # 2. Убираем множественные переносы
    text = re.sub(r'\n{3,}', '\n\n', text)

    # 3. Убираем строки, похожие на номера страниц (одиночные цифры)
    lines = text.split('\n')
    cleaned_lines = [l for l in lines if not re.match(r'^\s*\d+\s*$', l)]

    return "\n".join(cleaned_lines)


# --- ГЛАВНАЯ ФУНКЦИЯ ---

def parse_pdf(file_path, mode="neural") -> list[BookNode]:
    doc = fitz.open(file_path)

    # 1. Структура (Оглавление)
    # Сначала пробуем алгоритмически (быстро)
    toc_text = ""
    for i in range(min(15, len(doc))): toc_text += doc[i].get_text() + "\n"

    parser = HeuristicParser()
    toc_tree = parser.parse_toc(toc_text)

    # Если алгоритм не справился (пустое дерево), зовем LLM только для оглавления
    if not toc_tree.children:
        print("Алгоритм не нашел оглавление, пробуем LLM...")
        toc_list = llm_client.extract_toc_json(toc_text)
        # Тут нужно конвертировать JSON список обратно в структуру, если нужно,
        # но для простоты sequence мы можем собрать и из списка LLM.
        sequence = toc_list  # LLM возвращает список словарей
        # Сортируем
        sequence.sort(key=lambda x: x.get('page', 0))
    else:
        sequence = toc_to_linear_sequence(toc_tree)
        sequence.sort(key=lambda x: x['page'])

    if not sequence:
        doc.close()
        return [{"title": "Ошибка", "content": "Оглавление не найдено", "level": 0}]

    print(f"Найдено {len(sequence)} разделов. Режим: {mode}")

    # 2. Читаем весь текст
    full_text = get_all_text(doc)

    # 3. Находим границы разделов
    mapped_items = find_real_indices(full_text, sequence)

    final_nodes = []

    for i in range(len(mapped_items)):
        curr_map = mapped_items[i]
        curr_item = curr_map['item']
        start_idx = curr_map['start_idx']

        # Конец текущего = начало следующего
        if i + 1 < len(mapped_items):
            end_idx = mapped_items[i + 1]['start_idx']
        else:
            end_idx = len(full_text)

        # Вырезаем сырой кусок
        raw_chunk = full_text[start_idx: end_idx]

        # 4. ОБРАБОТКА (Ветвление)
        clean_content = ""

        if mode == "neural":
            print(f"LLM Cleaning: {curr_item['title']}...")
            clean_content = llm_client.process_large_text(raw_chunk)
        else:
            # Algorithmic (Fast)
            clean_content = clean_text_algorithmic(raw_chunk, curr_item['title'])

        final_nodes.append({
            "title": curr_item['title'],
            "content": clean_content,
            "level": curr_item.get('level', 1),
            "page": curr_item.get('page', 0)
        })

    doc.close()
    return final_nodes