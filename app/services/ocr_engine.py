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
from openai import AsyncOpenAI


OCR_MODEL = "glm-ocr"
OCR_BASE_URL = "http://127.0.0.1:1234/v1"
OCR_MAX_PARALLEL = 1     # vision-модели тяжёлые, не паралеллим
OCR_PAGE_DPI = 150       # компромисс между качеством и скоростью
OCR_MAX_TOKENS = 2048    # текст одной страницы влезет
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
            print(f"OCR model check failed: {e}")
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
        """
        if not await self.is_available():
            raise RuntimeError(f"OCR model '{self.model}' is not available in LM Studio")

        total = len(doc) if max_pages is None else min(max_pages, len(doc))
        pieces: list = []
        for i in range(total):
            page = doc[i]
            pix = page.get_pixmap(dpi=OCR_PAGE_DPI)
            png_bytes = pix.tobytes("png")
            try:
                text = await self.ocr_page_image(png_bytes)
            except Exception as e:
                print(f"OCR page {i+1} failed: {e}")
                text = ""
            pieces.append(text)

            if progress_callback:
                pct = int(10 + (i + 1) / total * 60)  # OCR занимает большую часть прогресса
                await progress_callback(pct, f"OCR страницы {i+1}/{total}...")

        return "\n\n".join(pieces)


# Singleton
ocr_client = OCREngine()
