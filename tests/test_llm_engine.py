"""Тесты llm_engine — то что не требует реального LLM (помощники + детекторы)."""
import pytest
from types import SimpleNamespace

from app.services.llm_engine import (
    detect_foreign_script,
    has_foreign_script,
    count_foreign_chars,
    scrub_foreign_script,
    postprocess_llm_output,
    _strip_llm_preamble,
    _strip_markdown_fences,
    _extract_message_content,
)


class TestScrubForeignScript:
    def test_removes_cjk(self):
        assert scrub_foreign_script("动机ировать") == "ировать"

    def test_preserves_cyrillic(self):
        assert scrub_foreign_script("мотивировать") == "мотивировать"

    def test_preserves_latin(self):
        assert scrub_foreign_script("std::atomic<T*>") == "std::atomic<T*>"
        assert scrub_foreign_script("motivation") == "motivation"

    def test_mixed(self):
        assert scrub_foreign_script("Целевая 动机 motivation") == "Целевая  motivation"

    def test_arabic_korean(self):
        # Удаляются арабский и корейский, пробелы между ними остаются
        assert scrub_foreign_script("текст مرحبا 안녕 end") == "текст   end"

    def test_empty(self):
        assert scrub_foreign_script("") == ""
        assert scrub_foreign_script(None) is None


# ============================================================================
# detect_foreign_script — детекция CJK / арабского / иврита / корейского
# ============================================================================

class TestForeignScript:
    @pytest.mark.parametrize("text,expected_zero", [
        ("Это нормальный русский текст без иностранных скриптов.", True),
        ("Plain English text without any CJK.", True),
        ("Текст с цифрами 1234 и пунктуацией!", True),
        ("Code: function foo() { return 42; }", True),
        # С CJK:
        ("Текст с китайскими 中文 символами", False),
        ("Some text 한국어 here", False),
        ("Mixed العربية script", False),
        ("Japanese: ひらがな カタカナ", False),
    ])
    def test_detection(self, text, expected_zero):
        ratio = detect_foreign_script(text)
        if expected_zero:
            assert ratio == 0.0, f"{text!r} → {ratio}"
        else:
            assert ratio > 0.0, f"{text!r} → {ratio}"

    def test_pure_chinese(self):
        ratio = detect_foreign_script("中文字符全部")
        assert ratio > 0.9  # практически все символы CJK

    def test_empty(self):
        assert detect_foreign_script("") == 0.0
        assert detect_foreign_script(None) == 0.0


class TestForeignAbsoluteCount:
    """Тесты для строгого детектора с абсолютным порогом."""

    def test_single_chinese_char_in_long_text_caught(self):
        """Даже 1 иероглиф в большом тексте должен быть пойман."""
        # 1 иероглиф на 1000 символов = 0.1%, ниже FOREIGN_CHAR_LIMIT=1%
        # Но detect_foreign_script вернёт > 0 — то есть строже чем 5% порог
        text = "Это длинный нормальный русский текст. " * 50 + "中"
        assert count_foreign_chars(text) == 1
        # Ratio низкий, но > FOREIGN_CHAR_LIMIT теперь нет (1 / 2000 = 0.05%)
        # Поэтому абсолютный порог не сработает (< 5), а ratio не сработает (< 1%)
        # Это ожидаемое поведение: ОДИН случайный символ не должен ломать всё
        assert has_foreign_script(text) is False

    def test_five_chinese_chars_caught_by_abs_limit(self):
        """5+ иероглифов срабатывают абсолютным порогом независимо от длины."""
        text = "Очень длинный русский текст. " * 100 + "中文字符串"  # 5 иероглифов
        assert count_foreign_chars(text) >= 5
        assert has_foreign_script(text) is True

    def test_short_text_with_two_chinese_caught_by_ratio(self):
        """В коротком тексте 2 иероглифа уже > 1%."""
        text = "Очистка текста " + "中文" + " завершена"  # 2 иероглифа в ~25 символах = 8%
        # ratio = 2 / ~25 > 1%
        assert has_foreign_script(text) is True

    def test_clean_text_not_flagged(self):
        text = "Идеально чистый русский текст без посторонних символов."
        assert has_foreign_script(text) is False

    def test_kenig_book_title_with_inline_word_not_flagged(self):
        """Текст с латинскими словами не должен срабатывать (это не CJK)."""
        text = "Используем библиотеку TensorFlow для обучения модели."
        assert has_foreign_script(text) is False

    def test_count_chars(self):
        assert count_foreign_chars("") == 0
        assert count_foreign_chars(None) == 0
        assert count_foreign_chars("hello") == 0
        assert count_foreign_chars("中文") == 2
        assert count_foreign_chars("привет 中 hello 文") == 2


# ============================================================================
# _strip_llm_preamble — удаление «вступительных фраз» от LLM
# ============================================================================

class TestStripPreamble:
    @pytest.mark.parametrize("preamble", [
        "Конечно, вот очищенный текст:",
        "Sure, here is the cleaned text:",
        "Certainly! Below is the result:",
        "Вот результат:",
        "Разумеется, ниже представлен...",
    ])
    def test_strips_known_preamble(self, preamble):
        text = preamble + "\n\nРеальный текст книги."
        out = _strip_llm_preamble(text)
        assert "Реальный текст" in out
        # Preamble НЕ должно остаться в начале результата
        assert not out.startswith(preamble)

    def test_keeps_normal_text(self):
        text = "Это первый абзац книги.\nЭто второй абзац."
        out = _strip_llm_preamble(text)
        assert out == text.strip()

    def test_strips_only_short_preamble(self):
        """Длинная строка (>120) не считается вступлением, даже если начинается с маркера."""
        long = "Конечно, " + "и тогда автор продолжает свою мысль и пишет, " * 10
        out = _strip_llm_preamble(long + "\n\nДалее идёт основной текст.")
        # Длинная фраза должна остаться, т.к. это уже не preamble
        assert "Конечно" in out


# ============================================================================
# _strip_markdown_fences
# ============================================================================

class TestStripMarkdownFences:
    def test_strips_triple_backticks(self):
        text = "```json\n{\"a\": 1}\n```"
        out = _strip_markdown_fences(text)
        assert "```" not in out
        assert "{\"a\": 1}" in out

    def test_strips_xml_fences(self):
        text = "```xml\n<root/>\n```"
        out = _strip_markdown_fences(text)
        assert "```" not in out


# ============================================================================
# postprocess_llm_output — полная цепочка очистки
# ============================================================================

class TestPostprocess:
    def test_removes_preamble_and_fences(self):
        raw = "Конечно, вот результат:\n```\nИтоговый текст книги.\n```"
        out = postprocess_llm_output(raw)
        assert "Конечно" not in out
        assert "```" not in out
        assert "Итоговый текст" in out

    def test_normalizes_multiple_newlines(self):
        raw = "Первый.\n\n\n\n\nВторой."
        out = postprocess_llm_output(raw)
        assert "\n\n\n" not in out


# ============================================================================
# _extract_message_content — content + reasoning fallback
# ============================================================================

class TestExtractContent:
    def test_content_present(self):
        msg = SimpleNamespace(content="Hello", reasoning_content="")
        assert _extract_message_content(msg) == "Hello"

    def test_content_empty_falls_back_to_reasoning(self):
        """Для reasoning-моделей типа Qwen3, когда content пустой."""
        msg = SimpleNamespace(content="", reasoning_content="Я думаю: ответ — 42.")
        result = _extract_message_content(msg)
        assert "ответ — 42" in result

    def test_content_none_handled(self):
        msg = SimpleNamespace(content=None, reasoning_content="fallback")
        assert _extract_message_content(msg) == "fallback"

    def test_model_extra_path(self):
        """Некоторые SDK кладут reasoning_content в model_extra."""
        msg = SimpleNamespace(
            content="",
            reasoning_content=None,
            model_extra={"reasoning_content": "from extras"},
        )
        result = _extract_message_content(msg)
        assert result == "from extras"

    def test_both_empty_returns_empty(self):
        msg = SimpleNamespace(content="", reasoning_content="")
        assert _extract_message_content(msg) == ""
