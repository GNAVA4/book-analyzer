"""Тесты выбора источника текста тела для маппинга (_pick_body_source).

Баг (session 012): когда build_toc эскалировал на OCR ради ОГЛАВЛЕНИЯ, его короткий
ocr_text подменял полное читаемое тело из text-layer (digital-design: 25k оглавления
вместо 1.5M тела → 94 секции в page_cut). Выбор источника теперь — прямое сравнение
качества + покрытия кандидатов, а не readability-флаг.
"""
from app.services.pdf_parser_neural import _pick_body_source

CLEAN = "Это нормальный читаемый русский текст из реальных слов и предложений. " * 60
GARBAGE = "1(апит €{}|\\ аловлоэк9ения 2пер$сонал| ц3 \\€{|}" * 60


class TestPickBodySource:
    def test_no_ocr_uses_text_layer(self):
        assert _pick_body_source(CLEAN, None) is CLEAN
        assert _pick_body_source(CLEAN, "") is CLEAN

    def test_garbage_text_layer_uses_ocr(self):
        """0e6e53b: text-layer — битый шрифт → берём полный OCR."""
        ocr = "Чистый OCR текст книги, полностью читаемый и осмысленный. " * 60
        assert _pick_body_source(GARBAGE, ocr) == ocr

    def test_clean_long_text_layer_beats_short_toc_ocr(self):
        """digital-design/978: text-layer читаем и длиннее короткого OCR-оглавления."""
        toc_ocr = "1 Введение ..... 5\n1.1 Обзор ..... 7\n2 Глава ..... 20\n" * 30
        long_clean = CLEAN * 30
        assert _pick_body_source(long_clean, toc_ocr) is long_clean

    def test_tiny_text_layer_falls_back_to_ocr(self):
        ocr = "Полный осмысленный OCR текст книги из реальных слов. " * 60
        assert _pick_body_source("  \n  ", ocr) == ocr

    def test_clean_but_shorter_text_layer_yields_to_longer_ocr(self):
        """Если text-layer чистый, но КОРОЧЕ полного OCR — берём OCR (покрытие)."""
        short_clean = CLEAN  # ~короткий
        long_ocr = CLEAN * 50
        assert _pick_body_source(short_clean, long_ocr) == long_ocr
