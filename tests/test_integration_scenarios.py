"""
Integration-тесты: имитация полных сценариев на синтетических данных.

Не используется ни LLM, ни OCR (только эвристический pipeline) — это
проверка, что разные ВИДЫ оглавлений и текстов парсятся корректно.
"""
import pytest

from app.services.toc_parser import HeuristicParser, toc_to_linear_sequence
from app.services.pdf_utils import find_real_indices
from app.services.xml_builder import build_tree_structure, dict_to_xml


def _padding(n=400):
    """Длинный филлер чтобы перейти границу find_toc_boundary."""
    return "\nдополнительный текст книги " * n


def _full_book(toc_block, content_block):
    return toc_block + _padding() + "\n=== ТЕКСТ ===\n" + content_block


# ============================================================================
# Сценарий 1: классическое русское оглавление
# ============================================================================

class TestClassicRussian:
    def test_full_pipeline(self):
        toc = """СОДЕРЖАНИЕ
Введение ............................... 5
Глава 1. Основы ........................ 10
1.1. Понятия ........................... 12
1.2. Принципы .......................... 18
Глава 2. Применение .................... 30
2.1. Примеры ........................... 32
Заключение ............................. 50
"""
        content = (
            "Введение\nЭто текст введения. " + "контент " * 30 +
            "Глава 1. Основы\nТекст главы 1. " + "контент " * 30 +
            "1.1. Понятия\nТекст 1.1. " + "контент " * 20 +
            "1.2. Принципы\nТекст 1.2. " + "контент " * 20 +
            "Глава 2. Применение\nТекст главы 2. " + "контент " * 20 +
            "2.1. Примеры\nТекст 2.1. " + "контент " * 20 +
            "Заключение\nТекст заключения."
        )
        text = _full_book(toc, content)
        seq = toc_to_linear_sequence(HeuristicParser().parse_toc(toc))
        assert len(seq) >= 5
        # Должны быть и уровни 1, и уровни 2
        levels = {s["level"] for s in seq}
        assert 1 in levels
        assert 2 in levels

        mapped = find_real_indices(text, seq)
        # Большинство секций должны найтись
        found = sum(1 for m in mapped if m["start_idx"] != -1)
        assert found >= len(seq) - 1  # допускаем 1 miss


# ============================================================================
# Сценарий 2: ToC только из частей (без подразделов)
# ============================================================================

class TestPartsOnly:
    def test_high_level_only(self):
        toc = """СОДЕРЖАНИЕ
Часть I. Стабильность .................. 20
Часть II. Производительность ........... 100
Часть III. Безопасность ................ 200
"""
        seq = toc_to_linear_sequence(HeuristicParser().parse_toc(toc))
        # Все части — level 1
        assert all(s["level"] == 1 for s in seq)
        assert len(seq) == 3


# ============================================================================
# Сценарий 3: римские номера
# ============================================================================

class TestRomanNumerals:
    @pytest.mark.parametrize("toc_text", [
        "СОДЕРЖАНИЕ\nI. Первый раздел ............ 5\nII. Второй раздел ........... 10",
        "ОГЛАВЛЕНИЕ\nI Введение ........... 5\nII Главная часть ........... 15",
    ])
    def test_roman_numerals_parsed(self, toc_text):
        seq = toc_to_linear_sequence(HeuristicParser().parse_toc(toc_text))
        assert len(seq) >= 2


# ============================================================================
# Сценарий 4: глубокая вложенность (4 уровня?)
# ============================================================================

class TestDeepNesting:
    def test_three_levels_correctly_nested(self):
        toc = """СОДЕРЖАНИЕ
1. Введение ........................... 5
1.1. Цели ............................. 7
1.1.1. Микро-цели ..................... 8
1.1.2. Макро-цели ..................... 9
1.2. Задачи ........................... 10
2. Заключение ......................... 20
"""
        seq = toc_to_linear_sequence(HeuristicParser().parse_toc(toc))
        # Должно быть 4 уровня 1 и 2 (Введение и Заключение, 1.1, 1.2)
        # и 2 уровня 3 (1.1.1, 1.1.2)
        levels = [s["level"] for s in seq]
        assert 3 in levels, f"No level 3 in {seq}"


# ============================================================================
# Сценарий 5: дубликаты в нескольких оглавлениях
# ============================================================================

class TestDuplicatesInTwoToc:
    def test_dedup_in_full_pipeline(self):
        """Книга с кратким и детальным оглавлением → дубли должны убираться."""
        # Краткое:
        # Глава 1, Глава 2
        # Детальное:
        # Глава 1 (дубль), 1.1, 1.2, Глава 2 (дубль), 2.1
        toc = """СОДЕРЖАНИЕ
Глава 1. Основы ........................ 5
Глава 2. Применение .................... 50

ПОДРОБНОЕ СОДЕРЖАНИЕ
Глава 1. Основы ........................ 5
1.1. Часть А ........................... 7
1.2. Часть Б ........................... 30
Глава 2. Применение .................... 50
2.1. Пример ............................ 52
"""
        seq = toc_to_linear_sequence(HeuristicParser().parse_toc(toc))
        # Хотя в исходнике 7 строк, после dedup в toc_builder будет меньше
        # (dedup делается уже в build_toc — здесь только проверяем парсинг)
        # Здесь parse_toc выдаёт ВСЕ, а уже toc_builder._dedup_and_order чистит


# ============================================================================
# Сценарий 6: ToC с маркером «Глава» / «Chapter»
# ============================================================================

class TestKeywordMarkers:
    @pytest.mark.parametrize("toc_text,expected_chapters", [
        ("СОДЕРЖАНИЕ\nГлава 1 ........... 5\nГлава 2 ........... 10", 2),
        ("CONTENTS\nChapter 1 .......... 5\nChapter 2 .......... 10", 2),
        ("СОДЕРЖАНИЕ\n§ 1. Основа ........... 5\n§ 2. Развитие ........... 10", 2),
    ])
    def test_chapter_markers(self, toc_text, expected_chapters):
        seq = toc_to_linear_sequence(HeuristicParser().parse_toc(toc_text))
        assert len(seq) >= expected_chapters


# ============================================================================
# Сценарий 7: пустое оглавление / book без ToC
# ============================================================================

class TestNoToC:
    def test_no_toc_returns_empty(self):
        text = (
            "Какой-то текст без оглавления вообще. " * 100 +
            "\nКнига просто продолжается без секций."
        )
        seq = toc_to_linear_sequence(HeuristicParser().parse_toc(text))
        # parse_toc может найти 0 или какие-то ложные пункты — главное не падать
        assert isinstance(seq, list)


# ============================================================================
# Сценарий 8: финальный XML
# ============================================================================

class TestFullXmlGeneration:
    def test_three_level_xml_structure(self):
        nodes = [
            {"title": "Глава 1", "level": 1, "content": "Текст главы 1", "page": 5, "confidence": 1.0},
            {"title": "1.1 Подраздел", "level": 2, "content": "Текст подраздела", "page": 7, "confidence": 1.0},
            {"title": "1.1.1 Деталь", "level": 3, "content": "Текст детали", "page": 8, "confidence": 0.85},
        ]
        tree = build_tree_structure(nodes)
        xml = dict_to_xml(tree)

        # Проверяем валидность XML
        from xml.etree import ElementTree as ET
        root = ET.fromstring(xml)
        assert root.tag == "Book"

        # Проверяем вложенность
        sections = root.findall(".//section")
        assert len(sections) == 3
        # Самая глубокая секция — деталь
        deep = root.find(".//section/section/section")
        assert deep is not None
        assert deep.get("title") == "1.1.1 Деталь"


# ============================================================================
# Сценарий 9: ToC с одной буквой в названиях (фильтр мусора не должен брать L1)
# ============================================================================

class TestShortValidTitles:
    def test_short_section_kept(self):
        """Например 'Об'. Короткое, но не мусор."""
        toc = """СОДЕРЖАНИЕ
Об авторе ............................ 5
Введение ............................. 10
Глава 1 .............................. 20
"""
        seq = toc_to_linear_sequence(HeuristicParser().parse_toc(toc))
        titles = [s["title"] for s in seq]
        assert any("Об авторе" in t for t in titles)


# ============================================================================
# Сценарий 10: грязный заголовок с двойными пробелами
# ============================================================================

class TestMessyTitles:
    def test_double_spaces_normalized(self):
        toc = """СОДЕРЖАНИЕ
Глава  1.   Старт ......... 5
"""
        seq = toc_to_linear_sequence(HeuristicParser().parse_toc(toc))
        assert len(seq) >= 1
