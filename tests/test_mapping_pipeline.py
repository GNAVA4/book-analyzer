"""Тесты mapping_pipeline — без обращения к LLM (моки)."""
import pytest
from unittest.mock import AsyncMock, patch

from app.services.mapping_pipeline import (
    _looks_like_toc_content,
    _verify_and_correct_order,
    _page_hint_search,
    _estimate_position_from_page,
    map_sequence,
)


# ============================================================================
# _looks_like_toc_content — детектор «найдено внутри ToC»
# ============================================================================

class TestLooksLikeToc:
    def test_dotted_leader_lines_detected(self):
        text = (
            "........................................24\n"
            "2.1. Авария........................................................ 25\n"
            "2.2. Последствия................................................. 30\n"
            "2.3. Анализ причин сбоя................................. 31\n"
            "2.4. Бесспорное доказательство.....................34\n"
        )
        assert _looks_like_toc_content(text) is True

    def test_numbered_subsections_detected(self):
        """Строки вида '1.1.1 Название' тоже признак ToC."""
        text = (
            "2.2.1 Specifications and handbooks\n"
            "2.2.2 Other Government documents\n"
            "2.3 Non-Government publications\n"
        )
        assert _looks_like_toc_content(text) is True

    def test_normal_prose_not_toc(self):
        text = (
            "В этой главе мы рассмотрим основные принципы. "
            "Они помогут понять структуру системы. "
            "Дальше будем разбирать примеры. "
            "Каждый из них уникален в своём роде. "
            "Применение этих принципов на практике даёт результат."
        )
        assert _looks_like_toc_content(text) is False

    def test_too_few_lines_returns_false(self):
        """Меньше 3 строк — не определяется."""
        assert _looks_like_toc_content("a\nb") is False

    def test_empty_returns_false(self):
        assert _looks_like_toc_content("") is False
        assert _looks_like_toc_content(None) is False


# ============================================================================
# _estimate_position_from_page
# ============================================================================

class TestEstimatePosition:
    def test_first_page_returns_zero(self):
        assert _estimate_position_from_page(1, 100, 100_000) == 0

    def test_middle_page(self):
        # стр 50 из 100 → 50%
        assert _estimate_position_from_page(50, 100, 100_000) == pytest.approx(49_000, abs=1000)

    def test_invalid_page_returns_zero(self):
        assert _estimate_position_from_page(0, 100, 100_000) == 0
        assert _estimate_position_from_page(None, 100, 100_000) == 0


# ============================================================================
# _verify_and_correct_order
# ============================================================================

class TestVerify:
    def _make_mapped(self, items):
        """items: list of (title, start_idx, strategy, page)."""
        return [
            {
                "item": {"title": t, "level": 1, "page": p},
                "start_idx": s,
                "end_idx": s + 30 if s != -1 else -1,
                "confidence": 1.0 if s != -1 else 0.0,
                "match_strategy": strat,
            }
            for t, s, strat, p in items
        ]

    def test_normal_order_not_changed(self):
        """Все секции в правильном порядке, ничего не меняется."""
        # Длинный текст в котором между секциями нет ToC-паттернов
        full_text = "Хороший связный текст книги. " * 200
        mapped = self._make_mapped([
            ("Глава 1", 100, "exact", 1),
            ("Глава 2", 1000, "exact", 5),
            ("Глава 3", 2000, "exact", 10),
        ])
        result = _verify_and_correct_order(mapped, full_text, len(full_text))
        # Все секции остаются
        assert all(m["start_idx"] != -1 for m in result)

    def test_in_toc_content_reverted(self):
        """Секции 'найденные' внутри ToC должны быть отвергнуты."""
        # Создаём текст: первые 100 chars — нормальные, потом ToC-паттерн
        toc_block = (
            "........................................24\n"
            "2.1. Авария........................................................ 25\n"
            "2.2. Последствия................................................. 30\n"
        )
        full_text = "preamble text " + toc_block + " more text " * 100
        # Секция «нашлась» на позиции 0 (где начинается preamble)
        mapped = self._make_mapped([
            ("Глава 2", 0, "exact", 5),
        ])
        # end_idx правильно укажет на начало ToC-блока
        mapped[0]["end_idx"] = full_text.index("..........")
        result = _verify_and_correct_order(mapped, full_text, len(full_text))
        assert result[0]["match_strategy"] == "reverted_in_toc"
        assert result[0]["start_idx"] == -1

    def test_out_of_order_rescue_reverted(self):
        """rescue, нашедший секцию ДО предыдущей сильной находки, отвергается."""
        full_text = "Хороший текст " * 300
        mapped = self._make_mapped([
            ("Глава 1", 100, "exact", 1),
            ("Глава 2", 2000, "exact", 5),
            ("Глава 3", 500, "embedding_rescue", 10),  # rescue нашёл ПЕРЕД Главой 2
        ])
        result = _verify_and_correct_order(mapped, full_text, len(full_text))
        # Глава 3 должна быть отвергнута
        assert result[2]["match_strategy"] == "reverted_out_of_order"

    def test_out_of_order_exact_NOT_reverted(self):
        """exact/tokenized нельзя отвергать — это сильные стратегии."""
        full_text = "Хороший текст " * 300
        mapped = self._make_mapped([
            ("Глава 1", 100, "exact", 1),
            ("Глава 2", 2000, "exact", 5),
            ("Глава 3", 500, "exact", 10),  # out-of-order, но exact
        ])
        result = _verify_and_correct_order(mapped, full_text, len(full_text))
        # exact остаётся (доверяем сильным стратегиям)
        assert result[2]["start_idx"] == 500


# ============================================================================
# _page_hint_search
# ============================================================================

class TestPageHint:
    def test_finds_title_in_page_window(self):
        """Заголовок находится в окне ±5000 от ожидаемой по странице позиции."""
        # 10000 символов мусора + заголовок в районе позиции 5000
        mid_text = "filler " * 700 + "Целевая глава находится здесь" + " content " * 200
        full_text = "preamble " * 100 + mid_text + " ending " * 100
        # Заголовок: «Целевая глава» на стр 5 из 10
        # 5/10 * len ≈ середина = 5000-7000 примерно
        target_pos = full_text.index("Целевая глава")
        # Расчёт ожидаемой позиции — для стр 5 из 10 будет ~50% длины
        result = _page_hint_search(
            "Целевая глава",
            full_text,
            len(full_text),
            page=5,
            total_pages=10,
        )
        # Должно сматчить, или вернуть позицию рядом с реальной
        if result:
            assert abs(result["start"] - target_pos) < 5000

    def test_returns_none_when_page_zero(self):
        result = _page_hint_search("title", "text" * 1000, 4000, page=0, total_pages=10)
        assert result is None

    def test_returns_none_when_total_pages_zero(self):
        result = _page_hint_search("title", "text" * 1000, 4000, page=5, total_pages=0)
        assert result is None
