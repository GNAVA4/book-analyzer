import re
import json
import asyncio
from openai import AsyncOpenAI


# ---------------------------------------------------------------------------
# Константы
# ---------------------------------------------------------------------------

LLM_MODEL = "qwen2.5-7b-instruct"      # non-reasoning, отвечает в content

# Параметры под 8K-context LM Studio. Эмпирически: на русском тексте
# 1 char может быть до 1.0 tokens (UTF-8 multi-byte), поэтому реальный
# бюджет на чанк: 8192 - 1500 (max_tokens) - 500 (промпт) - запас = ~5500 tokens
# = ~3500 chars русского текста в худшем случае.
# ВАЖНО: модель в LM Studio должна быть загружена с ctx >= 8192.
# Если получаете "Context size exceeded" — увеличьте ctx в LM Studio.
LLM_CHUNK_SIZE = 3_000             # максимальный размер одного чанка (символов)
LLM_BOUNDARY_CONTEXT = 1_000       # сколько символов даём LLM для уточнения границы
LLM_MAX_PARALLEL = 3               # максимум параллельных вызовов к LM Studio/Ollama
LLM_MAX_TOKENS = 1_500             # ответ; запас на reasoning-модели

# Доля чужеземных символов (CJK, арабский, иврит, корейский) — выше этого
# текст считаем галлюцинацией LLM («переводит» русский в китайский).
# Поднизили с 5% до 1% — даже один иероглиф в коротком ответе видим сразу.
FOREIGN_CHAR_LIMIT = 0.01
# Дополнительный абсолютный порог: если в ответе >= этого числа CJK-символов,
# срабатывает независимо от длины. Защищает от случая когда LLM возвращает
# огромный ответ с десятком китайских вкраплений (доля низкая, но проблема есть).
FOREIGN_CHAR_ABS_LIMIT = 5

# Регэксп для детекции чужих письменностей в русско-/латиноязычном тексте.
# CJK + хирагана/катакана + арабский + иврит + корейский.
_FOREIGN_SCRIPTS = re.compile(
    r'[぀-ゟ゠-ヿ一-鿿؀-ۿ֐-׿가-힯]'
)


def detect_foreign_script(text: str) -> float:
    """Возвращает долю символов из неевропейских письменностей."""
    if not text:
        return 0.0
    foreign = len(_FOREIGN_SCRIPTS.findall(text))
    return foreign / max(len(text), 1)


def count_foreign_chars(text: str) -> int:
    """Абсолютное число CJK/арабских символов в тексте."""
    if not text:
        return 0
    return len(_FOREIGN_SCRIPTS.findall(text))


def has_foreign_script(text: str) -> bool:
    """
    Главный детектор: возвращает True если в тексте есть подозрительная
    доля иностранных символов ИЛИ хотя бы FOREIGN_CHAR_ABS_LIMIT штук.

    Используется в clean_text_fragment для решения о retry.
    """
    if not text:
        return False
    n = count_foreign_chars(text)
    if n >= FOREIGN_CHAR_ABS_LIMIT:
        return True
    return (n / max(len(text), 1)) > FOREIGN_CHAR_LIMIT

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
            "СТРОГИЕ ПРАВИЛА:\n"
            "1. Верни ТОЛЬКО JSON-объект с ключом \"items\", без пояснений и markdown-блоков.\n"
            "2. Каждый элемент: {\"title\": \"...\", \"page\": <число>, \"level\": <1|2|3>}.\n"
            "3. ОДИН ЭЛЕМЕНТ = ОДИН РАЗДЕЛ. Запрещено склеивать несколько разделов "
            "в один title. Например ЧАСТЬ I и её главы — это РАЗНЫЕ элементы, "
            "ЧАСТЬ I = level 1, главы внутри = level 2.\n"
            "4. title должен быть КОРОТКИМ заголовком (до 100 символов), без номеров "
            "страниц внутри. Номер страницы идёт в отдельное поле \"page\".\n"
            "5. Используй язык оригинала. ЗАПРЕЩЕНО переводить заголовки на "
            "другие языки (особенно китайский, японский, корейский, арабский).\n"
            "6. Если оглавления нет — верни {\"items\": []}.\n\n"
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
    # Deep scan: LLM предлагает структуру по полному тексту
    # -----------------------------------------------------------------------

    async def propose_structure_from_text(self, full_text: str, chunk_size: int = 10_000) -> list:
        """
        Дорогой fallback: книга без ToC. Разбиваем текст на крупные chunks
        и просим LLM предложить точки разделения на главы — заголовок
        первого предложения, тема, индекс начала в chunk-е.

        Возвращает sequence в формате [{title, level, page}, ...].
        Page = None (мы не знаем номер из текста), find_real_indices
        будет искать по title через токенизацию.

        Метод медленный (минуты на большую книгу) и предназначен
        только для опционального deep-scan режима.
        """
        if not full_text:
            return []

        items: list = []
        # Берём первые ~150K символов — обычно этого хватает на оглавление крупной книги.
        # Больше — слишком медленно, и качество структурирования падает.
        text = full_text[:150_000]
        chunks = [text[i:i + chunk_size] for i in range(0, len(text), chunk_size)]

        prompt_prefix = (
            "Твоя роль: технический редактор. Перед тобой фрагмент книги без оглавления.\n"
            "Задача: найди в нём ЯВНЫЕ заголовки разделов (главы, части, разделы).\n\n"
            "Правила:\n"
            '1. Верни ТОЛЬКО JSON-объект с ключом "items".\n'
            '2. Элемент: {"title": "<заголовок как в тексте>", "level": <1|2|3>}.\n'
            "3. Не выдумывай заголовки которых нет в тексте.\n"
            '4. Если заголовков нет — верни {"items": []}.\n\n'
            "Фрагмент:\n---\n"
        )
        prompt_suffix = "\n---"

        for idx, chunk in enumerate(chunks):
            try:
                # Конкатенация вместо str.format — иначе фигурные скобки в JSON-примерах
                # интерпретируются как placeholders и падает с KeyError: 'title'
                prompt = prompt_prefix + chunk + prompt_suffix
                async with self._semaphore:
                    response = await self.client.chat.completions.create(
                        model=self.model,
                        messages=[{"role": "user", "content": prompt}],
                        temperature=0.1,
                        max_tokens=LLM_MAX_TOKENS,
                    )
                raw = _extract_message_content(response.choices[0].message)
                json_match = re.search(r'\{[\s\S]*\}', raw)
                if not json_match:
                    continue
                data = json.loads(json_match.group())
                for it in data.get("items", []):
                    title = str(it.get("title", "")).strip()
                    if title:
                        items.append({
                            "title": title,
                            "level": int(it.get("level", 1)) if it.get("level") else 1,
                            "page": None,
                        })
            except Exception as e:
                print(f"LLM deep-scan chunk {idx} error: {e}")
                continue

        # Убираем дубликаты, сохраняя первый встреченный порядок
        seen = set()
        unique: list = []
        for it in items:
            key = it["title"].lower()
            if key not in seen:
                seen.add(key)
                unique.append(it)
        return unique

    # -----------------------------------------------------------------------
    # Точечное нахождение заголовка в окне текста
    # -----------------------------------------------------------------------

    async def locate_section_in_text(self, title: str, text_window: str) -> int:
        """
        Точечный LLM-маппинг для секций, которые эвристика не нашла.

        Просит LLM найти, с какого символа в окне начинается раздел `title`,
        даже если он записан по-другому ("Глава 1" vs "Глава первая").

        Возвращает смещение от начала окна, либо -1 если не найдено.
        """
        if len(text_window.strip()) < 20:
            return -1

        prompt = (
            f"Найди в тексте ниже, где начинается раздел «{title}».\n"
            "Допустимы лёгкие отличия: разный регистр, римские/арабские цифры, "
            "сокращения, перенос строки внутри заголовка.\n\n"
            "Верни ТОЛЬКО одно целое число — позицию (в символах от начала текста), "
            "где находится первый символ найденного заголовка.\n"
            "Если раздел не найден — верни -1.\n\n"
            f"Текст:\n{text_window}"
        )
        try:
            async with self._semaphore:
                response = await self.client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.0,
                    max_tokens=64,
                )
            raw = _extract_message_content(response.choices[0].message).strip()
            numbers = re.findall(r'-?\d+', raw)
            if not numbers:
                return -1
            offset = int(numbers[-1])
            if offset < 0 or offset >= len(text_window):
                return -1
            return offset
        except Exception as e:
            print(f"LLM locate error for «{title[:40]}»: {e}")
            return -1

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
        Очищает один чанк текста от артефактов PDF/OCR.

        Защита от CJK-загрязнения (комбинированная):
          1. Если INPUT уже содержит CJK (glm-ocr иногда возвращает 动机
             вместо «мотив» по размытым местам) — используем CJK-aware
             промпт, который просит LLM восстановить кириллицу по контексту.
          2. Если ответ всё равно с CJK — retry с критическим префиксом.
          3. Если и retry с CJK — финальный scrub: regex выкидывает все CJK
             символы из текста (всё лучше, чем 动机 в content).
        """
        if len(text.strip()) < 10:
            return ""

        # Определяем исходный язык по первым символам — для строгого промпта.
        sample = text[:300]
        cyrillic = len(re.findall(r'[а-яёА-ЯЁ]', sample))
        latin = len(re.findall(r'[a-zA-Z]', sample))
        target_lang = "русский" if cyrillic > latin else "английский"

        header_instruction = (
            "5. УДАЛИ заголовок раздела из самой первой строки — "
            "оставь только основной текст.\n"
            if is_start else ""
        )

        # Если в input есть CJK — добавляем CJK-aware преамбулу, чтобы LLM
        # понимала что иероглифы это OCR-артефакт на месте кириллицы.
        input_has_cjk = has_foreign_script(text)
        cjk_preamble = (
            "ВАЖНО: входной текст пришёл от OCR-модели, которая иногда "
            "по размытым местам подставляет иероглифы (动机, 反应 и т.п.) "
            f"на месте {target_lang.upper()}-СЛОВ. Например '动机ировать' это "
            f"должно быть 'мотивировать'. Восстанови правильное "
            f"{target_lang.upper()}-написание по контексту, иероглифы убери "
            "полностью.\n\n"
            if input_has_cjk else ""
        )

        base_prompt = (
            f"{cjk_preamble}"
            "Твоя роль: технический редактор.\n"
            "Задача: восстановить связный текст из грязного PDF/OCR-экстракта.\n\n"
            "ИНСТРУКЦИИ — выполняй строго, без отступлений:\n"
            "1. Сохрани ВЕСЬ смысл и ВСЕ предложения — ничего не сокращай.\n"
            "2. Удали номера страниц и колонтитулы.\n"
            "3. Склей слова, разорванные переносом (на- пример → например).\n"
            "4. Исправь явные опечатки OCR, если контекст ясен. "
            "НЕ переписывай и НЕ перефразируй.\n"
            f"{header_instruction}"
            f"6. ЯЗЫК ОТВЕТА: ТОЛЬКО {target_lang.upper()}. "
            "ЗАПРЕЩЕНО использовать иероглифы, арабский, китайский, японский, корейский — "
            "это критическая ошибка. Не переводи текст на другие языки.\n"
            "7. Верни ТОЛЬКО очищенный текст — никаких вступлений, пояснений, "
            "фраз вроде «вот текст» или «конечно».\n\n"
            f"ТЕКСТ:\n{text}"
        )

        async def _call(prompt: str) -> str:
            async with self._semaphore:
                response = await self.client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.0,
                    max_tokens=LLM_MAX_TOKENS,
                )
            return _extract_message_content(response.choices[0].message)

        def _scrub_cjk(s: str) -> str:
            """Финальная гарантия: удаляем CJK/арабские/корейские символы
            из текста. Лучше потеря 1-2 букв в слове, чем 动机 в content."""
            if not s:
                return s
            return _FOREIGN_SCRIPTS.sub('', s)

        try:
            raw = await _call(base_prompt)
            result = postprocess_llm_output(raw)

            # Защита от языковых галлюцинаций
            if has_foreign_script(result):
                n = count_foreign_chars(result)
                print(f"[LLM clean] Foreign script: {n} chars, retrying...")
                retry_prompt = (
                    f"КРИТИЧЕСКАЯ ОШИБКА: предыдущий ответ содержал иероглифы. "
                    f"Повтори очистку текста СТРОГО на {target_lang.upper()} ЯЗЫКЕ. "
                    "Никакого китайского/арабского/японского/корейского. "
                    "Ни одного иностранного символа. "
                    "Только буквы целевого языка, цифры и стандартная пунктуация.\n\n"
                    + base_prompt
                )
                raw = await _call(retry_prompt)
                result = postprocess_llm_output(raw)

                # Если и повтор с CJK — финальный scrub
                if has_foreign_script(result):
                    n2 = count_foreign_chars(result)
                    print(f"[LLM clean] Retry also has {n2} CJK chars — scrubbing")
                    result = _scrub_cjk(result)

            return result
        except Exception as e:
            print(f"LLM clean error: {e}")
            # При полном провале LLM — возвращаем оригинал, но всё равно
            # scrub CJK из него (источник мог быть OCR с иероглифами)
            return _scrub_cjk(text) if has_foreign_script(text) else text

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
