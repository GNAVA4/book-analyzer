"""Тесты для toc_builder — постпроцессинг OCR-вывода перед heuristic-парсером."""

from app.services.toc_builder import (
    _split_sticky_toc_lines, _count_toc_entry_lines, _looks_incomplete,
)


class TestSplitStickyTocLines:
    def test_splits_glued_toc_entries(self):
        """Иглмен-кейс: OCR вернул пункты ToC одной строкой без переносов
        между ними. Должны быть разбиты по паттерну «page→Заглавная»."""
        sticky = (
            "ЧАСТЬ I НОВОЕ ПОД СОЛНЦЕМ Изменения 21 Мозг меняет 41 "
            "Трансформация 63 Синтез 100 Жизнь 114"
        )
        result = _split_sticky_toc_lines(sticky)
        lines = [l for l in result.split('\n') if l.strip()]
        # Должно получиться >= 4 строк (несколько пунктов разделены)
        assert len(lines) >= 4
        # Каждая «логическая» секция стала отдельной строкой
        assert any('Изменения' in l and 'Мозг' not in l for l in lines)
        assert any('Трансформация' in l for l in lines)

    def test_does_not_split_prose(self):
        """Обычная проза с одним числом не должна резаться."""
        prose = (
            "Был очень длинный текст и в нём упоминалось число 100 "
            "после которого Иван что-то делал и продолжалось ещё долго."
        )
        result = _split_sticky_toc_lines(prose)
        # 1 переход page→capital — не достаточно для разбиения
        assert result == prose

    def test_does_not_split_short_lines(self):
        """Короткие строки не трогаем даже при множественных переходах."""
        short = "A 10 B 20 C"  # < 60 символов
        result = _split_sticky_toc_lines(short)
        assert result == short

    def test_preserves_already_split_text(self):
        """Уже разбитый ToC не трогаем."""
        normal = (
            "Введение 5\n"
            "Глава 1 10\n"
            "Глава 2 30\n"
        )
        result = _split_sticky_toc_lines(normal)
        assert result == normal

    def test_empty_input(self):
        assert _split_sticky_toc_lines("") == ""
        assert _split_sticky_toc_lines(None) is None

    def test_latin_capitals(self):
        """Паттерн также должен ловить латинские заглавные (английские книги)."""
        sticky = (
            "Introduction 5 Chapter One 15 Chapter Two 35 "
            "Chapter Three 55 Conclusion 75"
        )
        result = _split_sticky_toc_lines(sticky)
        lines = [l for l in result.split('\n') if l.strip()]
        assert len(lines) >= 4


class TestLooksIncomplete:
    def test_counts_inline_page_entries(self):
        raw = (
            "Содержание\n"
            "Введение .................... 5\n"
            "Глава 1. Основы ............. 10\n"
            "Глава 2. Развитие ........... 30\n"
        )
        assert _count_toc_entry_lines(raw) == 3

    def test_counts_separate_line_entries(self):
        # формат Розенсона: заголовок на одной строке, страница на следующей
        raw = (
            "Глава 1. Вещь в культуре\n26\n"
            "Вещь и Дизайн\n26\n"
            "Вещь и Культура\n27\n"
        )
        assert _count_toc_entry_lines(raw) == 3

    def test_incomplete_when_raw_has_more_than_heuristic(self):
        # эвристика нашла 2 пункта, а в сыром тексте их 8 → неполнота
        raw = "\n".join(f"Подраздел {i} ............ {i*3}" for i in range(8))
        seq = [{"title": "Глава 1", "page": 1, "level": 1},
               {"title": "Глава 2", "page": 5, "level": 1}]
        assert _looks_incomplete(seq, raw) is True

    def test_complete_when_counts_match(self):
        raw = "\n".join(f"Глава {i} ............ {i*10}" for i in range(1, 6))
        seq = [{"title": f"Глава {i}", "page": i * 10, "level": 1} for i in range(1, 6)]
        assert _looks_incomplete(seq, raw) is False

    def test_empty_seq_is_incomplete(self):
        assert _looks_incomplete([], "Глава 1 .... 5") is True
