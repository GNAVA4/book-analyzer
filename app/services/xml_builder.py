import xml.etree.ElementTree as ET
from xml.dom import minidom
import re


def clean_xml_string(s: str) -> str:
    if not s: return ""
    illegal_chars = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f]')
    return illegal_chars.sub('', s)


def build_tree_structure(flat_nodes: list) -> dict:
    root = {"title": "Book Root", "children": [], "level": 0, "content": "", "page": 0}
    stack = [root]
    for node in flat_nodes:
        if not node.get('title'): continue
        new_node = {
            "title": clean_xml_string(node['title']),
            "content": clean_xml_string(node.get('content', '')),
            "children": [],
            "level": node.get('level', 1),
            "page": node.get('page', 0) if node.get('page') else 0
        }
        while len(stack) > 1 and stack[-1]['level'] >= new_node['level']:
            stack.pop()
        stack[-1]['children'].append(new_node)
        stack.append(new_node)
    return root


def dict_to_xml(data: dict, toc_items: list = None) -> str:
    def create_element(node):
        p = str(node.get('page', ''))
        if p == "0" or p == "None": p = ""
        elem = ET.Element("section", title=node.get('title', ''), page=p)
        if node.get('content') and node['content'].strip():
            content_elem = ET.SubElement(elem, "content")
            content_elem.text = node['content']
        for child in node.get('children', []):
            elem.append(create_element(child))
        return elem

    root_elem = ET.Element("Book")
    if toc_items:
        nav = ET.SubElement(root_elem, "NavigationTable")
        for item in toc_items:
            p = str(item.get('page', ''))
            if p == "None": p = ""
            ET.SubElement(nav, "Item",
                          title=clean_xml_string(str(item.get('title', ''))),
                          page=p,
                          level=str(item.get('level', '')))

    for child in data.get('children', []):
        root_elem.append(create_element(child))

    raw_str = ET.tostring(root_elem, encoding='utf-8')
    try:
        parsed = minidom.parseString(raw_str)
        return parsed.toprettyxml(indent="  ")
    except:
        return raw_str.decode('utf-8')