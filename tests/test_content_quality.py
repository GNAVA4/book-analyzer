"""Tests for content_quality: detect junk section bodies (dots, bullets,
ToC fragments, whitespace)."""

from app.services.content_quality import (
    classify_content,
    count_junk_sections,
)


class TestClassifyContent:
    def test_real_prose_returns_none(self):
        text = (
            "В этой главе рассматриваются основные методы статистического анализа. "
            "Особое внимание уделяется построению таблиц распределения и оценке "
            "доверительных интервалов для случайных величин."
        )
        assert classify_content(text) is None

    def test_empty_returns_none(self):
        assert classify_content("") is None
        assert classify_content(None) is None

    def test_whitespace_only(self):
        assert classify_content("   \n  \t ") == 'whitespace'

    def test_short_text_is_whitespace(self):
        """После strip <20 значимых символов = whitespace."""
        assert classify_content("a b c") == 'whitespace'

    def test_leader_dots_dominated(self):
        """Розенсон/Кениг кейс: контент = leader-точки и больше ничего."""
        text = "..................................................................."
        assert classify_content(text) == 'dots'

    def test_dots_with_some_text_still_dots(self):
        """Розенсон: «текст ........ 24» — точки доминируют по доле."""
        text = "Что такое дизайн?......................................24"
        assert classify_content(text) == 'dots'

    def test_real_prose_with_some_dots_not_flagged(self):
        """В прозе бывают точки в конце предложений — но их доля мала."""
        text = (
            "Первое предложение содержит точку. Второе предложение тоже заканчивается "
            "точкой. Точки в конце предложений — это норма."
        )
        assert classify_content(text) is None

    def test_bullet_fragment(self):
        """Контент = просто буллет «•» (subsection-into-toc Кениг case)."""
        assert classify_content("•") == 'bullet'
        assert classify_content("  •  ") == 'bullet'
        assert classify_content("·") == 'bullet'

    def test_bullet_short_with_text(self):
        assert classify_content("• Foo") == 'bullet'

    def test_long_text_starting_with_bullet_is_not_bullet_fragment(self):
        """Длинный контент, даже если начинается с буллета — это уже не bullet fragment."""
        text = (
            "• Первый принцип системы заключается в том, что данные должны "
            "проверяться при каждом обновлении и сохраняться в базе только после успеха."
        )
        assert classify_content(text) != 'bullet'

    def test_toc_fragment(self):
        """ВКР/ВВЕДЕНИЕ кейс: контент = серия строк ToC."""
        text = (
            "ации структурных сдвигов в стране.....24\n"
            "2.2 Статистическое изучение динамики объема платных услуг.....26\n"
            "2.3 Оценка дифференциации регионов страны.....34\n"
            "ГЛАВА 3 МНОГОМЕРНЫЙ АНАЛИЗ.....41\n"
        )
        assert classify_content(text) == 'toc_fragment'

    def test_prose_with_one_toc_like_line_not_flagged(self):
        """Случайная одна ToC-подобная строка в основном тексте не должна флагать."""
        text = (
            "Введение. В этой работе мы рассмотрим вопросы статистики. "
            "Содержит ссылки на: Таблица 1.....24 — это единственная такая строка. "
            "Дальше идёт обычная проза без подобных конструкций. "
            "Подробно рассматривается метод линейной регрессии."
        )
        assert classify_content(text) is None


class TestCountJunkSections:
    def test_count_breakdown(self):
        nodes = [
            {'content': '...........................'},  # dots
            {'content': '•'},  # bullet
            {'content': 'Глава X.....5\nГлава Y.....10\nГлава Z.....20'},  # toc_fragment
            {'content': 'a b c'},  # whitespace
            {'content': 'Нормальный текст главы достаточной длины для прозы. ' * 5},  # OK
            {'content': ''},  # not junk, just empty
            {'content': None},  # not junk, just missing
        ]
        out = count_junk_sections(nodes)
        assert out['total'] == 4
        assert out['dots'] == 1
        assert out['bullet'] == 1
        assert out['toc_fragment'] == 1
        assert out['whitespace'] == 1

    def test_empty_list(self):
        assert count_junk_sections([])['total'] == 0
