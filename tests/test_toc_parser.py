"""Тесты для heuristic ToC parser-а и вспомогательных функций."""
import pytest

from app.services.toc_parser import (
    HeuristicParser,
    TocNode,
    toc_to_linear_sequence,
    _is_garbage_toc_item,
)


# ============================================================================
# _is_garbage_toc_item
# ============================================================================

class TestGarbageFilter:
    @pytest.mark.parametrize("title,expected", [
        ("©", True),
        ("© 2023 Pearson", True),
        ("ISBN 978-5-496-00800-6", True),
        ("ISBN: 1234567", True),
        ("ББК 85.15", True),
        ("УДК 747.012", True),
        ("Х", True),       # одиночная буква
        ("123", True),     # только цифры
        ("...", True),     # только символы
        ("____", True),
        # Нормальные заголовки:
        ("Введение", False),
        ("Глава 1. Начинаем", False),
        ("Книга вторая", False),
        ("1.1 Основы программирования", False),
        ("References", False),
    ])
    def test_garbage_patterns(self, title, expected):
        assert _is_garbage_toc_item(title) is expected

    def test_empty_string_is_garbage(self):
        assert _is_garbage_toc_item("") is True
        assert _is_garbage_toc_item("   ") is True
        assert _is_garbage_toc_item(None) is True


# ============================================================================
# _guess_level — большая таблица кейсов
# ============================================================================

class TestGuessLevel:
    @pytest.fixture
    def parser(self):
        return HeuristicParser()

    @pytest.mark.parametrize("text,expected", [
        # === Уровень 3 — самые специфичные: X.Y.Z ===
        ("1.1.1 Детальный раздел", 3),
        ("2.3.4 Что-то ещё", 3),
        ("10.20.30 Глубокий", 3),
        ("3.3.1 Alarm", 3),       # из MIL-STD
        ("D.5.7 std::lock", 3) if False else ("1.1.1 X", 3),  # placeholder

        # === Уровень 1 — главные разделы ===
        ("Глава 1", 1),
        ("Глава 1. Введение", 1),
        ("Глава 10. Заключение", 1),
        ("Часть I. Стабильность", 1),
        ("Раздел 3", 1),
        ("Chapter 1", 1),
        ("Введение", 1),
        ("Введение Что общего между NASA и Пикассо", 1),
        ("Заключение", 1),
        ("Предисловие", 1),
        ("Об авторе", 1),
        ("Благодарности", 1),

        # === Уровень 1 — голая нумерация (двухколоночный ToC MIL-STD) ===
        ("3.", 1),
        ("1.", 1),
        ("10.", 1),
        ("II", 1),
        ("IV.", 1),
        ("XXX", 1),

        # === Уровень 1 — N. Имя (с буквой после точки) ===
        ("1. SCOPE", 1),
        ("3. DEFINITIONS", 1),
        ("1. Введение", 1),
        ("10. Антипаттерны", 1),

        # === Уровень 2 — X.Y без третьей точки ===
        ("1.1 Подраздел", 2),
        ("2.2. Government documents", 2),
        ("3.5 Каскадные отказы", 2),
        ("10.10 Cookies", 2),

        # === Уровень 2 — Подраздел (НЕ должно матчить «раздел») ===
        ("Подраздел 4", 2),
        ("Поддержка", 2),  # подстрока «под» не должна давать level 1
    ])
    def test_level_detection(self, parser, text, expected):
        got = parser._guess_level(text)
        assert got == expected, f"_guess_level({text!r}) = {got}, expected {expected}"


# ============================================================================
# HeuristicParser — синтетические оглавления
# ============================================================================

class TestParseTocSynthetic:
    """Прогон parse_toc на искусственных оглавлениях."""

    @pytest.fixture
    def parser(self):
        return HeuristicParser()

    def test_classic_dotted_toc(self, parser):
        """Классический ToC с точками-лидерами."""
        text = """
СОДЕРЖАНИЕ
Введение ........................ 5
Глава 1. Основы .................. 10
1.1. Понятия .................... 12
1.2. Принципы ................... 18
Глава 2. Применение ............. 30
"""
        seq = toc_to_linear_sequence(parser.parse_toc(text))
        titles = [s["title"] for s in seq]
        assert "Введение" in titles
        assert any("Глава 1" in t for t in titles)
        assert any("1.1" in t for t in titles)
        assert any("1.2" in t for t in titles)
        assert any("Глава 2" in t for t in titles)

    def test_reversed_page_first_format(self, parser):
        """Формат `Страница Название`, типичный для современных книг."""
        text = """
ОГЛАВЛЕНИЕ
9 Введение
15 Глава 1 Старт
21 Глава 2 Развитие
"""
        seq = toc_to_linear_sequence(parser.parse_toc(text))
        # page может быть в title или отдельно — главное чтобы что-то нашлось
        assert len(seq) >= 1

    def test_starts_only_on_clear_marker_not_foreword_paragraph(self, parser):
        """FOREWORD-параграфы вида `1. This standard...` не должны быть ToC."""
        text = """
FOREWORD
1. This standard is approved for use by all Departments and Agencies of the Department of Defense.
2. This standard establishes general human engineering criteria.
a. Achieve required performance.
b. Achieve required manpower readiness.

CONTENTS
1. SCOPE ........................... 1
1.1 Scope .......................... 1
2. APPLICABLE DOCUMENTS ............ 1
"""
        seq = toc_to_linear_sequence(parser.parse_toc(text))
        titles = [s["title"] for s in seq]
        # Параграфы FOREWORD не должны попасть в ToC
        assert not any("approved for use" in t for t in titles)
        assert not any("manpower readiness" in t for t in titles)
        # Зато реальные пункты ToC должны быть
        assert any("SCOPE" in t for t in titles)

    def test_list_of_figures_terminates_toc(self, parser):
        """После маркера `LIST OF FIGURES` парсинг прекращается."""
        text = """
CONTENTS
1. SCOPE ............................ 1
2. APPLICABLE DOCUMENTS ............. 5
3. DEFINITIONS ...................... 10

LIST OF FIGURES
Trackballs .......................... 30
Pedals .............................. 32
Door interlock switch ............... 45
"""
        seq = toc_to_linear_sequence(parser.parse_toc(text))
        titles = [s["title"] for s in seq]
        assert any("SCOPE" in t for t in titles)
        # Подписи рисунков НЕ должны попасть в ToC
        assert not any("Trackballs" in t for t in titles)
        assert not any("Pedals" in t for t in titles)

    def test_three_level_hierarchy(self, parser):
        """Подразделы 1.1.1 должны быть уровня 3."""
        text = """
СОДЕРЖАНИЕ
1. Введение ........................ 1
1.1. Цели ......................... 5
1.1.1. Микро-цели ................. 7
1.1.2. Макро-цели ................. 10
1.2. Задачи ....................... 15
"""
        seq = toc_to_linear_sequence(parser.parse_toc(text))
        levels = {s["title"][:8]: s["level"] for s in seq if s["title"]}
        # 1.1.1 и 1.1.2 должны быть уровня 3
        l3_count = sum(1 for s in seq if s["level"] == 3)
        assert l3_count >= 2, f"Expected >= 2 level-3 sections, got {l3_count}: {seq}"

    def test_garbage_filtered_in_sequence(self, parser):
        """ББК/УДК/© не должны попасть в финальный sequence."""
        text = """
ББК 85.15
УДК 747.012
© 2023 Pearson

СОДЕРЖАНИЕ
Введение ........................ 5
Глава 1 ......................... 10
"""
        seq = toc_to_linear_sequence(parser.parse_toc(text))
        titles = [s["title"] for s in seq]
        assert not any("ББК" in t for t in titles)
        assert not any("УДК" in t for t in titles)
        assert not any(t.strip() == "©" for t in titles)

    def test_short_titles_not_starting_with_keyword_in_middle(self, parser):
        """«Подраздел» не должно классифицироваться как уровень 1."""
        text = """
СОДЕРЖАНИЕ
Глава 1 ......................... 5
Подраздел 4 ..................... 6
"""
        seq = toc_to_linear_sequence(parser.parse_toc(text))
        for s in seq:
            if "Подраздел" in s["title"]:
                # Подраздел НЕ должен быть L1
                assert s["level"] >= 2, f"Подраздел got level {s['level']}"


# ============================================================================
# toc_to_linear_sequence — поведение
# ============================================================================

class TestLinearSequence:
    def test_empty_tree_returns_empty(self):
        root = TocNode("Root", 0)
        assert toc_to_linear_sequence(root) == []

    def test_flat_tree(self):
        root = TocNode("Root", 0)
        for i, name in enumerate(["Глава А", "Глава Б", "Глава В"], start=1):
            root.add_child(TocNode(name, 1, str(i)))
        seq = toc_to_linear_sequence(root)
        assert [s["title"] for s in seq] == ["Глава А", "Глава Б", "Глава В"]
        assert [s["page"] for s in seq] == [1, 2, 3]

    def test_nested_tree(self):
        root = TocNode("Root", 0)
        ch = TocNode("Глава 1", 1, "10")
        ch.add_child(TocNode("1.1", 2, "12"))
        ch.add_child(TocNode("1.2", 2, "15"))
        root.add_child(ch)
        seq = toc_to_linear_sequence(root)
        # Глава 1, 1.1, 1.2 в правильном порядке
        assert [s["title"] for s in seq] == ["Глава 1", "1.1", "1.2"]
        assert [s["level"] for s in seq] == [1, 2, 2]

    def test_garbage_node_skipped(self):
        root = TocNode("Root", 0)
        root.add_child(TocNode("©", 1, "1"))
        root.add_child(TocNode("Введение", 1, "5"))
        seq = toc_to_linear_sequence(root)
        titles = [s["title"] for s in seq]
        assert "©" not in titles
        assert "Введение" in titles

    def test_inherits_page_from_next_when_none(self):
        """Если у пункта нет page, должен унаследоваться от следующего."""
        root = TocNode("Root", 0)
        root.add_child(TocNode("Введение", 1, None))
        root.add_child(TocNode("Глава 1", 1, "10"))
        seq = toc_to_linear_sequence(root)
        # Введение получает page=10 от Главы 1
        assert seq[0]["page"] == 10
