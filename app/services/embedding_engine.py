"""
Семантический поиск секций через embeddings.

Используется как ещё один fallback для секций, которые эвристика не нашла:
нарезаем text-window на скользящие кусочки, считаем cosine-similarity
с embedding-ом title, берём позицию максимума.

В отличие от LLM-rescue (один вызов с большим окном), embeddings
эффективнее на длинных окнах: O(N) маленьких быстрых вызовов вместо
одного дорогого LLM-call с большим контекстом.
"""

import asyncio
import math
from openai import AsyncOpenAI


# Embedding-модель: при смене переменной нужно убедиться, что модель
# с таким id загружена в LM Studio (см. вкладку Local Server → Models).
#
# Рекомендуемые модели для русского текста (в порядке предпочтения):
#   * "bge-m3"                              — лучшее качество, 568M, без префиксов
#   * "multilingual-e5-large"               — классика, требует EMBED_USE_E5_PREFIX
#   * "multilingual-e5-base"                — компромисс, требует EMBED_USE_E5_PREFIX
#   * "text-embedding-nomic-embed-text-v1.5" — лёгкая, но плохо для RU
EMBEDDING_MODEL = "text-embedding-qwen3-embedding-0.6b"
EMBEDDING_BASE_URL = "http://127.0.0.1:1234/v1"
EMBEDDING_MAX_PARALLEL = 4

# Модели семейства multilingual-e5 ОБУЧЕНЫ с префиксами "query: " и "passage: ".
# Без них качество значительно хуже. Для bge-m3 и nomic префиксы не нужны.
# Если ставите e5-модель — поменяйте флаг на True.
EMBED_USE_E5_PREFIX = False

# Размер скользящего фрагмента и шага для поиска.
# 200/100 — достаточно чтобы заголовок целиком влез в одну позицию,
# и достаточно мелко чтобы найти точное место.
EMBED_WINDOW_SIZE = 200
EMBED_STRIDE = 100

# Минимальная cosine-similarity для принятия совпадения.
# Подбирается эмпирически: ниже → больше ложных, выше → больше пропусков.
# Зависит от модели — у каждой свой scale:
#   * nomic-embed-text-v1.5       → 0.65 (даёт высокие абсолютные значения)
#   * bge-m3                      → 0.55–0.65
#   * multilingual-e5-large/base  → 0.75 (после нормализации)
#   * Qwen3-Embedding-0.6b        → 0.45 (более «осторожные» оценки)
EMBED_MATCH_THRESHOLD = 0.45


def _cosine_similarity(a: list, b: list) -> float:
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


class EmbeddingEngine:
    def __init__(self):
        self.client = AsyncOpenAI(base_url=EMBEDDING_BASE_URL, api_key="lm-studio")
        self.model = EMBEDDING_MODEL
        self._semaphore = asyncio.Semaphore(EMBEDDING_MAX_PARALLEL)
        # Кеш чтобы один и тот же title не считать дважды
        self._cache: dict = {}
        self._available: bool | None = None

    async def is_available(self) -> bool:
        """Проверяет что embedding-модель доступна. Кешируется на жизнь объекта."""
        if self._available is not None:
            return self._available
        try:
            await self.embed("test")
            self._available = True
        except Exception as e:
            print(f"Embedding model unavailable: {e}")
            self._available = False
        return self._available

    async def embed(self, text: str, is_query: bool = False) -> list:
        """
        Возвращает embedding одного текста.

        is_query: для моделей семейства multilingual-e5 нужно префиксовать
        запросы (заголовки) "query: " и тексты (chunks) "passage: ".
        Без префиксов качество существенно хуже.
        """
        if not text or not text.strip():
            return []
        # Префикс — часть кэш-ключа, чтобы запрос и тот же текст как passage
        # не путались.
        cache_key = (text, is_query) if EMBED_USE_E5_PREFIX else text
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        input_text = text
        if EMBED_USE_E5_PREFIX:
            input_text = ("query: " if is_query else "passage: ") + text

        async with self._semaphore:
            resp = await self.client.embeddings.create(
                model=self.model,
                input=input_text,
            )
        emb = resp.data[0].embedding
        # Кеш ограничиваем по размеру (не более 1024 ключей)
        if len(self._cache) < 1024:
            self._cache[cache_key] = emb
        return emb

    async def embed_batch(self, texts: list, is_query: bool = False) -> list:
        """Параллельная пакетная обработка списка текстов."""
        if not texts:
            return []
        tasks = [self.embed(t, is_query=is_query) for t in texts]
        return await asyncio.gather(*tasks)

    async def locate_section(
        self,
        title: str,
        text_window: str,
        threshold: float = EMBED_MATCH_THRESHOLD,
    ) -> tuple[int, float]:
        """
        Ищет позицию title в text_window через семантический поиск.

        Возвращает (offset, score). Если ничего не найдено — (-1, 0.0).
        """
        if not await self.is_available():
            return -1, 0.0
        if len(text_window) < EMBED_WINDOW_SIZE:
            return -1, 0.0

        # Нарезаем скользящие окна
        chunks = []
        positions = []
        for i in range(0, len(text_window) - EMBED_WINDOW_SIZE + 1, EMBED_STRIDE):
            chunks.append(text_window[i: i + EMBED_WINDOW_SIZE])
            positions.append(i)

        if not chunks:
            return -1, 0.0

        try:
            # Title — это «запрос», chunks — «passages» (для e5-моделей это важно)
            title_emb = await self.embed(title, is_query=True)
            chunk_embs = await self.embed_batch(chunks, is_query=False)
        except Exception as e:
            print(f"Embedding locate error for «{title[:40]}»: {e}")
            return -1, 0.0

        best_idx, best_score = -1, -1.0
        for i, emb in enumerate(chunk_embs):
            score = _cosine_similarity(title_emb, emb)
            if score > best_score:
                best_score = score
                best_idx = i

        if best_idx < 0 or best_score < threshold:
            return -1, best_score

        return positions[best_idx], best_score


# Singleton
embedding_client = EmbeddingEngine()
