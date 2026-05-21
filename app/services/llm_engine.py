import json
import asyncio
from openai import AsyncOpenAI


class LLMEngine:
    def __init__(self):
        # LM Studio Local Server
        self.client = AsyncOpenAI(
            base_url='http://127.0.0.1:1234/v1',
            api_key='lm-studio',
        )
        # qwen3.6-35b-a3b (LM Studio)
        self.model = "qwen3.6-35b-a3b"

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
    7. Отвечай только на русском

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

    async def fix_boundary(self, title: str, chunk_start: str) -> int:
        """Просит LLM найти где заканчивается заголовок и начинается текст.
        
        Returns:
            Смещение в символах (int) или 0 если не найдено.
        """
        prompt = f"""Найди в тексте ниже, где ЗАКАНЧИВАЕТСЯ заголовок "{title}" и начинается основной текст главы.

Верни ТОЛЬКО число — количество символов от начала текста до начала основного содержания.
Если заголовок не найден, верни 0.
Не добавляй комментариев, пробелов или кавычек. Только число.

Текст:
{chunk_start}"""
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=10
            )
            result = response.choices[0].message.content.strip()
            match = __import__('re').search(r'\d+', result)
            return int(match.group()) if match else 0
        except Exception as e:
            print(f"LLM fix_boundary Error: {e}")
            return 0

    async def find_missing_chapter(self, title: str, context_text: str, expected_page=None) -> dict:
        """Ищет не найденную главу в контекстном тексте.
        
        Returns:
            {"found": bool, "start_offset": int или -1}
        """
        page_hint = f" (ожидаемая страница: {expected_page})" if expected_page else ""
        prompt = f"""Ты — парсер структуры документа.

Глава "{title}"{page_hint} не была найдена стандартным поиском.

Вот текст вокруг ожидаемой позиции этой главы:
---
{context_text[:8000]}
---

Найди в этом тексте заголовок "{title}".
Верни ТОЛЬКО JSON без комментариев:
{{"found": true/false, "start_offset": номер_символа_или_-1}}

Если не found — верни {{"found": false, "start_offset": -1}}.
"""
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=50,
                response_format={"type": "json_object"}
            )
            data = json.loads(response.choices[0].message.content)
            return {
                "found": bool(data.get("found", False)),
                "start_offset": int(data.get("start_offset", -1))
            }
        except Exception as e:
            print(f"LLM find_missing_chapter Error: {e}")
            return {"found": False, "start_offset": -1}


llm_client = LLMEngine()