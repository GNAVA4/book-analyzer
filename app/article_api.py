"""API анализа статей относительно именованных корпусов (страница static/article.html).

Эндпоинты синхронные (def): FastAPI выполняет их в пуле потоков, эмбеддинги и SQLite не
блокируют цикл событий. Один процесс, один CorpusStore; запись в БД — под замком.
"""
import datetime
import json
import os
import tempfile
import threading
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app.analysis.article_report import analyze_article
from app.analysis.corpus import CorpusStore
from app.analysis.water import WaterJob
from app.services.article_docx import parse_article_docx
from app.services.article_parser import parse_article_pdf

router = APIRouter(prefix='/api')
_store = None
_lock = threading.Lock()


_water = None


def store() -> CorpusStore:
    global _store
    if _store is None:
        _store = CorpusStore()
    return _store


def water() -> WaterJob:
    global _water
    if _water is None:
        _water = WaterJob(store())
    return _water


def _need_embedder():
    if water().busy():
        raise HTTPException(503, 'Идёт проверка на воду: видеокарта занята большой моделью, эмбеддер '
                                 'выгружен. Новые файлы можно будет проверить после её окончания '
                                 '(или остановите проверку). Документы корпуса открываются и сейчас.')


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
                   area: str = Form(''), add: bool = Form(False), year: int = Form(None),
                   time_mode: str = Form('before'), include_undated: bool = Form(True)):
    ext = os.path.splitext(file.filename.lower())[1]
    if ext not in ('.pdf', '.docx'):
        raise HTTPException(422, 'Поддерживаются PDF и DOCX')
    path = os.path.join(tempfile.gettempdir(), f'article_{uuid4().hex}{ext}')
    with open(path, 'wb') as f:
        f.write(file.file.read())
    try:
        parsed = parse_article_pdf(path) if ext == '.pdf' else parse_article_docx(path)
    finally:
        os.remove(path)
    if not parsed['sections']:
        raise HTTPException(422, 'Не удалось выделить текст работы: ' + '; '.join(parsed['warnings']))
    _need_embedder()
    if parsed.get('needs_ocr'):
        raise HTTPException(422, 'Скан без текстового слоя — нужен OCR (пока не поддерживается)')
    try:
        year = year or datetime.date.today().year          # загружаемая работа — по умолчанию свежая
        report = analyze_article(parsed, store(), corpus, scope=scope, area=area or None, year=year,
                                 time_mode=time_mode, include_undated=include_undated)
    except KeyError as e:
        raise HTTPException(404, str(e))
    report['file'] = file.filename
    if add:
        with _lock:
            r = store().add_document(corpus, os.path.splitext(file.filename)[0], parsed,
                                     area=area or report['area']['area'], meta={'year': year})
        report['added'] = r
    return report


@router.get('/corpus-document/{doc_id}')
def analyze_corpus_document(doc_id: int, corpus: str, scope: str = 'topic', time_mode: str = 'before',
                            include_undated: bool = True):
    """Документ, уже лежащий в корпусе, — анализ относительно корпуса без него самого."""
    row = store().db.execute('SELECT name, parsed, area, area_source, year FROM document WHERE id=?',
                             (doc_id,)).fetchone()
    if not row:
        raise HTTPException(404, 'нет документа')
    report = analyze_article(json.loads(row['parsed']), store(), corpus, scope=scope,
                             area=row['area'] if row['area_source'] == 'label' else None, exclude=doc_id,
                             year=row['year'], time_mode=time_mode, include_undated=include_undated)
    report['file'] = row['name']
    report['doc_id'] = doc_id
    return report


# --- «вода» (app/analysis/water.py) ---------------------------------------

@router.get('/water/status')
def water_status():
    return water().state


@router.post('/water/start')
def water_start(payload: dict):
    """payload: {texts: [...], label: '...'} — абзацы открытого документа."""
    return water().start(payload.get('texts') or [], payload.get('label') or 'документ')


@router.post('/water/start-corpus')
def water_start_corpus(payload: dict):
    """payload: {corpora: ['...']} — все абзацы всех документов этих корпусов (по порядку)."""
    texts, names = [], payload.get('corpora') or []
    for name in names:
        for (parsed,) in store().db.execute(
                'SELECT d.parsed FROM document d JOIN corpus c ON c.id = d.corpus_id WHERE c.name=? ORDER BY d.id', (name,)):
            d = json.loads(parsed)
            texts += [t for s in d['sections'] for t in s['paragraphs']]
    return water().start(texts, 'корпуса: ' + ', '.join(names))


@router.post('/water/stop')
def water_stop():
    water().stop()
    return water().state


@router.post('/water/profiles')
def water_profiles(payload: dict):
    """payload: {texts: [...]} → {sha1: профиль} для проверенных абзацев."""
    return water().lookup(payload.get('texts') or [])


@router.post('/water/pending')
def water_pending(payload: dict):
    texts = payload.get('texts') or []
    pend = water().pending(texts)
    return {'pending': len(pend), 'pending_chars': sum(len(t) for t in pend)}


# --- разметка (app/analysis/labels.py, static/label.html) -----------------

@router.post('/label/generate')
def label_generate(payload: dict):
    from app.analysis import labels
    return labels.generate(store(), payload.get('corpora') or [])


@router.get('/label/items')
def label_items(kind: str, annotator: str):
    from app.analysis import labels
    return {'items': labels.items(store().db, kind, annotator), 'labels': labels.LABELS[kind]}


@router.post('/label')
def label_save(payload: dict):
    from app.analysis import labels
    if not (payload.get('annotator') or '').strip():
        raise HTTPException(422, 'Укажите имя разметчика')
    labels.save(store().db, int(payload['item_id']), payload['annotator'].strip(), payload['value'])
    return {'ok': True}


@router.get('/label/report')
def label_report():
    from app.analysis import labels
    return labels.report(store().db)
