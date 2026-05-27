"""Тесты для pdf_utils — функции без зависимостей от внешних сервисов."""
import pytest
from unittest.mock import MagicMock

from app.services.pdf_utils import (
    detect_garbage_text,
    check_document_readability,
    _is_terminal_section,
    find_toc_boundary,
    find_real_indices,
    fast_clean_chunk,
    clean_footer_header,
    get_clean_title,
    _search_with_confidence,
)


# ============================================================================
# get_clean_title
# ============================================================================

class TestCleanTitle:
    @pytest.mark.parametrize("raw,expected_contains", [
        ("Глава 1. Введение", "Введение"),
        ("Chapter 5 Application", "Application"),
        ("Часть III Title", "Title"),
        ("§ 2.1 Раздел", "Раздел"),
        ("I. Roman Heading", "Roman Heading"),
        ("1.1 Подраздел", "Подраздел"),
    ])
    def test_strips_prefixes(self, raw, expected_contains):
        clean = get_clean_title(raw)
        assert expected_contains in clean, f"{raw!r} → {clean!r}"

    def test_empty(self):
        assert get_clean_title("") == ""
        assert get_clean_title(None) == ""

    def test_strips_trailing_dots(self):
        # get_clean_title удаляет ключевые слова (Введение, Глава…) с начала,
        # поэтому проверяем стрип точек на нейтральном заголовке.
        out = get_clean_title("Прозрачность.......")
        assert out.endswith("ть") and not out.endswith(".")


# ============================================================================
# detect_garbage_text — главный детектор кракозябр
# ============================================================================

class TestDetectGarbage:
    def test_normal_russian_text_not_garbage(self):
        text = (
            "Это нормальный русский текст с обычными словами. "
            "Он содержит несколько предложений, и они хорошо читаются. "
            "Дополнительный абзац для теста."
        ) * 4
        r = detect_garbage_text(text)
        assert r["is_garbage"] is False, r

    def test_normal_english_text_not_garbage(self):
        text = (
            "This is normal English text with regular sentences. "
            "It contains multiple paragraphs that are easy to read. "
            "Additional content for testing purposes."
        ) * 4
        r = detect_garbage_text(text)
        assert r["is_garbage"] is False, r

    def test_short_text_not_analyzed(self):
        """Слишком короткий текст не должен анализироваться."""
        r = detect_garbage_text("short")
        assert r["is_garbage"] is False
        assert "too_short" in r["reason"]

    def test_broken_unicode_words_detected(self):
        """Слова с цифрами/символами внутри букв (битая кодировка)."""
        text = (
            "1(апиталовложения персонала " * 10 +
            "€ти:иулирование 1!1отивация " * 10 +
            "обыч1ные сло2ва " * 10
        )
        r = detect_garbage_text(text)
        assert r["is_garbage"] is True
        assert r["broken_word_ratio"] > 0.2

    def test_only_special_chars_garbage(self):
        text = "|||{{{}}}€€€$$\\\\}{12!@1!@1!@1!@1!@1!" * 10
        r = detect_garbage_text(text)
        assert r["is_garbage"] is True

    def test_very_short_words_garbage(self):
        """Тексты из коротких 1-2-символьных токенов = битая разметка."""
        text = "т о б ы л х о р о ш о н о т у т н е " * 30
        r = detect_garbage_text(text)
        # short words доминируют + real_words мало
        assert r["is_garbage"] is True

    def test_mixed_languages_normal(self):
        """Текст с латиницей и кириллицей — нормальный."""
        text = (
            "В этой главе мы рассмотрим библиотеку TensorFlow и подходы Deep Learning. "
            "Используем функцию train_test_split из модуля sklearn для подготовки данных."
        ) * 5
        r = detect_garbage_text(text)
        assert r["is_garbage"] is False

    def test_returns_all_metrics(self):
        text = "Нормальный текст " * 50
        r = detect_garbage_text(text)
        assert "garbage_ratio" in r
        assert "broken_word_ratio" in r
        assert "real_word_ratio" in r
        assert "reason" in r


# ============================================================================
# check_document_readability — на mock-документе
# ============================================================================

class TestReadability:
    def _make_doc(self, page_texts: list):
        """Создаёт mock PDF-документ."""
        doc = MagicMock()
        doc.__len__.return_value = len(page_texts)
        pages = []
        for txt in page_texts:
            p = MagicMock()
            p.get_text.return_value = txt
            pages.append(p)
        doc.__getitem__.side_effect = lambda i: pages[i]
        return doc

    def test_readable_document(self):
        clean_page = "Нормальный текст книги " * 30
        doc = self._make_doc([clean_page] * 15)
        r = check_document_readability(doc)
        assert r["is_readable"] is True
        assert r["recommendation"] == "algorithm"

    def test_unreadable_document(self):
        broken_page = "1(апит 2(оловой 3!асти " * 30
        doc = self._make_doc([broken_page] * 15)
        r = check_document_readability(doc)
        assert r["is_readable"] is False
        assert r["recommendation"] == "ocr_required"

    def test_mixed_doc_majority_clean(self):
        """Если большинство страниц чистые, документ читаем."""
        clean = "Нормальный русский текст " * 30
        broken = "1(апит 2!олова " * 30
        doc = self._make_doc([clean] * 8 + [broken] * 2)
        r = check_document_readability(doc)
        assert r["is_readable"] is True

    def test_empty_document(self):
        doc = MagicMock()
        doc.__len__.return_value = 0
        r = check_document_readability(doc)
        assert r["is_readable"] is False
        assert r["detail"] == "empty_document"

    def test_all_pages_empty_text(self):
        """Все страницы дают пустой текст (бывает у защищённых PDF)."""
        doc = self._make_doc(["", "  ", ""])
        r = check_document_readability(doc)
        assert r["is_readable"] is False


# ============================================================================
# _is_terminal_section
# ============================================================================

class TestTerminalSection:
    @pytest.mark.parametrize("title,expected", [
        ("Приложения", True),
        ("Приложение А", True),
        ("Алфавитный указатель", True),
        ("Указатель", True),
        ("Список литературы", True),
        ("Список источников", True),
        ("Примечания", True),
        ("Библиография", True),
        ("Appendix A", True),
        ("Appendix B: Glossary", True),
        ("Index", True),
        ("References", True),
        ("Notes", True),
        # Не терминальные:
        ("Введение", False),
        ("Глава 1", False),
        ("Заключение", False),
        ("Об авторе", False),
    ])
    def test_terminal_markers(self, title, expected):
        assert _is_terminal_section(title) is expected


# ============================================================================
# fast_clean_chunk
# ============================================================================

class TestFastCleanChunk:
    def test_glues_hyphenated_words(self):
        text = "Это про-\nстое предло-\nжение"
        out = fast_clean_chunk(text)
        assert "простое" in out
        assert "предложение" in out

    def test_removes_standalone_page_numbers(self):
        text = "Содержательный абзац.\n23\nСледующий абзац."
        out = fast_clean_chunk(text)
        assert "23" not in out.split("\n")  # отдельная строка с числом удалена
        assert "Содержательный" in out

    def test_normalizes_multiple_newlines(self):
        text = "Первый.\n\n\n\n\nВторой."
        out = fast_clean_chunk(text)
        assert "\n\n\n" not in out

    def test_preserves_inline_numbers(self):
        """1984 в середине строки не должен удаляться."""
        text = "Книга вышла в 1984 году в Англии."
        out = fast_clean_chunk(text)
        assert "1984" in out


# ============================================================================
# clean_footer_header
# ============================================================================

class TestFooterHeader:
    def test_removes_repeated_headers(self):
        """Строка, повторяющаяся 5+ раз, удаляется как колонтитул."""
        repeated = "Книга 'Анализ' — глава 1"  # длиннее 15 символов
        lines = [repeated] * 5 + ["Реальный контент"] + [repeated] * 5
        # Дополним до 60 строк
        lines += ["Текст контента " + str(i) for i in range(60)]
        text = "\n".join(lines)
        out = clean_footer_header(text)
        # Повторяющаяся строка удалена
        assert repeated not in out
        # Контент сохранён
        assert "Реальный контент" in out

    def test_short_text_returned_as_is(self):
        text = "Короткий\nтекст\nкниги"
        assert clean_footer_header(text) == text


# ============================================================================
# find_real_indices — маппинг с confidence
# ============================================================================

class TestFindRealIndices:
    """
    Сложные тесты find_real_indices.
    Важно: find_toc_boundary использует FALLBACK_OFFSET=3000, если ToC не нашёлся
    в первых 20% текста. Поэтому реалистичные тесты делаем с длинным текстом,
    где основной контент находится ПОСЛЕ позиции 3000.
    """

    def _make_realistic(self, toc_block: str, content_block: str) -> str:
        """ToC + дополнение до 3000 символов + основной контент."""
        padding = "\nдополнительный филлер " * 200
        return toc_block + padding + "\n=== ОСНОВНОЙ ТЕКСТ ===\n" + content_block

    def test_exact_match(self):
        toc = "СОДЕРЖАНИЕ\nГлава 1. Начинаем ...... 5\nГлава 2. Продолжение ...... 10"
        content = (
            "Глава 1. Начинаем\nТекст первой главы. " * 5 +
            "Глава 2. Продолжение\nТекст второй главы."
        )
        text = self._make_realistic(toc, content)
        seq = [
            {"title": "Глава 1. Начинаем", "level": 1, "page": 5},
            {"title": "Глава 2. Продолжение", "level": 1, "page": 10},
        ]
        mapped = find_real_indices(text, seq)
        assert mapped[0]["match_strategy"] == "exact", mapped[0]
        assert mapped[1]["match_strategy"] == "exact"
        assert mapped[0]["confidence"] == 1.0

    def test_tokenized_match_for_split_title(self):
        """Заголовок разорван пробелами в тексте."""
        toc = "СОДЕРЖАНИЕ\nГлава 1 Начинаем рассказ ...... 5"
        content = "Глава  1.   Начинаем рассказ" + " контент " * 50
        text = self._make_realistic(toc, content)
        seq = [{"title": "Глава 1 Начинаем рассказ", "level": 1, "page": 5}]
        mapped = find_real_indices(text, seq)
        assert mapped[0]["start_idx"] != -1, mapped[0]
        assert mapped[0]["confidence"] >= 0.85

    def test_not_found_returns_minus_one(self):
        text = "Это совсем другой текст без нужных заголовков. " * 200
        seq = [{"title": "Несуществующая глава", "level": 1, "page": 1}]
        mapped = find_real_indices(text, seq)
        assert mapped[0]["start_idx"] == -1
        assert mapped[0]["confidence"] == 0.0
        assert mapped[0]["match_strategy"] == "not_found"

    def test_sequential_lock(self):
        """Глава N ищется ПОСЛЕ Главы N-1."""
        toc = "СОДЕРЖАНИЕ\nГлава 1. Старт ...... 5\nГлава 2. Развитие ...... 10"
        content = (
            "Глава 1. Старт\nКонтент главы 1. " * 5 +
            "Глава 2. Развитие\nКонтент главы 2. " * 5 +
            # Упоминание Главы 1 ПОЗЖЕ — sequential lock должен это игнорировать
            "Ссылка на Глава 1. Старт упоминается в Глоссарии."
        )
        text = self._make_realistic(toc, content)
        seq = [
            {"title": "Глава 1. Старт", "level": 1, "page": 5},
            {"title": "Глава 2. Развитие", "level": 1, "page": 10},
        ]
        mapped = find_real_indices(text, seq)
        # Глава 1 должна найтись ПЕРВОЙ, не в Глоссарии
        assert mapped[0]["start_idx"] != -1
        assert mapped[1]["start_idx"] != -1
        assert mapped[0]["start_idx"] < mapped[1]["start_idx"]

    def test_terminal_section_disables_fallback(self):
        """После Приложений поиск 'Списка' не должен прыгнуть к ToC."""
        toc = (
            "СОДЕРЖАНИЕ\n"
            "Глава 1. Главное ...... 5\n"
            "Глава 2. Дополнительное ...... 10\n"
            "Приложения ...... 20\n"
            "Список литературы ...... 25\n"
        )
        content = (
            "Глава 1. Главное\nТекст главы 1.\n" + " дополнительно " * 20 +
            "Глава 2. Дополнительное\nТекст главы 2.\n" + " дополнительно " * 20 +
            "Приложения\nКод приложения.\n" + " дополнительно " * 10 +
            "Список литературы\n[1] Книга 1\n[2] Книга 2"
        )
        text = self._make_realistic(toc, content)
        seq = [
            {"title": "Глава 1. Главное", "level": 1, "page": 5},
            {"title": "Глава 2. Дополнительное", "level": 1, "page": 10},
            {"title": "Приложения", "level": 1, "page": 20},
            {"title": "Список литературы", "level": 1, "page": 25},
        ]
        mapped = find_real_indices(text, seq)
        # Список литературы должен быть ПОСЛЕ Приложений
        idx_app = next(m["start_idx"] for m in mapped if "Приложения" in m["item"]["title"])
        idx_list = next(m["start_idx"] for m in mapped if "Список" in m["item"]["title"])
        assert idx_app != -1 and idx_list != -1
        assert idx_list >= idx_app


# ============================================================================
# find_toc_boundary
# ============================================================================

class TestTocBoundary:
    def test_returns_position_of_last_toc_item(self):
        # Текст: ToC сверху, потом длинный реальный текст. Boundary должна
        # вернуть позицию первого упоминания последнего пункта ToC.
        toc = "СОДЕРЖАНИЕ\nВведение......5\nГлава 1......10\nПрозрачность......100\n"
        content = "Длинный реальный текст. " * 1000 + "Прозрачность важна в системе."
        text = toc + content
        seq = [
            {"title": "Введение", "level": 1, "page": 5},
            {"title": "Глава 1", "level": 1, "page": 10},
            {"title": "Прозрачность", "level": 1, "page": 100},
        ]
        boundary = find_toc_boundary(text, seq)
        # Должна быть позиция первого «Прозрачность» (в ToC), до основного текста
        assert boundary > 0
        # boundary должна быть в начале текста, не в конце
        first_occurrence = text.find("Прозрачность")
        assert boundary <= first_occurrence + 50

    def test_fallback_for_empty_sequence(self):
        text = "Текст без оглавления"
        assert find_toc_boundary(text, []) == 0


# ============================================================================
# _search_with_confidence — exact_normalized promotion (Массель кейс)
# ============================================================================

class TestSearchConfidencePromotion:
    def test_whitespace_only_diff_promotes_to_exact(self):
        """Совпадение через tokenized_regex, отличающееся от title только
        пробелами/переносами, должно вернуть conf=1.0 + exact_normalized."""
        text = "preface 2.2.  Обучаемость\n  пользователя content"
        title = "2.2. Обучаемость пользователя"
        clean = "Обучаемость пользователя"
        res = _search_with_confidence(text, title, clean, 0, 0)
        assert res is not None
        assert res['confidence'] == 1.00
        assert res['strategy'] == 'exact_normalized'

    def test_real_diff_stays_tokenized(self):
        """Если между токенами в тексте есть лишние знаки пунктуации —
        это не чистый whitespace, confidence остаётся 0.85."""
        # title: «2.2 Обучаемость пользователя»
        # в тексте: «2.2: Обучаемость, пользователя» — двоеточие и запятая
        # допускаются в `[\s\W]*?` между токенами, но после нормализации
        # пробелов всё равно отличаются от title.
        text = "preface 2.2: Обучаемость, пользователя suffix"
        title = "2.2 Обучаемость пользователя"
        clean = "Обучаемость пользователя"
        res = _search_with_confidence(text, title, clean, 0, 0)
        assert res is not None
        assert res['strategy'] == 'tokenized_regex'
        assert res['confidence'] == 0.85

    def test_exact_match_still_exact(self):
        """Чистый exact-матч остаётся стратегией exact, без понижения."""
        text = "preface 2.2. Обучаемость пользователя content"
        title = "2.2. Обучаемость пользователя"
        clean = "Обучаемость пользователя"
        res = _search_with_confidence(text, title, clean, 0, 0)
        assert res is not None
        assert res['confidence'] == 1.00
        assert res['strategy'] == 'exact'
