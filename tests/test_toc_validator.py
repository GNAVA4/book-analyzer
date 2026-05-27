"""Тесты для toc_validator — чистая логика без HTTP/OCR-моков."""

from app.services.toc_validator import (
    _significant_words,
    _normalize,
    TOC_TITLE_WORDS_TO_CHECK,
    TOC_MIN_WORD_LEN,
)


class TestSignificantWords:
    def test_strips_chapter_prefix(self):
        words = _significant_words("Глава 1. Введение в дизайн")
        # Не должно быть "глава" или "1" — они префиксы
        assert 'глава' not in words
        assert '1' not in words
        assert 'введение' in words

    def test_strips_part_prefix(self):
        words = _significant_words("Часть I. Дизайн как творчество")
        assert 'часть' not in words
        assert 'дизайн' in words

    def test_strips_numeric_prefix(self):
        words = _significant_words("2.2.1. Исследуемые интерфейсы")
        assert '2.2.1' not in words and '2' not in words
        assert 'исследуемые' in words
        assert 'интерфейсы' in words

    def test_filters_short_words(self):
        words = _significant_words("в и а тест дизайн")
        for w in words:
            assert len(w) >= TOC_MIN_WORD_LEN

    def test_limits_word_count(self):
        long_title = "слово раз два три четыре пять шесть семь восемь"
        words = _significant_words(long_title)
        assert len(words) <= TOC_TITLE_WORDS_TO_CHECK

    def test_empty(self):
        assert _significant_words("") == []
        assert _significant_words(None) == []


class TestNormalize:
    def test_collapses_whitespace(self):
        assert _normalize("a   b\n\tc") == "a b c"

    def test_lowercase(self):
        assert _normalize("HELLO World") == "hello world"

    def test_strips(self):
        assert _normalize("  text  ") == "text"

    def test_empty(self):
        assert _normalize("") == ""
        assert _normalize(None) == ""
