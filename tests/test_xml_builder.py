"""Тесты xml_builder."""
import pytest
from xml.etree import ElementTree as ET

from app.services.xml_builder import (
    build_tree_structure,
    dict_to_xml,
    clean_xml_string,
)


# ============================================================================
# clean_xml_string
# ============================================================================

class TestCleanXmlString:
    def test_removes_illegal_chars(self):
        text = "Hello\x00World\x01"
        assert "\x00" not in clean_xml_string(text)
        assert "\x01" not in clean_xml_string(text)
        assert "Hello" in clean_xml_string(text)

    def test_keeps_normal_chars(self):
        text = "Нормальный текст с цифрами 123 и знаками препинания!"
        assert clean_xml_string(text) == text

    def test_keeps_newlines_and_tabs(self):
        text = "line1\nline2\tcol"
        out = clean_xml_string(text)
        assert "\n" in out
        assert "\t" in out

    def test_empty(self):
        assert clean_xml_string("") == ""
        assert clean_xml_string(None) == ""


# ============================================================================
# build_tree_structure — flat → tree
# ============================================================================

class TestBuildTree:
    def test_flat_level_1(self):
        nodes = [
            {"title": "A", "level": 1, "content": "a", "page": 1},
            {"title": "B", "level": 1, "content": "b", "page": 2},
        ]
        tree = build_tree_structure(nodes)
        assert len(tree["children"]) == 2
        assert tree["children"][0]["title"] == "A"
        assert tree["children"][1]["title"] == "B"

    def test_nested_two_levels(self):
        nodes = [
            {"title": "Глава 1", "level": 1, "content": "", "page": 1},
            {"title": "1.1", "level": 2, "content": "a", "page": 2},
            {"title": "1.2", "level": 2, "content": "b", "page": 3},
            {"title": "Глава 2", "level": 1, "content": "", "page": 4},
        ]
        tree = build_tree_structure(nodes)
        # На верхнем уровне 2 главы
        assert len(tree["children"]) == 2
        # У Главы 1 — 2 подраздела
        assert len(tree["children"][0]["children"]) == 2
        # У Главы 2 — нет подразделов
        assert len(tree["children"][1]["children"]) == 0

    def test_three_levels(self):
        nodes = [
            {"title": "Гл 1", "level": 1, "content": "", "page": 1},
            {"title": "1.1", "level": 2, "content": "", "page": 2},
            {"title": "1.1.1", "level": 3, "content": "x", "page": 3},
            {"title": "1.1.2", "level": 3, "content": "y", "page": 4},
            {"title": "1.2", "level": 2, "content": "", "page": 5},
        ]
        tree = build_tree_structure(nodes)
        ch1 = tree["children"][0]
        assert ch1["title"] == "Гл 1"
        assert len(ch1["children"]) == 2  # 1.1 и 1.2
        sub11 = ch1["children"][0]
        assert sub11["title"] == "1.1"
        assert len(sub11["children"]) == 2  # 1.1.1 и 1.1.2

    def test_skips_empty_titles(self):
        nodes = [
            {"title": "A", "level": 1, "content": "", "page": 1},
            {"title": "", "level": 1, "content": "", "page": 2},
            {"title": "B", "level": 1, "content": "", "page": 3},
        ]
        tree = build_tree_structure(nodes)
        titles = [c["title"] for c in tree["children"]]
        assert "" not in titles
        assert "A" in titles and "B" in titles

    def test_preserves_confidence(self):
        nodes = [
            {"title": "A", "level": 1, "content": "", "page": 1, "confidence": 0.85},
        ]
        tree = build_tree_structure(nodes)
        assert tree["children"][0]["confidence"] == 0.85


# ============================================================================
# dict_to_xml — финальная сериализация
# ============================================================================

class TestDictToXml:
    def test_basic_xml_output(self):
        data = {
            "title": "Book Root",
            "children": [
                {"title": "Глава 1", "content": "контент", "level": 1, "page": 1, "confidence": 1.0, "children": []},
            ],
        }
        xml = dict_to_xml(data)
        assert "<Book>" in xml
        assert 'title="Глава 1"' in xml
        assert "контент" in xml

    def test_includes_navigation_table(self):
        data = {"title": "Book Root", "children": []}
        toc = [
            {"title": "Глава 1", "page": 1, "level": 1},
            {"title": "1.1", "page": 2, "level": 2},
        ]
        xml = dict_to_xml(data, toc_items=toc)
        assert "<NavigationTable>" in xml
        assert 'title="Глава 1"' in xml
        assert 'level="2"' in xml

    def test_confidence_attribute(self):
        data = {
            "title": "Book Root",
            "children": [
                {"title": "Гл 1", "content": "c", "level": 1, "page": 1, "confidence": 0.65, "children": []},
            ],
        }
        xml = dict_to_xml(data)
        assert 'confidence="0.65"' in xml

    def test_valid_xml_parseable(self):
        """Сгенерированный XML должен быть валидным XML."""
        data = {
            "title": "Book Root",
            "children": [
                {"title": "A & B", "content": "<bad>", "level": 1, "page": 1, "confidence": 1.0, "children": []},
            ],
        }
        xml = dict_to_xml(data)
        # Не должен упасть при парсинге
        root = ET.fromstring(xml)
        assert root.tag == "Book"

    def test_handles_illegal_xml_chars_in_content(self):
        data = {
            "title": "Book Root",
            "children": [
                {
                    "title": "Title with \x00 nul",
                    "content": "Content with \x01 control",
                    "level": 1, "page": 1, "confidence": 1.0, "children": [],
                },
            ],
        }
        xml = dict_to_xml(data)
        # XML должен быть валидным
        root = ET.fromstring(xml)
        # Управляющие символы вырезаны
        assert "\x00" not in xml
        assert "\x01" not in xml
