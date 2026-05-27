"""Тесты для toc_builder — постпроцессинг OCR-вывода перед heuristic-парсером."""

from app.services.toc_builder import _split_sticky_toc_lines


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
