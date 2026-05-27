# Сравнение реализаций: ранняя итерация vs текущая

Базовая точка отсчёта — коммит `54dc814` («Add 5 quality features: ToC filters, rescue, embeddings, OCR»). Это первая версия в ветке `llm-toc-fallback` с уже работающим OCR и LLM-fallback'ами, и именно эта версия описана в документе «Что успел сделать подробнее…».

Текущее состояние — рабочее дерево после моих изменений (сегодня).

---

## 1. Архитектура: что общего, что разное

| Аспект | 54dc814 (первая) | Текущая | Изменение |
|---|---|---|---|
| `parse_pdf_neural` | Монолит, ~350 строк, всё внутри | Тонкий координатор (~210 строк), логика в `toc_builder` + `mapping_pipeline` | + |
| ToC pipeline | Inline в neural-парсере, ~3 уровня | Отдельный `toc_builder.py` с **6 уровнями** (heuristic → LLM → OCR+heuristic → OCR+LLM → deep_scan → LLM-expand) | + |
| Mapping | `find_real_indices` + 1-уровневый rescue | Отдельный `mapping_pipeline.py` с **5 уровнями** (regex → page-hint → fuzzy → embedding → LLM rescue) + двухпроходный verify | + |
| `_safe_print` для OCR | ❌ нет | ✅ есть (фикс cp1251 / U+FFFD) | + (главный фикс сегодня) |
| OCR per-page timeout | ❌ нет | ✅ 60 сек | + |
| OCR пропуск пустых страниц | ❌ нет | ✅ 0 chars + 0 images → skip | + |
| Dedup ToC | На уровне `_normalize_llm_items` | Полный `_dedup_and_order` (case-insensitive, выбор лучшего) + применяется **на всех return-путях** | + (сегодня) |
| Split-склейки в OCR ToC | ❌ нет | ✅ `_split_sticky_toc_lines` (Иглмен-кейс) | + (сегодня) |
| `exact_normalized` (1.0 при whitespace-only diff) | ❌ только tokenized=0.85 | ✅ есть | + (сегодня) |
| Trim хвоста секций | ❌ нет | ✅ `_trim_description_after_section_name` («Благодарности Я написал…») | + (сегодня) |
| OCR-валидация ToC (±5 стр.) | ❌ нет | ✅ `toc_validator.py` (опционально) | + (сегодня) |
| Safety net пустого XML | ❌ нет | ✅ fallback на «полный текст» | + (сегодня) |
| WebSocket double-close | Краш `RuntimeError` | Корректная обработка `WebSocketDisconnect` | + (сегодня) |

---

## 2. Что в текущей реализации стало хуже

| Регрессия | Причина | Где |
|---|---|---|
| Жёсткие пороги контекста (`LLM_CHUNK_SIZE=3000`, `TOC_LLM_MAX_CHARS=12000`) | Раньше было 2500/6000 под ctx=4096 | `llm_engine.py`, `toc_builder.py` |
| `CONFIDENCE_THRESHOLD=0.85` vs 0.80 в первой | Чуть больше секций идёт в LLM clean (медленнее) | `pdf_parser_neural.py` |
| `TOC_GOOD_ENOUGH=12` vs 8 в первой | Pipeline дольше «не останавливается» на heuristic-результате, чаще зовёт LLM | `toc_builder.py` |
| Каскад `restored_after_rescue_fail` | Восстанавливает заведомо ложные exact-матчи если rescue не помог — попадает в content «Books.Ru / piracy» (Do Good Design кейс) | `mapping_pipeline.py:_verify_and_correct_order` + restore-блок |

Это решения принятые **между** 54dc814 и `bbd4cb4` (последний коммит до моих сегодняшних правок) — они часть эволюции, не моего отката.

---

## 3. Где мы сейчас застряли

### 3.1. Книги где сегодня ВСЁ хорошо
- **Массель** — 23 секции, avg_conf=0.996, 0 not_found (улучшение: дубль `2. ПРИНЦИПЫ` ушёл, conf 0.85→1.0)
- **Розенсон** — 20 секций, 16 с контентом, 0 not_found
- **0e6e53b** — **35 секций, 34 с контентом, avg_conf=0.845** ⭐ (раньше отдавался пустой XML с «Документ нечитаем», сегодня после фикса cp1251 работает целиком через OCR + LLM-ToC + embedding-rescue)

### 3.2. Книги где остаются точечные проблемы
- **Иглмен** — 22 секции в ToC (раньше склеивались в 14), но 6 не_найдены в теле PDF потому что заголовки глав вёрстаны крупным шрифтом который PyMuPDF не извлекает как текст. **Не баг pipeline'а, ограничение исходного PDF.**
- **parallelnoe** — 248 секций, 160 с контентом, ~25 + 50 reverted в Приложениях A–D (вёрстка не извлекается). Каскадного провала больше нет (раньше после 10.1.2 терялось 80+).
- **Do Good Design** — 18 секций, 13 с контентом, «Глава 4», «Глава 6» не_найдены (та же причина, заголовки крупным шрифтом). «Благодарности» теперь чистая.

### 3.3. Открытые архитектурные проблемы

#### А) `_restored_after_rescue_fail` сохраняет ложные совпадения
В Do Good Design «Об авторе» матчит часть страницы с легальным спамом «Books.Ru». Page-distance верификатор отбрасывает находку, но rescue не находит лучшего варианта → восстанавливаем ложную exact-позицию с conf=0.70. Контент содержит лицензионный текст вместо настоящего «Об авторе».

**Нужно**: либо trim copyright/legal blurbs из контента, либо для секций где best alternative тоже сомнительный (rescue ниже threshold) — оставлять пустым с пометкой «найдено в спам-зоне».

#### Б) Заголовки глав как picture в PDF
PyMuPDF не извлекает крупный типографский шрифт. Сейчас pipeline ищет title в тексте — если его там нет, секция теряется. Альтернатива: для секций с page-hint без алгоритмического match — **резать контент по странице** (от page до next.page), без поиска title.

#### В) Низкий confidence при OCR-derived ToC
Иглмен, 0e6e53b: ToC извлечён через LLM, но в OCR-тексте title записан немного иначе (motivation vs мотивация). exact не матчит → fuzzy/embedding rescue, conf 0.5-0.7 → каждая секция идёт в LLM clean → медленно (~1 час на 35-секционную книгу).

**Нужно**: или быстрый clean для conf>=0.5 (OCR-derived текст обычно уже чистый), или параллельный LLM clean (сейчас sequential).

#### Г) Контексты в LM Studio под пользователя
Сейчас в коде стоят 8K-параметры (ctx >= 8192). У пользователя по умолчанию 4K. Когда модель загружена через JIT с 4K, всё валится с `Context size exceeded`. Решается на стороне юзера (`lms load --context-length 8192`), но pipeline должен это проверять и предупреждать.

---

## 4. Плюсы прежнего подхода (54dc814)

1. **Простота**: монолит `parse_pdf_neural` — легче читать и отлаживать. Сейчас цепочка `parse_pdf_neural → build_toc → _heuristic/_llm_from_text/_ocr_then_*  → map_sequence → _verify_and_correct_order → cleanup` сложна для трейсинга.
2. **Меньше «уровней не_найдено»**: rescue был одноуровневым, не было `_restored_after_rescue_fail` который оживляет ложные находки.
3. **Меньше зависимостей**: 1 LLM модель + 1 embed + 1 OCR. Сейчас тот же набор, но больше точек где они вызываются (clean, ToC, rescue, locate).

## 5. Плюсы текущего подхода

1. **Корректность OCR-кейса 0e6e53b** — главный win. Без `_safe_print` оригинальная реализация в Windows-окружении с cp1251 console падала на странице 22 (как и было у пользователя сегодня перед фиксом).
2. **Каскад ToC явно разделён по уровням** — отлаживать стало проще (видно `toc_source` в meta).
3. **Двухпроходный verify** ловит «найдено внутри ToC» (Release It!) и `out-of-order` после rescue.
4. **Embedding-rescue первым tier** перед LLM-rescue — быстрее.
5. **Меньше fail-кейсов**: parallelnoe от 80+ not_found к ~25, dedup ловит case-insensitive дубли, OCR не падает на cp1251, `exact_normalized` ловит whitespace-разницу.

---

## 6. На чём конкретно застрял сейчас

1. **0e6e53b LLM-clean медленный (1+ час на 35 секций)**. Качество хорошее, скорость — нет. Решение: параллелить LLM clean (см. 3.3.В).
2. **«Об авторе» захватывает копирастический спам** (Do Good Design). Решение: trim known legal blurbs или флаг подозрительности.
3. **Заголовки крупным шрифтом не находятся в теле**. Решение: page-cut fallback (резать по page-hint, не по title).
4. **Скорость на больших книгах (parallelnoe 248 секций)**. Не пробовал — может быть OK, может быть катастрофа в LLM-clean. Стоит замерить.

---

## 7. Что точно надо забрать в текущую реализацию

(уже есть, но проверить что не сломалось)

- Жёсткий промпт `extract_toc_json` против слипания ✅ есть
- CJK retry в `clean_text_fragment` ✅ есть
- Page-hint window ±5K ✅ есть
- Verify in-ToC + verify out-of-order ✅ есть
- TERMINAL_SECTION_MARKERS для приложений/указателей ✅ есть
- `_guess_level` startswith вместо substring ✅ есть
- `_normalize_llm_items` multi-page filter ✅ есть
- response_format hack обход для LM Studio ✅ есть
- `_extract_message_content` reasoning-fallback ✅ есть
