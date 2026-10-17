"""API анализа статей относительно именованных корпусов (страница static/article.html).

Эндпоинты синхронные (def): FastAPI выполняет их в пуле потоков, эмбеддинги и SQLite не
блокируют цикл событий. Один процесс, один CorpusStore; запись в БД — под замком.
"""
import json
import os
import tempfile
import threading
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app.analysis.article_report import analyze_article
from app.analysis.corpus import CorpusStore
from app.services.article_parser import parse_article_pdf

router = APIRouter(prefix='/api')
_store = None
_lock = threading.Lock()


def store() -> CorpusStore:
    global _store
    if _store is None:
        _store = CorpusStore()
    return _store


@router.get('/corpora')
def list_corpora():
    return store().corpora()


@router.post('/corpora')
def create_corpus(name: str = Form(...), description: str = Form('')):
    with _lock:
        cid = store().corpus_id(name.strip(), create=True)
        store().db.execute('UPDATE corpus SET description=? WHERE id=?', (description, cid))
        store().db.commit()
    return {'name': name.strip()}


@router.get('/corpora/{name}/documents')
def corpus_documents(name: str):
    try:
        return store().documents(name)
    except KeyError as e:
        raise HTTPException(404, str(e))


@router.get('/areas')
def areas():
    return [r[0] for r in store().db.execute(
        "SELECT DISTINCT area FROM document WHERE area_source='label' AND area IS NOT NULL ORDER BY area")]


@router.post('/article/analyze')
def analyze_upload(file: UploadFile = File(...), corpus: str = Form(...), scope: str = Form('topic'),
                   area: str = Form(''), add: bool = Form(False)):
    if not file.filename.lower().endswith('.pdf'):
        raise HTTPException(422, 'Пока поддерживается только PDF статьи')
    path = os.path.join(tempfile.gettempdir(), f'article_{uuid4().hex}.pdf')
    with open(path, 'wb') as f:
        f.write(file.file.read())
    try:
        parsed = parse_article_pdf(path)
    finally:
        os.remove(path)
    if parsed.get('needs_ocr'):
        raise HTTPException(422, 'Скан без текстового слоя — нужен OCR (пока не поддерживается)')
    try:
        report = analyze_article(parsed, store(), corpus, scope=scope, area=area or None)
    except KeyError as e:
        raise HTTPException(404, str(e))
    report['file'] = file.filename
    if add:
        with _lock:
            r = store().add_document(corpus, os.path.splitext(file.filename)[0], parsed,
                                     area=area or report['area']['area'])
        report['added'] = r
    return report


@router.get('/corpus-document/{doc_id}')
def analyze_corpus_document(doc_id: int, corpus: str, scope: str = 'topic'):
    """Документ, уже лежащий в корпусе, — анализ относительно корпуса без него самого."""
    row = store().db.execute('SELECT name, parsed, area, area_source FROM document WHERE id=?',
                             (doc_id,)).fetchone()
    if not row:
        raise HTTPException(404, 'нет документа')
    report = analyze_article(json.loads(row['parsed']), store(), corpus, scope=scope,
                             area=row['area'] if row['area_source'] == 'label' else None, exclude=doc_id)
    report['file'] = row['name']
    report['doc_id'] = doc_id
    return report
