"""
«Вода» в сервисе: разбор абзацев на утверждения большой моделью (app/analysis/claims.py) в фоне.

Абзац водянистый, если среди его утверждений общие слова и связки — не меньше половины
(WATERY_GENERAL_SHARE, как в scripts/claims_corpus.py и отчёте session 018). Плотность —
содержательные утверждения (результат + факт) на 1000 знаков.

Результаты хранятся в data/corpus.db (таблица para_claims, ключ — sha1 текста абзаца), поэтому:
  - повторная проверка того же абзаца (в любом документе) мгновенна;
  - прерванный прогон продолжается с места остановки.

Видеокарта одна (12 ГБ): перед LLM выгружаются ВСЕ модели (указание user), после — LLM
выгружается и возвращается эмбеддер. Пока идёт проверка, новые тексты эмбеддить нельзя
(эмбеддер выгружен) — API сообщает об этом.
"""
import hashlib
import json
import sqlite3
import subprocess
import threading
import time

from app.analysis import claims as C
from app.analysis.semantic import EMBED_MODEL, is_formula

LMS = r'C:\Users\rusla\.lmstudio\bin\lms.exe'
LLM_LOAD = [C.LLM_MODEL, '-c', '4096', '--parallel', '1']     # session 017/022: VRAM ~65%
EMBED_LOAD = [EMBED_MODEL, '-c', '8192']                       # [user, s022]
MIN_PARA = 200               # короче — подписи, обрывки: утверждений не даёт (claims_corpus.MIN_PARA)
WATERY_GENERAL_SHARE = 0.5   # как в scripts/claims_corpus.py
RETRIES, RETRY_PAUSE = 3, 15  # повтор запроса к LLM при сбое; 15 с — время на освобождение памяти

_SCHEMA = """CREATE TABLE IF NOT EXISTS para_claims (
  hash TEXT PRIMARY KEY, model TEXT, chars INTEGER, claims TEXT, created REAL)"""


def text_hash(t: str) -> str:
    return hashlib.sha1(t.encode('utf-8')).hexdigest()


def eligible(t: str) -> bool:
    return len(t) >= MIN_PARA and not is_formula(t)


def profile(claims: list, chars: int) -> dict:
    p = C.claim_profile(claims, chars)
    return {'claims': p['claims'], 'substantive': p['результат'] + p['факт'],
            'general': p['общее'] + p['связка'], 'density': round(p['substantive_per_1000'], 2),
            'general_share': round(p['general_share'], 2),
            'watery': p['claims'] > 0 and p['general_share'] >= WATERY_GENERAL_SHARE,
            'items': claims}


def lookup(db, texts: list) -> dict:
    """sha1(текст) → профиль утверждений для уже проверенных абзацев."""
    db.execute(_SCHEMA)
    keys = list({text_hash(t) for t in texts})
    out = {}
    for i in range(0, len(keys), 500):
        part = keys[i:i + 500]
        q = 'SELECT hash, chars, claims FROM para_claims WHERE hash IN (%s)' % ','.join('?' * len(part))
        for h, chars, cl in db.execute(q, part):
            out[h] = profile(json.loads(cl), chars)
    return out


def _lms(*args, timeout=600) -> str:
    r = subprocess.run([LMS, *args], capture_output=True, text=True, encoding='utf-8',
                       errors='replace', timeout=timeout)
    return r.stdout + r.stderr


class WaterJob:
    """Одна фоновая проверка за раз: очередь текстов → утверждения → para_claims."""

    def __init__(self, store):
        self.store = store
        store.db.execute(_SCHEMA)
        store.db.commit()
        self.lock = threading.Lock()
        self.thread = None
        self.state = {'running': False, 'phase': 'idle', 'label': '', 'total': 0, 'done': 0,
                      'skipped_cached': 0, 'started': None, 'finished': None, 'error': None,
                      'sec_per_para': None}
        self._stop = False

    # --- результаты ---
    def lookup(self, texts: list) -> dict:
        return lookup(self.store.db, texts)

    def pending(self, texts: list) -> list:
        texts = list(dict.fromkeys(t for t in texts if eligible(t)))
        have = self.lookup(texts)
        return [t for t in texts if text_hash(t) not in have]

    # --- управление ---
    def busy(self) -> bool:
        return self.state['running']

    def start(self, texts: list, label: str) -> dict:
        with self.lock:
            if self.state['running']:
                return {'ok': False, 'reason': 'проверка уже идёт', 'state': self.state}
            todo = self.pending(texts)
            n_elig = len({t for t in texts if eligible(t)})
            self.state.update(running=bool(todo), phase='queued' if todo else 'idle', label=label,
                              total=len(todo), done=0, skipped_cached=n_elig - len(todo),
                              started=time.time(), finished=None if todo else time.time(),
                              error=None, sec_per_para=None)
            if not todo:
                return {'ok': True, 'state': self.state}
            self._stop = False
            self.thread = threading.Thread(target=self._run, args=(todo,), daemon=True)
            self.thread.start()
            return {'ok': True, 'state': self.state}

    def stop(self):
        self._stop = True

    @staticmethod
    def _extract(llm, t: str) -> list:
        """Разовый сбой LM Studio (500 при нехватке памяти, s023) не должен обрывать многочасовой
        прогон: RETRIES попыток с паузой, потом ошибка поднимается."""
        for attempt in range(RETRIES):
            try:
                return C.extract_claims(llm, t)    # кэш .cache/llm_claims.jsonl тоже используется
            except Exception:                       # noqa: BLE001
                if attempt == RETRIES - 1:
                    raise
                time.sleep(RETRY_PAUSE)

    def _run(self, todo: list):
        llm = C.ClaimsLLM()
        db = sqlite3.connect(self.store.path, timeout=30)    # своё соединение для фонового потока
        try:
            self.state['phase'] = 'loading'
            if C.LLM_MODEL not in _lms('ps'):
                _lms('unload', '--all')
                out = _lms('load', *LLM_LOAD, '-y')
                if 'successfully' not in out.lower():
                    raise RuntimeError('LLM не загрузилась: ' + out.strip()[-300:])
            self.state['phase'] = 'checking'
            t0 = time.time()
            for t in todo:
                if self._stop:
                    break
                claims = self._extract(llm, t)
                db.execute('INSERT OR REPLACE INTO para_claims VALUES (?,?,?,?,?)',
                           (text_hash(t), C.LLM_MODEL, len(t), json.dumps(claims, ensure_ascii=False), time.time()))
                db.commit()
                self.state['done'] += 1
                self.state['sec_per_para'] = round((time.time() - t0) / self.state['done'], 1)
        except Exception as e:                             # noqa: BLE001 — показать на странице
            self.state['error'] = str(e)
        finally:
            self.state['phase'] = 'restoring'
            try:
                _lms('unload', '--all')
                _lms('load', *EMBED_LOAD, '-y')
            except Exception as e:                         # noqa: BLE001
                self.state['error'] = (self.state['error'] or '') + f' / эмбеддер: {e}'
            self.state.update(running=False, phase='stopped' if self._stop else 'done',
                              finished=time.time())
