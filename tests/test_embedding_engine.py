"""Тесты embedding_engine — без реального обращения к LLM Studio (моки)."""
import pytest
from unittest.mock import patch, AsyncMock

from app.services.embedding_engine import _cosine_similarity, EmbeddingEngine


# ============================================================================
# _cosine_similarity
# ============================================================================

class TestCosine:
    def test_identical_vectors(self):
        v = [1.0, 2.0, 3.0]
        assert _cosine_similarity(v, v) == pytest.approx(1.0)

    def test_orthogonal_vectors(self):
        a, b = [1.0, 0.0], [0.0, 1.0]
        assert _cosine_similarity(a, b) == pytest.approx(0.0)

    def test_opposite_vectors(self):
        a, b = [1.0, 0.0], [-1.0, 0.0]
        assert _cosine_similarity(a, b) == pytest.approx(-1.0)

    def test_empty_vectors(self):
        assert _cosine_similarity([], []) == 0.0
        assert _cosine_similarity([1.0], []) == 0.0

    def test_zero_vector(self):
        assert _cosine_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0


# ============================================================================
# EmbeddingEngine.locate_section — с моками клиента
# ============================================================================

class TestLocateSection:
    @pytest.fixture
    def engine(self):
        e = EmbeddingEngine()
        e._available = True  # обходим is_available, чтобы не дёргать LLM Studio
        return e

    @pytest.mark.asyncio
    async def test_returns_minus_one_when_unavailable(self):
        e = EmbeddingEngine()
        e._available = False
        offset, score = await e.locate_section("Заголовок", "Текст" * 100)
        assert offset == -1
        assert score == 0.0

    @pytest.mark.asyncio
    async def test_returns_minus_one_for_short_window(self, engine):
        offset, score = await e_locate(engine, "Глава", "tiny")
        assert offset == -1

    @pytest.mark.asyncio
    async def test_finds_exact_match(self, engine):
        """Мокаем embed: фрагмент с заголовком имеет тот же embedding что и title."""
        target_title = "Тестовый заголовок"
        # Длинный текст, в котором target_title встроен в позицию ~1000
        noise = "случайный шум " * 70
        target_chunk = "далее идёт " + target_title + " и продолжение"
        text_window = noise + target_chunk + noise

        # Мок embed: возвращает «1» если text содержит target_title, иначе «0»
        async def fake_embed(text):
            return [1.0, 1.0, 1.0] if target_title in text else [0.1, 0.0, 0.0]

        with patch.object(engine, "embed", side_effect=fake_embed):
            offset, score = await engine.locate_section(target_title, text_window)

        assert offset >= 0, f"Expected to find, got offset={offset}"
        assert score >= 0.65


async def e_locate(engine, title, text):
    return await engine.locate_section(title, text)
