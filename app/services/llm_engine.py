import re
import json
import asyncio
from openai import AsyncOpenAI


# ---------------------------------------------------------------------------
# Константы
# ---------------------------------------------------------------------------

LLM_MODEL = "qwen2.5-14b-instruct"     # non-reasoning, отвечает в content
LLM_CHUNK_SIZE = 6_000             # максимальный размер одного чанка (символов)
LLM_BOUNDARY_CONTEXT = 800        # сколько символов даём LLM для уточнения границы
LLM_MAX_PARALLEL = 3              # максимум параллельных вызовов к LM Studio/Ollama
LLM_MAX_TOKENS = 8_192            # запас для reasoning-моделей (Qwen3 etc.)

# Фразы-маркеры, которые LLM добавляет вопреки инструкциям.
# Строки с этих фраз в начале ответа будут обрезаны.
LLM_PREAMBLE_MARKERS = [
    "конечно", "вот", "разумеется", "certainly", "sure", "of course",
    "here is", "here's", "ниже", "предоставляю", "пожалуйста",
]


# ---------------------------------------------------------------------------
# Постпроцессинг LLM-ответов
# ---------------------------------------------------------------------------

def _strip_llm_preamble(text: str) -> str:
    """
    Удаляет вежливые вступления, которые LLM добавляет вопреки инструкциям.
    Например: «Конечно, вот очищенный текст для вашего раздела...»
    Обрезаем всё до первого абзаца, который не содержит маркеров.
    """
    lines = text.strip().splitlines()
    for i, line in enumerate(lines):
        low = line.lower().strip()
        is_preamble = any(low.startswith(marker) for marker in LLM_PREAMBLE_MARKERS)
        # Строка-маркер обычно короткая (до 120 символов) и не содержит точки в середине
        if is_preamble and len(line) < 120:
            continue
        # Первая не-маркерная строка — начало реального текста
        return '\n'.join(lines[i:]).strip()
    return text.strip()


def _strip_markdown_fences(text: str) -> str:
    """Убирает ```-блоки, которые LLM иногда оборачивает вокруг текста."""
    text = re.sub(r'^```[a-z]*\n?', '', text, flags=re.MULTILINE)
    text = re.sub(r'\n?```$', '', text, flags=re.MULTILINE)
    return text.strip()


def postprocess_llm_output(text: str) -> str:
    """Полная постобработка ответа LLM перед записью в XML."""
    text = _strip_markdown_fences(text)
    text = _strip_llm_preamble(text)
    # Нормализуем переносы
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _extract_message_content(message) -> str:
    """
    Извлекает текст из ответа LLM с поддержкой reasoning-моделей.

    Reasoning-модели (Qwen3, DeepSeek-R1, GLM-4 thinking) кладут размышления
    в `reasoning_content`, а финальный ответ — в `content`. Когда генерация
    обрывается по max_tokens, `content` пуст, и весь полезный результат
    находится в reasoning. Используем reasoning_content как fallback.
    """
    content = getattr(message, 'content', None) or ''
    if content.strip():
        return content
    # Fallback на reasoning_content (некоторые SDK возвращают через model_extra)
    reasoning = getattr(message, 'reasoning_content', None)
    if not reasoning:
        extra = getattr(message, 'model_extra', None) or {}
        reasoning = extra.get('reasoning_content', '') if isinstance(extra, dict) else ''
    return reasoning or ''


# ---------------------------------------------------------------------------
# LLM Engine
# ---------------------------------------------------------------------------

class LLMEngine:
    def __init__(self):
        self.client = AsyncOpenAI(
            base_url='http://127.0.0.1:1234/v1',
            api_key='lm-studio',
        )
        self.model = LLM_MODEL
        # Семафор ограничивает число параллельных запросов к Ollama
        self._semaphore = asyncio.Semaphore(LLM_MAX_PARALLEL)

    # -----------------------------------------------------------------------
    # Извлечение ToC
    # -----------------------------------------------------------------------

    async def extract_toc_json(self, text_pages: str) -> list:
        """
        Просит LLM извлечь оглавление из первых страниц документа.
        Возвращает список словарей {title, page, level}.
        """
        prompt = (
            "Твоя роль: парсер структуры документов.\n"
            "Входные данные: текст первых страниц книги.\n"
            "Задача: извлечь оглавление в формате JSON.\n\n"
            "Правила:\n"
            "1. Верни ТОЛЬКО JSON-объект с ключом \"items\", без пояснений.\n"
            "2. Каждый элемент: {\"title\": \"...\", \"page\": <число>, \"level\": <1|2|3>}.\n"
            "3. Если оглавления нет — верни {\"items\": []}.\n\n"
            f"Текст:\n---\n{text_pages}\n---"
        )
        try:
            async with self._semaphore:
                response = await self.client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.1,
                    max_tokens=LLM_MAX_TOKENS,
                )
            raw = _extract_message_content(response.choices[0].message)
            # Извлекаем JSON-блок из произвольного текста (LM Studio не поддерживает
            # response_format=json_object; reasoning-модели тоже могут обернуть в текст).
            json_match = re.search(r'\{[\s\S]*\}', raw)
            data = json.loads(json_match.group() if json_match else _strip_markdown_fences(raw))
            return data.get("items", [])
        except Exception as e:
            print(f"LLM ToC Error: {e}")
            return []

    # -----------------------------------------------------------------------
    # Уточнение границы главы
    # -----------------------------------------------------------------------

    async def fix_chapter_boundary(self, title: str, chunk_start: str) -> int:
        """
        Для чанков с низким confidence просит LLM найти точное начало
        основного текста главы (после заголовка).

        Возвращает смещение в символах от начала chunk_start,
        с которого начинается основной текст. 0 если не определено.
        """
        prompt = (
            f"Найди в тексте ниже, где ЗАКАНЧИВАЕТСЯ заголовок «{title}» "
            "и начинается основной текст главы.\n"
            "Верни ТОЛЬКО одно целое число — количество символов от начала текста "
            "до первого символа основного содержания.\n"
            "Если заголовок не найден — верни 0.\n\n"
            f"Текст:\n{chunk_start}"
        )
        try:
            async with self._semaphore:
                response = await self.client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.0,
                    max_tokens=LLM_MAX_TOKENS,
                )
            raw = _extract_message_content(response.choices[0].message).strip()
            # Берём ПОСЛЕДНЕЕ число в ответе — reasoning-модели часто
                # упоминают промежуточные числа в размышлениях.
            numbers = re.findall(r'\d+', raw)
            return int(numbers[-1]) if numbers else 0
        except Exception:
            return 0

    # -----------------------------------------------------------------------
    # Очистка текстового чанка
    # -----------------------------------------------------------------------

    async def clean_text_fragment(self, text: str, is_start: bool = False) -> str:
        """
        Очищает один чанк текста от артефактов PDF-конвертации.
        is_start=True: дополнительно просим удалить заголовок из первой строки.
        """
        if len(text.strip()) < 10:
            return ""

        header_instruction = (
            "5. УДАЛИ заголовок раздела из самой первой строки — "
            "оставь только основной текст.\n"
            if is_start else ""
        )

        prompt = (
            "Твоя роль: технический редактор.\n"
            "Задача: восстановить связный текст из грязного PDF-экстракта.\n\n"
            "ИНСТРУКЦИИ — выполняй строго, без отступлений:\n"
            "1. Сохрани ВЕСЬ смысл и ВСЕ предложения — ничего не сокращай.\n"
            "2. Удали номера страниц и колонтитулы.\n"
            "3. Склей слова, разорванные переносом (на- пример → например).\n"
            "4. Исправь лишние пробелы и пустые строки.\n"
            f"{header_instruction}"
            "6. Верни ТОЛЬКО очищенный текст — никаких вступлений, пояснений, "
            "фраз вроде «вот текст» или «конечно».\n\n"
            f"ТЕКСТ:\n{text}"
        )
        try:
            async with self._semaphore:
                response = await self.client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.0,
                    max_tokens=LLM_MAX_TOKENS,
                )
            raw = _extract_message_content(response.choices[0].message)
            return postprocess_llm_output(raw)
        except Exception as e:
            print(f"LLM clean error: {e}")
            return text  # возвращаем оригинал при ошибке

    # -----------------------------------------------------------------------
    # Обработка большого текста (несколько чанков параллельно)
    # -----------------------------------------------------------------------

    async def process_large_text(self, full_text: str, is_start: bool = True) -> str:
        """
        Разбивает большой текст на чанки и обрабатывает их параллельно
        (с ограничением через семафор LLM_MAX_PARALLEL).
        """
        if len(full_text) <= LLM_CHUNK_SIZE:
            return await self.clean_text_fragment(full_text, is_start=is_start)

        # Нарезаем на чанки
        chunks = []
        for i in range(0, len(full_text), LLM_CHUNK_SIZE):
            chunks.append((i, full_text[i: i + LLM_CHUNK_SIZE]))

        # Запускаем параллельно — семафор внутри clean_text_fragment
        # не даст перегрузить Ollama
        tasks = [
            self.clean_text_fragment(chunk, is_start=(idx == 0 and is_start))
            for idx, chunk in chunks
        ]
        results = await asyncio.gather(*tasks)
        return "\n".join(results)


# Singleton — импортируется в парсерах
llm_client = LLMEngine()
