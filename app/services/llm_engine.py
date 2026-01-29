import json
import asyncio
from openai import AsyncOpenAI


class LLMEngine:
    def __init__(self):
        # Асинхронный клиент для Ollama
        self.client = AsyncOpenAI(
            base_url='http://localhost:11434/v1',
            api_key='ollama',
        )
        # Рекомендуемые модели: qwen2.5:7b (легкая) или qwen2.5:14b (средняя)
        self.model = "qwen2.5:7b"

    async def extract_toc_json(self, text_pages: str):
        prompt = f"""
Твоя роль: Парсер структуры документов.
Входные данные: Текст первых страниц книги.
Задача: Извлечь оглавление в формате JSON.

Инструкции:
1. Верни JSON объект с ключом "items".
2. "items" должен содержать список разделов.
3. Формат элемента: {{"title": "Название", "page": номер_числом, "level": уровень_вложенности}}.
4. Если оглавления нет, верни {{"items": []}}.

Текст:
---
{text_pages}
---
"""
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                response_format={"type": "json_object"}
            )
            data = json.loads(response.choices[0].message.content)
            return data.get("items", [])
        except Exception as e:
            print(f"LLM ToC Error: {e}")
            return []

    async def clean_text_fragment(self, text, is_start=False):
            if len(text.strip()) < 10: return ""

            prompt = f"""
    Твоя роль: Технический редактор.
    Задача: Восстановить связный текст из грязного PDF-экстракта.

    ИНСТРУКЦИИ:
    1. Сохрани весь смысл и все предложения.
    2. Удали номера страниц, колонтитулы (верхние/нижние заголовки страниц).
    3. Склей слова, разорванные переносом (на- пример -> например).
    4. Исправь пробелы.
    {"5. УДАЛИ заголовок раздела из самой первой строки." if is_start else ""}
    6. Верни только чистый текст, без твоих комментариев.

    ТЕКСТ:
    {text}
    """
            try:
                response = await self.client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.0,  # Максимальная точность
                )
                return response.choices[0].message.content.strip()
            except Exception as e:
                return text  # Возвращаем оригинал при ошибке

    async def process_large_text(self, full_text, is_start=True):
        # Если текст большой - режем на части
        chunk_size = 6000
        if len(full_text) <= chunk_size:
            return await self.clean_text_fragment(full_text, is_start=is_start)

        parts = []
        for i in range(0, len(full_text), chunk_size):
            chunk = full_text[i: i + chunk_size]
            cleaned = await self.clean_text_fragment(chunk, is_start=(i == 0 and is_start))
            parts.append(cleaned)

        return "\n".join(parts)


llm_client = LLMEngine()