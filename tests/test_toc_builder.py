"""Тесты для toc_builder — постпроцессинг OCR-вывода перед heuristic-парсером."""

from app.services.toc_builder import (
    _split_sticky_toc_lines, _count_toc_entry_lines, _looks_incomplete,
    _dedup_and_order, _drop_fuzzy_pageless_dupes,
    _build_prefix_map, _reattach_numerical_prefixes,
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


class TestDropFuzzyPagelessDupes:
    """ВКР-кейс: одна и та же глава приходит дважды — раз из ToC с страницей,
    раз из заголовка тела без страницы, с однобуквенной OCR-разницей."""

    def test_drops_pageless_near_duplicate(self):
        items = [
            {"title": "ГЛАВА 1 ТЕОРЕТИЧЕСКИЕ ОСНОВЫ ИЗУЧЕНЯЯ ПЛАТНЫХ УСЛУГ",
             "page": 5, "level": 1},
            {"title": "ЗАКЛЮЧЕНИЕ", "page": 82, "level": 1},
            {"title": "ГЛАВА 1 ТЕОРЕТИЧЕСКИЕ ОСНОВЫ ИЗУЧЕНЯЮ ПЛАТНЫХ УСЛУГ",
             "page": None, "level": 1},
        ]
        out = _drop_fuzzy_pageless_dupes(items)
        titles = [s["title"] for s in out]
        assert any("ИЗУЧЕНЯЯ" in t for t in titles)
        assert not any("ИЗУЧЕНЯЮ" in t for t in titles)
        assert len(out) == 2

    def test_keeps_unique_pageless_entries(self):
        items = [
            {"title": "Глава 1 Введение", "page": 5, "level": 1},
            {"title": "Об авторе", "page": None, "level": 1},
        ]
        out = _drop_fuzzy_pageless_dupes(items)
        assert len(out) == 2

    def test_keeps_distinct_short_titles(self):
        """Короткие заголовки с малой длиной — высокий риск ложного срабатывания.
        Гард `longer < 8` пропускает их."""
        items = [
            {"title": "Введение", "page": 5, "level": 1},
            {"title": "Введ", "page": None, "level": 1},
        ]
        out = _drop_fuzzy_pageless_dupes(items)
        assert len(out) == 2

    def test_keeps_genuinely_different_titles(self):
        items = [
            {"title": "Глава 1 Теоретические основы статистики", "page": 5, "level": 1},
            {"title": "Глава 2 Эмпирический анализ данных", "page": None, "level": 1},
        ]
        out = _drop_fuzzy_pageless_dupes(items)
        assert len(out) == 2

    def test_pageless_only_unchanged(self):
        items = [
            {"title": "Глава 1", "page": None, "level": 1},
            {"title": "Глава 2", "page": None, "level": 1},
        ]
        assert _drop_fuzzy_pageless_dupes(items) == items

    def test_dedup_and_order_drops_vkr_trailing_dupe(self):
        """E2E через _dedup_and_order — реальный ВКР-кейс."""
        items = [
            {"title": "ВВЕДЕНИЕ", "page": 2, "level": 1},
            {"title": "ГЛАВА 1 ТЕОРЕТИЧЕСКИЕ ОСНОВЫ СТАТИСТИЧЕСКОГО ИЗУЧЕНЯЯ ОБЪЕМА ПЛАТНЫХ УСЛУГ НАСЕЛЕНИЮ",
             "page": 5, "level": 1},
            {"title": "ЗАКЛЮЧЕНИЕ", "page": 82, "level": 1},
            {"title": "ГЛАВА 1 ТЕОРЕТИЧЕСКИЕ ОСНОВЫ СТАТИСТИЧЕСКОГО ИЗУЧЕНЯЮ ОБЪЕМА ПЛАТНЫХ УСЛУГ НАСЕЛЕНИЮ",
             "page": None, "level": 1},
        ]
        out = _dedup_and_order(items)
        assert len(out) == 3
        assert out[-1]["title"] == "ЗАКЛЮЧЕНИЕ"


class TestReattachNumericalPrefixes:
    """digital-design / 978-5-7996: LLM срезает «1.2.1» из title; восстанавливаем
    префикс из той же строки сырого текста, на которой работал LLM."""

    RAW = (
        "Содержание\n"
        "1 От нуля до единицы ........................................ 2\n"
        "1.1 План игры ............................................... 3\n"
        "1.2 Искусство управления сложностью ......................... 5\n"
        "1.2.1 Абстракция ............................................ 6\n"
        "1.2.2 Конструкторская дисциплина ............................ 11\n"
        "2 Комбинационная логика ..................................... 50\n"
        "2.1 Введение ................................................ 50\n"
    )

    def test_reattaches_simple_prefix(self):
        items = [
            {"title": "План игры", "page": 3, "level": 2},
            {"title": "Абстракция", "page": 6, "level": 3},
        ]
        out = _reattach_numerical_prefixes(items, self.RAW)
        titles = [i["title"] for i in out]
        assert "1.1 План игры" in titles
        assert "1.2.1 Абстракция" in titles

    def test_does_not_double_prefix_existing(self):
        items = [{"title": "1.1 План игры", "page": 3, "level": 2}]
        out = _reattach_numerical_prefixes(items, self.RAW)
        assert out[0]["title"] == "1.1 План игры"

    def test_leaves_unrecognised_title_alone(self):
        items = [{"title": "Что-то постороннее", "page": 99, "level": 1}]
        out = _reattach_numerical_prefixes(items, self.RAW)
        assert out[0]["title"] == "Что-то постороннее"

    def test_empty_raw_text(self):
        items = [{"title": "Абстракция", "page": 6, "level": 3}]
        out = _reattach_numerical_prefixes(items, "")
        assert out[0]["title"] == "Абстракция"

    def test_handles_paragraph_notation(self):
        raw = "§ 1.4 Паразитные связи цифровых элементов   26\n"
        items = [{"title": "Паразитные связи цифровых элементов", "page": 26, "level": 2}]
        out = _reattach_numerical_prefixes(items, raw)
        assert out[0]["title"].startswith("§")
        assert "Паразитные связи" in out[0]["title"]

    def test_intro_without_prefix_stays_bare(self):
        """Введение без префикса в сыром тексте — не приклеиваем чужой."""
        raw = "Содержание\nВведение                                        1\n"
        items = [{"title": "Введение", "page": 1, "level": 1}]
        out = _reattach_numerical_prefixes(items, raw)
        assert out[0]["title"] == "Введение"

    def test_first_occurrence_wins_for_duplicate_bare(self):
        """Если bare title повторяется в сыром тексте, берём префикс с ПЕРВОГО вхождения."""
        raw = (
            "1.1 Введение ................. 5\n"
            "2.1 Введение ................. 50\n"
        )
        items = [{"title": "Введение", "page": 5, "level": 2}]
        out = _reattach_numerical_prefixes(items, raw)
        assert out[0]["title"] == "1.1 Введение"

    def test_build_prefix_map_basic(self):
        m = _build_prefix_map(self.RAW)
        assert m.get("план игры") == "1.1"
        assert m.get("абстракция") == "1.2.1"
        assert m.get("искусство управления сложностью") == "1.2"

    def test_build_prefix_map_ignores_garbage(self):
        m = _build_prefix_map("Просто проза без оглавления и без страниц вообще")
        assert m == {}
