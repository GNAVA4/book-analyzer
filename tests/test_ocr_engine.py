"""Tests for ocr_engine — error recovery on glm-ocr 400."""

from app.services.ocr_engine import _extract_text_from_ocr_error


class _FakeBadRequest(Exception):
    """Mimics openai.BadRequestError shape (body dict + str repr)."""
    def __init__(self, body, message=""):
        super().__init__(message or str(body))
        self.body = body


class TestExtractTextFromOcrError:
    def test_extracts_text_after_marker(self):
        body = {"error": "Failed to parse input at pos 0: \n8-e правило\nДелегируйте!"}
        exc = _FakeBadRequest(body)
        text = _extract_text_from_ocr_error(exc)
        assert text.startswith("8-e правило")
        assert "Делегируйте!" in text

    def test_extracts_from_message_field(self):
        body = {"message": "Failed to parse input at pos 0: \nЧасть I\nВведение"}
        exc = _FakeBadRequest(body)
        text = _extract_text_from_ocr_error(exc)
        assert "Часть I" in text
        assert "Введение" in text

    def test_strips_trailing_json_garbage(self):
        """When body is empty, we fall back to str(exc) which may include trailing '}."""
        exc = _FakeBadRequest({}, "Failed to parse input at pos 0:\nHello world'}")
        text = _extract_text_from_ocr_error(exc)
        assert text == "Hello world"

    def test_returns_empty_on_unrelated_error(self):
        body = {"error": "Model not loaded"}
        exc = _FakeBadRequest(body)
        assert _extract_text_from_ocr_error(exc) == ""

    def test_handles_no_body(self):
        exc = Exception("Failed to parse input at pos 0: \nrecovered text")
        text = _extract_text_from_ocr_error(exc)
        assert "recovered text" in text

    def test_handles_none_body(self):
        exc = _FakeBadRequest(None, "no marker here")
        assert _extract_text_from_ocr_error(exc) == ""
