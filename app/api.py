from fastapi import APIRouter, UploadFile, File, WebSocket, Response
from app.services.docx_parser import parse_docx
from app.services.pdf_parser_fast import parse_pdf_fast
from app.services.pdf_parser_neural import parse_pdf_neural
from app.services.txt_parser import parse_txt
from app.services.xml_builder import build_tree_structure, dict_to_xml
import shutil
import os

router = APIRouter()


@router.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    temp_filename = f"temp_{file.filename}"
    with open(temp_filename, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    return {"filename": file.filename}


@router.post("/analyze/fast")
async def analyze_fast(filename: str):
    temp_filename = f"temp_{filename}"
    ext = filename.split('.')[-1].lower()

    try:
        if ext == 'docx':
            flat_nodes = parse_docx(temp_filename)
            toc_sequence = None
        elif ext == 'txt':
            flat_nodes, toc_sequence = parse_txt(temp_filename)
        else:
            flat_nodes, toc_sequence = parse_pdf_fast(temp_filename)

        tree_data = build_tree_structure(flat_nodes)
        xml_content = dict_to_xml(tree_data, toc_items=toc_sequence)
        return Response(content=xml_content, media_type="application/xml")
    finally:
        if os.path.exists(temp_filename):
            os.remove(temp_filename)


@router.websocket("/ws/analyze")
async def websocket_analyze(websocket: WebSocket):
    await websocket.accept()
    try:
        data = await websocket.receive_json()
        filename = data.get("filename")
        temp_filename = f"temp_{filename}"

        async def send_status(pct, msg):
            await websocket.send_json({"type": "progress", "percent": pct, "message": msg})

        flat_nodes, toc_sequence = await parse_pdf_neural(temp_filename, progress_callback=send_status)
        tree_data = build_tree_structure(flat_nodes)
        xml_content = dict_to_xml(tree_data, toc_items=toc_sequence)
        await websocket.send_json({"type": "complete", "xml": xml_content})
    except Exception as e:
        await websocket.send_json({"type": "error", "message": str(e)})
    finally:
        if 'temp_filename' in locals() and os.path.exists(temp_filename):
            os.remove(temp_filename)
        await websocket.close()