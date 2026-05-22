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


EMBEDDING_MODEL = "text-embedding-nomic-embed-text-v1.5"
EMBEDDING_BASE_URL = "http://127.0.0.1:1234/v1"
EMBEDDING_MAX_PARALLEL = 4

# Размер скользящего фрагмента и шага для поиска.
# 200/100 — достаточно чтобы заголовок целиком влез в одну позицию,
# и достаточно мелко чтобы найти точное место.
EMBED_WINDOW_SIZE = 200
EMBED_STRIDE = 100

# Минимальная cosine-similarity для принятия совпадения.
# Подбирается эмпирически: ниже → больше ложных, выше → больше пропусков.
EMBED_MATCH_THRESHOLD = 0.65


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

    async def embed(self, text: str) -> list:
        """Возвращает embedding одного текста."""
        if not text or not text.strip():
            return []
        cached = self._cache.get(text)
        if cached is not None:
            return cached
        async with self._semaphore:
            resp = await self.client.embeddings.create(
                model=self.model,
                input=text,
            )
        emb = resp.data[0].embedding
        # Кеш ограничиваем по размеру (не более 1024 ключей)
        if len(self._cache) < 1024:
            self._cache[text] = emb
        return emb

    async def embed_batch(self, texts: list) -> list:
        """Параллельная пакетная обработка списка текстов."""
        if not texts:
            return []
        tasks = [self.embed(t) for t in texts]
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
            title_emb = await self.embed(title)
            chunk_embs = await self.embed_batch(chunks)
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
