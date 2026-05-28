"""Тесты валидации/скоринга кандидатов ToC (toc_validate) — без живых моделей."""

import pytest

from app.services.toc_validate import (
    check_formal, check_cjk, score_toc, is_valid,
    _significant_tokens, _strip_number_prefix,
)


def _seq(*titles_pages):
    return [{"title": t, "page": p, "level": lvl} for t, p, lvl in titles_pages]


class TestCheckFormal:
    def test_clean(self):
        seq = _seq(("Введение", 5, 1), ("Глава 1", 10, 1), ("1.1 Основы", 11, 2))
        assert check_formal(seq) == []

    def test_empty(self):
        assert check_formal([]) == ['empty']

    def test_empty_title(self):
        assert any('empty_title' in i for i in check_formal(_seq(("", 5, 1))))

    def test_too_long_title(self):
        long = "x" * 200
        assert any('title_too_long' in i for i in check_formal(_seq((long, 5, 1))))

    def test_bad_level(self):
        assert any('bad_level' in i for i in check_formal([{"title": "A", "page": 1, "level": 9}]))

    def test_duplicates(self):
        seq = _seq(("Глава", 1, 1), ("Глава", 2, 1), ("Глава", 3, 1), ("Глава", 4, 1))
        assert any('duplicates' in i for i in check_formal(seq))

    def test_non_monotonic_pages_one_reset_ok(self):
        # один «сброс» допустим (краткое+детальное оглавление)
        seq = _seq(("A", 10, 1), ("B", 20, 1), ("C", 5, 1), ("D", 15, 1))
        assert not any('pages_non_monotonic' in i for i in check_formal(seq))

    def test_non_monotonic_pages_two_resets_fails(self):
        seq = _seq(("A", 10, 1), ("B", 5, 1), ("C", 20, 1), ("D", 8, 1))
        assert any('pages_non_monotonic' in i for i in check_formal(seq))

    def test_too_many_vs_raw(self):
        seq = _seq(*[(f"T{i}", i, 1) for i in range(40)])
        assert any('too_many' in i for i in check_formal(seq, raw_entry_count=5))


class TestCheckCJK:
    def test_detects_cjk(self):
        seq = _seq(("动机 мотивация", 5, 1), ("Норма", 6, 1))
        cjk = check_cjk(seq)
        assert len(cjk) == 1 and "动机" in cjk[0]

    def test_clean_passes(self):
        assert check_cjk(_seq(("std::atomic", 5, 1), ("Введение", 6, 1))) == []


class TestTokenHelpers:
    def test_strip_number_prefix(self):
        assert _strip_number_prefix("3.2. Режимы отказов") == "Режимы отказов"
        assert _strip_number_prefix("Глава 5. Мифопоэтика") == "Мифопоэтика"

    def test_significant_tokens(self):
        toks = _significant_tokens("1.1. Правильный выбор цели")
        assert "правильный" in toks and "выбор" in toks
        # короткие слова и числа отброшены
        assert all(len(t) >= 4 for t in toks)


@pytest.mark.asyncio
class TestScoreToc:
    async def test_grounded_lexical(self):
        full = "В этой главе рассматриваются режимы отказов и распространение трещин в системе."
        seq = _seq(("3.2 Режимы отказов", 41, 2), ("3.3 Распространение трещин", 42, 2))
        sc = await score_toc(seq, full, embed=False)
        assert sc['grounding'] == 1.0
        assert is_valid(sc)

    async def test_ungrounded_hallucination(self):
        full = "Текст про дизайн интерфейсов и юзабилити, ничего про космос."
        seq = _seq(("Колонизация Марса", 5, 1), ("Квантовая гравитация", 6, 1),
                   ("Чёрные дыры", 7, 1), ("Тёмная материя", 8, 1))
        sc = await score_toc(seq, full, embed=False)
        assert sc['grounding'] < 0.8
        assert not is_valid(sc)

    async def test_cjk_makes_invalid(self):
        full = "мотивация и язык важны в обучении"
        seq = _seq(("动机 мотивация", 5, 1), ("язык", 6, 1))
        sc = await score_toc(seq, full, embed=False)
        assert sc['cjk_items']
        assert not is_valid(sc)

    async def test_empty(self):
        sc = await score_toc([], "любой текст", embed=False)
        assert sc['n_total'] == 0 and not is_valid(sc)
