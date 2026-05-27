"""
OCR fallback через локальную vision-модель (glm-ocr в LM Studio).

Используется когда `detect_garbage_text` пометил PDF как нечитаемый —
типично для книг с битой кодировкой шрифтов, отсканированных в PDF,
или нестандартными OCR-эмбедами.

Рендерим страницы в PNG через PyMuPDF, отправляем в vision-LLM,
склеиваем распознанный текст и возвращаем как обычный full_text.
"""

import asyncio
import base64
import io
import sys
from openai import AsyncOpenAI


def _safe_print(*args, **kwargs):
    """print(), безопасный к unicode-крашам Windows cp1251.

    glm-ocr может возвращать U+FFFD и другие нераспознаваемые символы.
    Стандартный print() на Windows крашится UnicodeEncodeError при их выводе,
    из-за чего обрывается весь OCR-цикл. Здесь подменяем неконвертируемые
    символы на '?' через кодировку самого stdout."""
    enc = (sys.stdout.encoding or 'utf-8')
    try:
        print(*args, **kwargs)
    except UnicodeEncodeError:
        safe = [str(a).encode(enc, errors='replace').decode(enc) for a in args]
        print(*safe, **kwargs)


OCR_MODEL = "glm-ocr"
OCR_BASE_URL = "http://127.0.0.1:1234/v1"
OCR_MAX_PARALLEL = 1     # vision-модели тяжёлые, не паралеллим
OCR_PAGE_DPI = 150       # компромисс между качеством и скоростью
OCR_MAX_TOKENS = 2048    # текст одной страницы влезет
OCR_PAGE_TIMEOUT_SEC = 60  # per-page timeout — защита от зависаний glm-ocr
OCR_PAGE_PROMPT = (
    "Extract all text content from this image of a book page. "
    "Output only the raw text in reading order, preserving paragraph breaks. "
    "Do NOT add commentary, headers, or descriptions of the image."
)


class OCREngine:
    def __init__(self):
        self.client = AsyncOpenAI(base_url=OCR_BASE_URL, api_key="lm-studio")
        self.model = OCR_MODEL
        self._semaphore = asyncio.Semaphore(OCR_MAX_PARALLEL)
        self._available: bool | None = None

    async def is_available(self) -> bool:
        if self._available is not None:
            return self._available
        try:
            models = await self.client.models.list()
            ids = {m.id for m in models.data}
            self._available = self.model in ids
        except Exception as e:
            _safe_print(f"OCR model check failed: {e}")
            self._available = False
        return self._available

    async def ocr_page_image(self, png_bytes: bytes) -> str:
        """OCR одной страницы (PNG bytes)."""
        b64 = base64.b64encode(png_bytes).decode()
        async with self._semaphore:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": OCR_PAGE_PROMPT},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{b64}"},
                        },
                    ],
                }],
                max_tokens=OCR_MAX_TOKENS,
                temperature=0.0,
            )
        return (response.choices[0].message.content or "").strip()

    async def ocr_document(self, doc, progress_callback=None, max_pages: int | None = None) -> str:
        """
        OCR всего документа. Возвращает склеенный полный текст.

        progress_callback(pct: int, msg: str) вызывается на каждую страницу.
        max_pages — ограничение для тестов / больших книг (по умолчанию все).

        Стратегия:
          - страницы с 0 символов И без растровых картинок → пропускаем
            (физически пусто, OCR ничего не извлечёт)
          - на остальные ставим OCR_PAGE_TIMEOUT_SEC таймаут — glm-ocr
            иногда зависает на обложках, нельзя блокировать весь pipeline
        """
        if not await self.is_available():
            raise RuntimeError(f"OCR model '{self.model}' is not available in LM Studio")

        total = len(doc) if max_pages is None else min(max_pages, len(doc))
        pieces: list = []
        skipped_empty = 0
        for i in range(total):
            page = doc[i]
            existing_text = page.get_text().strip()
            has_images = bool(page.get_images())

            # Полностью пустая страница: 0 символов И никаких встроенных растров.
            # Тратить OCR на такую — потеря времени без выхлопа.
            if not existing_text and not has_images:
                pieces.append("")
                skipped_empty += 1
                if progress_callback:
                    pct = int(10 + (i + 1) / total * 60)
                    await progress_callback(pct, f"страница {i+1}/{total} пустая, пропуск")
                continue

            pix = page.get_pixmap(dpi=OCR_PAGE_DPI)
            png_bytes = pix.tobytes("png")
            try:
                text = await asyncio.wait_for(
                    self.ocr_page_image(png_bytes),
                    timeout=OCR_PAGE_TIMEOUT_SEC,
                )
            except asyncio.TimeoutError:
                _safe_print(f"OCR page {i+1} timed out after {OCR_PAGE_TIMEOUT_SEC}s")
                text = ""
            except Exception as e:
                _safe_print(f"OCR page {i+1} failed: {e}")
                text = ""
            pieces.append(text)

            if progress_callback:
                pct = int(10 + (i + 1) / total * 60)  # OCR занимает большую часть прогресса
                await progress_callback(pct, f"OCR страницы {i+1}/{total}...")

        if skipped_empty:
            _safe_print(f"OCR: skipped {skipped_empty} fully empty pages")
        return "\n\n".join(pieces)


# Singleton
ocr_client = OCREngine()
