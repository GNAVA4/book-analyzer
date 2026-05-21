import xml.etree.ElementTree as ET
from xml.dom import minidom
import re


# ---------------------------------------------------------------------------
# Утилиты
# ---------------------------------------------------------------------------

def clean_xml_string(s: str) -> str:
    """Убирает символы, недопустимые в XML."""
    if not s:
        return ""
    illegal_chars = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f]')
    return illegal_chars.sub('', s)


# ---------------------------------------------------------------------------
# Построение дерева из плоского списка
# ---------------------------------------------------------------------------

def build_tree_structure(flat_nodes: list) -> dict:
    """
    Преобразует плоский список узлов с полем level
    в иерархическое дерево (dict с children).
    """
    root = {
        "title": "Book Root",
        "children": [],
        "level": 0,
        "content": "",
        "page": 0,
        "confidence": 1.0,
    }
    stack = [root]

    for node in flat_nodes:
        if not node.get('title'):
            continue
        new_node = {
            "title": clean_xml_string(node['title']),
            "content": clean_xml_string(node.get('content', '')),
            "children": [],
            "level": node.get('level', 1),
            "page": node.get('page', 0) if node.get('page') else 0,
            # Пробрасываем confidence в дерево (по умолчанию 1.0 для DOCX)
            "confidence": node.get('confidence', 1.0),
        }
        while len(stack) > 1 and stack[-1]['level'] >= new_node['level']:
            stack.pop()
        stack[-1]['children'].append(new_node)
        stack.append(new_node)

    return root


# ---------------------------------------------------------------------------
# Сериализация в XML
# ---------------------------------------------------------------------------

def dict_to_xml(data: dict, toc_items: list = None) -> str:
    """
    Сериализует дерево в XML-строку.

    Атрибуты секции:
      title      — название раздела
      page       — номер страницы (пустая строка если неизвестен)
      confidence — уверенность маппинга (0.00–1.00)
                   Если < 0.70 — значит граница была нечёткой,
                   содержимое обрабатывалось LLM.

    NavigationTable в начале документа содержит линейный список
    всех разделов из оглавления — удобно для навигации.
    """

    def _fmt_page(val) -> str:
        s = str(val) if val else ""
        return "" if s in ("0", "None", "") else s

    def _fmt_conf(val) -> str:
        try:
            return f"{float(val):.2f}"
        except (TypeError, ValueError):
            return "1.00"

    def create_element(node: dict) -> ET.Element:
        elem = ET.Element(
            "section",
            title=node.get('title', ''),
            page=_fmt_page(node.get('page')),
            confidence=_fmt_conf(node.get('confidence', 1.0)),
        )
        content = node.get('content', '')
        if content and content.strip():
            content_elem = ET.SubElement(elem, "content")
            content_elem.text = content
        for child in node.get('children', []):
            elem.append(create_element(child))
        return elem

    # --- Корневой элемент ---
    root_elem = ET.Element("Book")

    # --- Навигационная таблица ---
    if toc_items:
        nav = ET.SubElement(root_elem, "NavigationTable")
        for item in toc_items:
            ET.SubElement(
                nav, "Item",
                title=clean_xml_string(str(item.get('title', ''))),
                page=_fmt_page(item.get('page')),
                level=str(item.get('level', '')),
            )

    # --- Разделы ---
    for child in data.get('children', []):
        root_elem.append(create_element(child))

    # --- Сериализация с pretty-print ---
    raw_bytes = ET.tostring(root_elem, encoding='utf-8')
    try:
        parsed = minidom.parseString(raw_bytes)
        return parsed.toprettyxml(indent="  ")
    except Exception:
        return raw_bytes.decode('utf-8')
