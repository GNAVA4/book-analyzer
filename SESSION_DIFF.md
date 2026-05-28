# Изменения за сессию (2026-05-28)

Базовая точка: коммит `bbd4cb4` — «Add fuzzy matching layer for ToC titles».  
Текущее состояние: коммит `59a86bd` — 3 коммита поверх базы.

```
59a86bd  Scrub CJK from all content paths, not just LLM clean
8948953  Enable all pipeline flags by default + CJK-aware clean
3def216  Fix OCR cp1251 crash + add ToC validator + heuristic polish
```

**Итого:** 21 файл, +1165 строк, −87 строк.

---

## 1. Что починили (баги)

### 1.1. OCR падал на странице 22 в Windows (cp1251) ⭐ главный фикс

**Симптом:** Книга `0e6e53b` всегда возвращала пустой XML с «Документ нечитаем».  
**Причина:** `print()` в `ocr_engine.py` вызывался с символом U+FFFD (replacement char,
который ставит glm-ocr вместо нераспознанных глифов). Консоль Windows с кодировкой
cp1251 не умеет выводить U+FFFD → `UnicodeEncodeError` → OCR-цикл падал на странице 22,
возвращал `None` → pipeline выдавал «нечитаем».

**Фикс:**
- `app/services/ocr_engine.py` — добавлена `_safe_print()`: при UnicodeEncodeError
  транскодирует строку с `errors='replace'` и повторяет вывод
- `app/main.py` — `sys.stdout.reconfigure(encoding='utf-8', errors='replace')` в старте

**Результат:** `0e6e53b` теперь обрабатывается полностью — 35 секций, 34 с контентом.

---

### 1.2. WebSocket двойное закрытие → RuntimeError

**Симптом:** В логах сервера периодически `RuntimeError: Cannot call "send" after closing connection`.  
**Причина:** `finally: await websocket.close()` вызывался когда клиент уже отключился.  
**Фикс:** `app/api.py` — перед любым `send` и в `finally` проверяем
`websocket.application_state == WebSocketState.CONNECTED`.

---

## 2. Что улучшили

### 2.1. CJK-символы (иероглифы) в контенте → полностью убраны

glm-ocr иногда вставляет китайские иероглифы (动机) вместо нераспознанного кириллического
текста. До сессии иероглифы попадали в XML через `fast_clean_chunk`-путь (confidence ≥ 0.85),
потому что там LLM не вызывается.

**Фикс (двухслойный):**
- `app/services/llm_engine.py` — добавлена публичная функция `scrub_foreign_script(text)`,
  убирает CJK/арабский/иврит/корейский, сохраняет латиницу и кириллицу
- `app/services/pdf_parser_neural.py` — `scrub_foreign_script()` применяется ко ВСЕМ
  `final_nodes` (и `title`, и `content`) независимо от пути очистки; также к заголовкам
  `sequence` (NavigationTable)
- Дополнительно в `llm_engine.py`: если INPUT в LLM-clean содержит CJK → retry с
  объяснительным промптом («OCR вставил иероглифы вместо кириллицы»); если retry снова
  даёт CJK → `scrub_foreign_script` применяется к результату

**Результат:** `0e6e53b` — 0 иероглифов в XML (было 49).

---

### 2.2. Склеенные строки ToC в OCR → корректно разбиваются (Иглмен)

glm-ocr объединяет несколько строк ToC в одну:
`ЧАСТЬ I. ПРЕДЧУВСТВИЕ14Глава 1ВОСПРИЯТИЕ15Глава 2ВОСПРИЯТИЕ16`

**Фикс:** `app/services/toc_builder.py` — добавлена `_split_sticky_toc_lines(text)`:
- Находит строки длиннее 60 символов с 2+ вхождениями паттерна «пробел + число + пробел + заглавная»
- Расставляет переносы строк, превращая склейку в корректный список строк ToC
- Применяется и к OCR-тексту, и к PyMuPDF raw_text (для читаемых PDF)

**Результат:** Иглмен — 14 склеенных записей → 22 корректные секции.

---

### 2.3. `exact_normalized`: whitespace-only разница даёт confidence=1.0

Было: `tokenized_regex` (убирает \n, \t, лишние пробелы между словами) давал confidence=0.85,
даже если после нормализации заголовок совпадал идеально.

**Фикс:** `app/services/pdf_utils.py` — после tokenized_regex match сравниваем
нормализованный текст совпадения с нормализованным заголовком; если равны →
`strategy='exact_normalized', confidence=1.0`.  
`app/services/mapping_pipeline.py` — `exact_normalized` добавлен в whitelist
для page-distance проверки.

**Результат:** Массель — avg_confidence 0.85 → 1.0, все секции идут через `fast_clean`
(LLM clean не вызывается вообще).

---

### 2.4. Дедупликация ToC: регистронезависимая на всех путях

Было: дедуп использовал ключ `(title.lower(), page)` — секции с разными страницами
оставались дублями. Также применялся только в конце `build_toc`, а OCR-пути возвращались
раньше без дедупа.

**Фикс:** `app/services/toc_builder.py` —
- Ключ дедупа изменён на `title.lower()` (только заголовок, без страницы)
- При конфликте берётся запись с большей страницей (за исключением page=None)
- `_dedup_and_order` применяется на **всех** return-путях

**Результат:** Массель — дубль «2. ПРИНЦИПЫ» / «2.Принципы» больше не появляется.

---

### 2.5. Trim длинных заголовков в эвристике ToC

Было: «Благодарности Я написал эту книгу потому что...» входил как заголовок целиком.  
**Фикс:** `app/services/toc_parser.py` —
- `_COMPLETE_SECTION_NAMES` — frozenset известных коротких терминальных заголовков
  (Благодарности, Примечания, Глоссарий, Приложение, Введение и др.)
- `_is_complete_section_name(title)` — проверяет принадлежность
- `_trim_description_after_section_name(line)` — обрезает продолжение строки когда
  обнаружен полный заголовок

---

### 2.6. OCR: timeout и пропуск пустых страниц

`app/services/ocr_engine.py`:
- `OCR_PAGE_TIMEOUT_SEC = 60` — per-page timeout через `asyncio.wait_for`
- Пропускаем страницы где **и** символов 0 **и** изображений 0 (`page.get_images()`)
  — такие страницы пустые физически, OCR не поможет

---

### 2.7. Все флаги pipeline по умолчанию включены

Раньше `deep_scan`, `llm_expand`, `validate_toc_ocr` были выключены по умолчанию.

**Фикс:**
- `app/services/pdf_parser_neural.py` — сигнатура `parse_pdf_neural(..., deep_scan=True, use_ocr=True, llm_expand=True, validate_toc_ocr=True)`
- `app/api.py` — дефолты в парсинге параметров WebSocket сообщения
- `static/index.html` — чекбоксы `optDeepScan`, `optLlmExpand`, `optValidateTocOcr` теперь `checked` по умолчанию

---

### 2.8. Новый модуль: `toc_validator.py`

`validate_toc_via_ocr(doc, sequence, progress_cb)`:
- OCR страниц ±2 перед и +8 после найденной страницы ToC
- Сравнивает извлечённые заголовки с заголовками из sequence
- Возвращает `{coverage_pct, toc_page, ocr_pages, missing_titles, extra_lines}`
- Пропускается если ToC уже получен из OCR (self-comparison бессмысленно)
- Результат идёт в `meta['toc_validation']` ответа WebSocket

---

### 2.9. Safety net: пустой XML → полный текст одной секцией

Если `sequence` пуст (ни один уровень ToC не сработал), но `full_text` есть → вместо
тихого пустого XML возвращается единственная секция «Полный текст книги» с `match_strategy='fallback_full_text'`.

---

### 2.10. Новые инструменты разработки

- `scripts/run_pipeline.py` — E2E тест: POST /upload + WebSocket /ws/analyze, сохраняет
  XML в `xml/<basename>.xml`, печатает stats + summary по всем файлам
- `.gitignore` — добавлен `xml/` (тестовые результаты не коммитим)
- `PIPELINE_DIFF.md` — сравнение ранней реализации (54dc814) с текущей на момент начала сессии

---

## 3. Покрытие тестами

Добавлены тесты (все проходят без реального LLM/OCR):

| Файл | Что тестирует |
|---|---|
| `tests/test_toc_builder.py` | `_split_sticky_toc_lines`, `_dedup_and_order` |
| `tests/test_toc_validator.py` | `validate_toc_via_ocr` с mock-OCR |
| `tests/test_mapping_pipeline.py` | page-distance verify, backup/restore, fuzzy (уже был) |
| `tests/test_pdf_utils.py` | `exact_normalized` promotion |
| `tests/test_llm_engine.py` | `scrub_foreign_script`, `has_foreign_script`, `count_foreign_chars` |

---

## 4. Результаты по книгам

| Книга | До сессии | После сессии | Вердикт |
|---|---|---|---|
| **0e6e53b** (OCR) | ❌ пустой XML, «нечитаем» | 35 секций, 34 с контентом, 0 CJK | ⭐ Главное улучшение |
| **Массель** | 23 сек, avg=0.85, дубль «ПРИНЦИПЫ» | 23 сек, avg=1.0, без дублей | Лучше |
| **Иглмен** | ~14 склеенных | 22 секции (16 с контентом) | Лучше |
| **Клейнман** | 40+ секций, норм | Без изменений | Нейтрально |
| **Розенсон** | 20 секций | Без изменений | Нейтрально |
| **Release It!** | 33 сек, avg=0.91 | 34 сек, avg=0.654, **7 conf=0.00** | ⚠ Регрессия |

---

## 5. Открытая регрессия: Release It! (не исправлена)

**Симптом:** 7 секций (страницы 13–24) имеют confidence=0.00 и пустой контент.

Затронутые секции (все в предисловии + начало главы 1):
- «Для кого предназначена эта книга?» (стр. 13)
- «Структура книги» (стр. 14)
- «Благодарности» (стр. 15)
- «1.1. ...» (стр. 17), «1.2. ...» (стр. 18), «1.5. ...» (стр. 20)
- «Часть I. Стабильность» (стр. 24)

**Предполагаемая причина:** page-distance проверка отвергает exact-match, найденный
в зоне ToC (страницы 8–12 PDF — само оглавление), rescue не находит корректного
места в окне страниц 13–24. Точная причина не установлена — отложено.

**До начала сессии** эти секции были как-то найдены (avg=0.91). Скорее всего, дело в
изменении `_dedup_and_order` или применении `_split_sticky_toc_lines` к PyMuPDF-тексту,
что изменило структуру `sequence` и сдвинуло page-positions.

---

## 6. Известные нерешённые проблемы (без изменений с прошлой сессии)

| Проблема | Причина | Решение |
|---|---|---|
| Иглмен: 6 секций не найдено | Заголовки вёрстаны крупным шрифтом, PyMuPDF не извлекает | Page-cut fallback (нарезать по page-hint, не по title) |
| Do Good Design: «Об авторе» захватывает копирайт-спам | `_restored_after_rescue_fail` оживляет ложный exact-match | Trim known legal blurbs или флаг подозрительности |
| 0e6e53b: скорость (1+ час) | 14 low-conf секций → LLM clean | Уже параллельный (asyncio.gather), но 14 × 35K chars = долго |
