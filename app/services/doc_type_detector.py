import fitz


class DocType:
    PDF_TEXT = "pdf_text"
    PDF_SCAN = "pdf_scan"
    ENCRYPTED = "encrypted"
    DOCX = "docx"
    TXT = "txt"


def detect_pdf(file_path: str) -> dict:
    """Определяет тип PDF файла."""
    try:
        doc = fitz.open(file_path)
    except Exception as e:
        return {"type": DocType.PDF_TEXT, "confidence": 0.5, "error": f"fitz.open failed: {e}"}

    # Проверка шифрования
    if doc.is_encrypted:
        doc.close()
        return {"type": DocType.ENCRYPTED, "confidence": 1.0}

    # Проверка на скан: извлекаем текст с нескольких страниц
    sample_pages = min(5, len(doc))
    total_chars = 0
    
    for i in range(sample_pages):
        text = doc[i].get_text().strip()
        # Убираем типичные PDF-артефакты (переносы строк в середине слов)
        cleaned = text.replace('\n', ' ').replace('\x0c', '')
        total_chars += len(cleaned)

    doc.close()

    # Если в первых 5 страницах меньше ~200 символов текста — скорее всего скан
    avg_chars_per_page = total_chars / sample_pages if sample_pages > 0 else 0
    
    if avg_chars_per_page < 30:
        return {"type": DocType.PDF_SCAN, "confidence": 0.9}
    
    if avg_chars_per_page < 100:
        return {"type": DocType.PDF_SCAN, "confidence": 0.5}

    return {"type": DocType.PDF_TEXT, "confidence": 1.0}


def detect_docx(file_path: str) -> dict:
    """Определяет что файл является DOCX."""
    try:
        import docx
        doc = docx.Document(file_path)
        return {"type": DocType.DOCX, "confidence": 1.0}
    except Exception:
        return {"type": None, "confidence": 0.0}


def detect_txt(file_path: str) -> dict:
    """Определяет кодировку TXT файла."""
    for encoding in ["utf-8", "cp1251"]:
        try:
            with open(file_path, 'r', encoding=encoding) as f:
                content = f.read(4096)
            return {"type": DocType.TXT, "confidence": 1.0, "encoding": encoding}
        except (UnicodeDecodeError, Exception):
            continue

    return {"type": None, "confidence": 0.0}


def detect(file_path: str) -> dict:
    """Определяет тип любого файла."""
    ext = file_path.split('.')[-1].lower() if '.' in file_path else ""
    
    if ext == 'pdf':
        return detect_pdf(file_path)
    elif ext == 'docx':
        return detect_docx(file_path)
    elif ext in ('txt', 'text'):
        return detect_txt(file_path)
    else:
        return {"type": None, "confidence": 0.0}
