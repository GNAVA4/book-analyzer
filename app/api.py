import os
import shutil
import tempfile
from uuid import uuid4

from fastapi import APIRouter, UploadFile, File, WebSocket, Response, HTTPException

from app.services.docx_parser import parse_docx
from app.services.pdf_parser_fast import parse_pdf_fast
from app.services.pdf_parser_neural import parse_pdf_neural
from app.services.txt_parser import parse_txt
from app.services.xml_builder import build_tree_structure, dict_to_xml

router = APIRouter()

ALLOWED_EXTENSIONS = {'pdf', 'docx', 'txt'}
MAX_FILE_SIZE = 150 * 1024 * 1024  # 150 МБ


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------

def _validate_extension(filename: str) -> str:
    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=422,
            detail=f"Неподдерживаемый формат «{ext}». Допустимы: {', '.join(ALLOWED_EXTENSIONS)}"
        )
    return ext


def _make_temp_path(original_filename: str) -> str:
    ext = original_filename.rsplit('.', 1)[-1] if '.' in original_filename else 'tmp'
    return os.path.join(tempfile.gettempdir(), f"bookanalyzer_{uuid4().hex}.{ext}")


def _safe_remove(path: str) -> None:
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Эндпоинты
# ---------------------------------------------------------------------------

@router.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    """
    Загружает файл. Возвращает temp_id для последующего /analyze.
    Лимит: 150 МБ. Допустимые форматы: PDF, DOCX, TXT.
    """
    _validate_extension(file.filename)

    temp_path = _make_temp_path(file.filename)
    try:
        written = 0
        with open(temp_path, "wb") as buf:
            while chunk := await file.read(256 * 1024):  # читаем по 256 КБ
                written += len(chunk)
                if written > MAX_FILE_SIZE:
                    _safe_remove(temp_path)
                    raise HTTPException(
                        status_code=413,
                        detail=f"Файл слишком большой. Лимит: {MAX_FILE_SIZE // 1024 // 1024} МБ"
                    )
                buf.write(chunk)
    except HTTPException:
        raise
    except Exception as e:
        _safe_remove(temp_path)
        raise HTTPException(status_code=500, detail=f"Ошибка сохранения: {e}")

    temp_id = os.path.basename(temp_path)
    return {"temp_id": temp_id, "original_name": file.filename}


@router.websocket("/ws/analyze")
async def websocket_analyze(websocket: WebSocket):
    """
    Гибридный (neural) анализ через WebSocket.
    Клиент шлёт: {"temp_id": "...", "deep_scan": false}

      deep_scan: bool (default false) — если ToC не найден ни эвристикой
                 ни через LLM, запустить дорогой режим где LLM сама
                 строит структуру по chunks полного текста. МЕДЛЕННО.

    Сообщения от сервера:
      {"type": "progress", "percent": 0-100, "message": "..."}
      {"type": "complete",  "xml": "...", "stats": {...}}
      {"type": "error",     "message": "..."}
    """
    await websocket.accept()
    temp_path = None

    try:
        data = await websocket.receive_json()
        temp_id = data.get("temp_id")
        deep_scan = bool(data.get("deep_scan", False))
        use_ocr = bool(data.get("use_ocr", True))

        if not temp_id:
            await websocket.send_json({"type": "error", "message": "temp_id не передан"})
            return

        temp_path = os.path.join(tempfile.gettempdir(), temp_id)
        if not os.path.exists(temp_path):
            await websocket.send_json({"type": "error", "message": "Файл не найден. Загрузите заново."})
            return

        async def send_status(pct: int, msg: str):
            await websocket.send_json({"type": "progress", "percent": pct, "message": msg})

        flat_nodes, toc_sequence, meta = await parse_pdf_neural(
            temp_path,
            progress_callback=send_status,
            deep_scan=deep_scan,
            use_ocr=use_ocr,
        )

        stats = {
            "total_sections": len(flat_nodes),
            "avg_confidence": round(
                sum(n.get('confidence', 1.0) for n in flat_nodes) / max(len(flat_nodes), 1),
                3
            ),
            "low_confidence_sections": sum(
                1 for n in flat_nodes if n.get('confidence', 1.0) < 0.80
            ),
            "unreadable": any(
                "НЕЧИТАЕМ" in n.get('content', '') for n in flat_nodes
            ),
            "toc_source": meta.get("toc_source", "unknown"),
            "deep_scan_used": meta.get("deep_scan_used", False),
            "ocr_used": meta.get("ocr_used", False),
        }

        tree_data = build_tree_structure(flat_nodes)
        xml_content = dict_to_xml(tree_data, toc_items=toc_sequence)

        await websocket.send_json({
            "type": "complete",
            "xml": xml_content,
            "stats": stats,
        })

    except Exception as e:
        await websocket.send_json({"type": "error", "message": str(e)})
    finally:
        _safe_remove(temp_path)
        await websocket.close()


@router.post("/analyze/fast")
async def analyze_fast(temp_id: str):
    """Алгоритмический анализ. Принимает temp_id из /upload."""
    temp_path = os.path.join(tempfile.gettempdir(), temp_id)

    if not os.path.exists(temp_path):
        raise HTTPException(status_code=404, detail="Файл не найден. Загрузите заново.")

    ext = temp_id.rsplit('.', 1)[-1].lower() if '.' in temp_id else ''

    try:
        if ext == 'docx':
            flat_nodes = parse_docx(temp_path)
            toc_sequence = None
        elif ext == 'txt':
            flat_nodes, toc_sequence = parse_txt(temp_path)
        else:
            flat_nodes, toc_sequence = parse_pdf_fast(temp_path)

        tree_data = build_tree_structure(flat_nodes)
        xml_content = dict_to_xml(tree_data, toc_items=toc_sequence)
        return Response(content=xml_content, media_type="application/xml")
    finally:
        _safe_remove(temp_path)


