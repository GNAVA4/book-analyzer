import sys

# Windows console default cp1251 крашится на символе U+FFFD (replacement char)
# который glm-ocr возвращает для нераспознанных мест. Любой print() с таким
# символом вылетает UnicodeEncodeError и обрывает весь OCR-цикл.
# errors='replace' гарантирует что print не упадёт никогда.
if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace', line_buffering=True)
if sys.stderr and hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace', line_buffering=True)

# Sanity-маркер: видно ли это сообщение → reconfigure применён
print(f"[app.main] stdout encoding={sys.stdout.encoding} errors={sys.stdout.errors}", flush=True)

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from app.api import router

app = FastAPI(title="Book Analyzer Service")

# Подключаем роуты
app.include_router(router)

# Подключаем статику (наш index.html)
app.mount("/", StaticFiles(directory="static", html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)