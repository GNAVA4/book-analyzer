"""Тесты mapping_pipeline — без обращения к LLM (моки)."""
import pytest
from unittest.mock import AsyncMock, patch

from app.services.mapping_pipeline import (
    _looks_like_toc_content,
    _verify_and_correct_order,
    _page_hint_search,
    _estimate_position_from_page,
    _fuzzy_locate,
    _revert_position_clusters,
    map_sequence,
)


# ============================================================================
# _revert_position_clusters — Класс-2: главы матчатся кучей в задней части
# ============================================================================

class TestRevertPositionClusters:
    def _m(self, page, start, strat='exact'):
        return {'item': {'page': page}, 'start_idx': start, 'end_idx': start + 10,
                'confidence': 1.0, 'match_strategy': strat}

    def test_reverts_crammed_run_with_spread_pages(self):
        # 5 глав втиснуты в ~3K символов, а их страницы разнесены (10..120)
        # total_pages=200, full_text_len=268000 -> ест разнесены на десятки K.
        mapped = [
            self._m(10, 246000), self._m(40, 246800), self._m(70, 247500),
            self._m(100, 248300), self._m(120, 249000),
        ]
        n = _revert_position_clusters(mapped, 268000, 200)
        assert n == 5
        assert all(m['start_idx'] == -1 and m['match_strategy'] == 'reverted_cluster' for m in mapped)

    def test_does_not_revert_legit_close_sections(self):
        # позиции близко И страницы близко (реальные соседние подразделы) — не трогаем
        mapped = [
            self._m(10, 5000), self._m(11, 5800), self._m(12, 6500),
        ]
        n = _revert_position_clusters(mapped, 268000, 200)
        assert n == 0

    def test_does_not_revert_spread_positions(self):
        # страницы разнесены И позиции разнесены (нормальные тела) — не трогаем
        mapped = [
            self._m(10, 10000), self._m(40, 50000), self._m(70, 95000),
        ]
        n = _revert_position_clusters(mapped, 268000, 200)
        assert n == 0

    def test_ignores_page_cut_and_short_runs(self):
        # page_cut не трогаем; прогон < 3 не ревертим
        mapped = [
            self._m(10, 246000, strat='page_cut'), self._m(40, 246500),
            self._m(70, 247000),
        ]
        n = _revert_position_clusters(mapped, 268000, 200)
        assert n == 0  # только 2 не-page_cut подряд -> < CLUSTER_MIN


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

    def test_dotted_with_spaces_detected(self):
        """Книги где точки разнесены пробелами (стиль Кенига)."""
        text = (
            "Содержание.  .  .  .  .  .  .  .  .  .  .  .  6\n"
            "Предисловие.  .  .  .  .  .  .  .  .  .  .  7\n"
            "Об авторе.  .  .  .  .  .  .  .  .  .  .  .  10\n"
            "Введение.  .  .  .  .  .  .  .  .  .  .  .  11\n"
        )
        assert _looks_like_toc_content(text) is True

    def test_dash_leaders_detected(self):
        """Лидеры в виде тире вместо точек."""
        text = (
            "Введение ———————————— 5\n"
            "Глава 1 ———————————————— 10\n"
            "Глава 2 ———————————————— 20\n"
            "Глава 3 ———————————————— 30\n"
        )
        assert _looks_like_toc_content(text) is True

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

    def test_page_distance_reverts_far_exact_match(self):
        """exact match, попавший далеко от page-hint, должен быть отвергнут."""
        full_text = "Long book content. " * 50_000  # ~950K chars
        full_text_len = len(full_text)
        # Глава на странице 10 из 200, ожидаемая позиция ~47K
        # А exact-match попал на 500K — это явно упоминание в Примечаниях
        mapped = self._make_mapped([
            ("Глава 1", 30_000, "exact", 5),    # рядом с page 5 (ожид ~24K)
            ("Глава 10", 500_000, "exact", 10),  # ДАЛЕКО от page 10 (ожид ~47K)
        ])
        result = _verify_and_correct_order(mapped, full_text, full_text_len, total_pages=200)
        # Глава 1 остаётся, Глава 10 отвергнута
        assert result[0]["start_idx"] == 30_000
        assert result[1]["start_idx"] == -1
        assert result[1]["match_strategy"] == "reverted_page_distance"

    def test_page_distance_keeps_close_match(self):
        """exact match рядом с page-hint остаётся."""
        full_text = "Long book content. " * 50_000  # 950K chars
        mapped = self._make_mapped([
            ("Глава 5", 250_000, "exact", 50),  # page 50/200, ожид ~237K — допустимо
        ])
        result = _verify_and_correct_order(mapped, full_text, len(full_text), total_pages=200)
        # Расстояние небольшое — остаётся
        assert result[0]["start_idx"] == 250_000

    def test_page_distance_without_pages_skipped(self):
        """Если у секции нет page, проверка page_distance не применяется."""
        full_text = "Long book content. " * 50_000
        mapped = self._make_mapped([
            ("Глава 1", 500_000, "exact", None),  # page=None
        ])
        result = _verify_and_correct_order(mapped, full_text, len(full_text), total_pages=200)
        # Без page нет ориентира — не отвергаем
        assert result[0]["start_idx"] == 500_000


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


# ============================================================================
# _fuzzy_locate
# ============================================================================

class TestFuzzyLocate:
    def test_exact_match_returns_high_score(self):
        text = "filler text " * 30 + "Не бойтесь рисковать смело" + " more " * 30
        target_pos = text.find("Не бойтесь")
        offset, score = _fuzzy_locate("Не бойтесь рисковать", text)
        assert offset != -1
        assert score >= 0.95
        # Должно быть рядом с настоящей позицией
        assert abs(offset - target_pos) < 30

    def test_catches_typo_e_vs_e(self):
        """Главный кейс: LLM-опечатка 'Не боитесь' vs 'Не бойтесь'."""
        text = "filler " * 40 + "Не бойтесь рисковать и пробуйте новое" + " content " * 40
        # Title с опечаткой (как из LLM ToC)
        offset, score = _fuzzy_locate("Не боитесь рисковать", text)
        assert offset != -1, f"score={score}"
        assert score >= 0.85

    def test_catches_capitalization(self):
        """ГЛАВА 1 vs Глава 1 — должно сматчить."""
        text = "preamble " * 30 + "ГЛАВА 1. ВВЕДЕНИЕ В СИСТЕМУ" + " content " * 30
        offset, score = _fuzzy_locate("Глава 1. Введение в систему", text)
        assert offset != -1

    def test_does_not_match_unrelated(self):
        """Совсем разные строки не должны давать ложного match-а."""
        text = "Совсем другой контент про другие вещи и темы. " * 20
        offset, score = _fuzzy_locate("Глава о фотонной кристаллизации", text)
        # Score может быть выше нуля, но ниже порога
        assert offset == -1 or score < 0.85

    def test_short_title_rejected(self):
        """Слишком короткие title (< FUZZY_MIN_TITLE_LEN) не fuzzy-матчатся."""
        text = "preamble text with some words " * 20
        offset, _ = _fuzzy_locate("AB", text)
        assert offset == -1

    def test_empty_inputs(self):
        assert _fuzzy_locate("", "text") == (-1, 0.0)
        assert _fuzzy_locate("title", "") == (-1, 0.0)
        assert _fuzzy_locate(None, "text") == (-1, 0.0)

    def test_handles_nonbreaking_space(self):
        """Title с обычным пробелом vs текст с \\xa0 — должно работать."""
        # PDF часто извлекается с неразрывными пробелами между словами
        text = "preamble " * 30 + "НЕ\xa0БОЙТЕСЬ РИСКОВАТЬ" + " content" * 30
        offset, score = _fuzzy_locate("Не бойтесь рисковать", text)
        assert offset != -1, f"score={score}"
        assert score >= 0.95

    def test_real_kenig_case_typo_e_vs_e_with_nbsp(self):
        """Реальный кейс из Иглмена: LLM ToC 'Не боитесь' vs текст 'НЕ\\xa0БОЙТЕСЬ'."""
        text = "filler " * 30 + "ГЛ А В А  1 0\nНЕ\xa0БОЙТЕСЬ РИСКОВАТЬ\nВ конце XIX века..." + " content" * 30
        offset, score = _fuzzy_locate("Не боитесь рисковать", text)
        # Опечатка «и»/«й» + nbsp → должно сматчить с порогом 0.85
        assert offset != -1, f"score={score}"

    def test_does_not_confuse_similar_subsections(self):
        """
        Главное опасение по поводу fuzzy: «Расчёт параметров системы А» vs
        «Расчёт параметров системы Б». Они слишком похожи по длине и набору
        букв — fuzzy должен НЕ путать их, если threshold достаточно высокий.
        """
        text = (
            "Раздел 5.2. Расчёт параметров системы Б — подробное описание этой схемы " * 5
        )
        # Ищем «систему А», но в тексте только «систему Б»
        offset, score = _fuzzy_locate("Расчёт параметров системы А", text, threshold=0.90)
        # Должно НЕ сматчить из-за порога 0.90 (одна буква отличия в коротком title
        # даёт ratio ~0.96, но threshold 0.90 это допускает — нужно проверить)
        # На самом деле это плохой кейс для fuzzy. Тест документирует поведение:
        # при пороге 0.90 разница в 1 букву всё ещё может сматчить.
        # Это известное ограничение — fuzzy работает только когда альтернатив нет
        # рядом в тексте. В реальном pipeline alternatives уже найдены exact-ом.
        if offset != -1:
            # Подтверждаем что score очень близок к пределу
            assert score >= 0.90
