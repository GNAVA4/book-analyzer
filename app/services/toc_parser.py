import re


# Паттерны "мусорных" пунктов ToC, которые встречаются на титульных страницах:
# копирайт, выходные данные, ISBN — не являются разделами книги.
_TOC_GARBAGE_PATTERNS = [
    re.compile(r'^©', re.IGNORECASE),
    re.compile(r'^\s*ISBN\b', re.IGNORECASE),
    re.compile(r'^\s*ББК\b', re.IGNORECASE),
    re.compile(r'^\s*УДК\b', re.IGNORECASE),
    re.compile(r'^\s*[a-zA-Zа-яА-Я]{1,2}\s*$'),   # одна-две буквы
    re.compile(r'^\s*\d+\s*$'),                     # только цифры
    re.compile(r'^[\W_]+$'),                        # только не-буквенные символы
]


def _is_garbage_toc_item(title: str) -> bool:
    """True если пункт ToC выглядит как титульный мусор (©, ББК, ISBN, и т.п.)."""
    if not title or len(title.strip()) < 3:
        return True
    t = title.strip()
    return any(p.match(t) for p in _TOC_GARBAGE_PATTERNS)


class TocNode:
    def __init__(self, title, level, page=None):
        self.title = title
        self.level = level
        self.page = page
        self.children = []

    def add_child(self, node):
        self.children.append(node)


class HeuristicParser:
    def __init__(self):
        self.header_markers = ['оглавление', 'содержание', 'contents', 'table of contents']
        # Маркеры, после которых ToC заканчивается — это списки рисунков/таблиц
        # с сотнями подписей-нелистинговых заголовков (типичная проблема MIL-STD).
        self.toc_terminator_markers = [
            'list of figures', 'list of tables', 'list of illustrations',
            'список рисунков', 'список таблиц', 'список иллюстраций',
            'figures', 'tables',  # одиночные слова — рискованно, но в контексте ToC ОК
        ]

        # 1. ПАТТЕРН ПУНКТА С НОМЕРОМ СТРАНИЦЫ
        self.item_pattern = re.compile(
            r'^(.+?)(?:\.{2,}|(?:\.[\s\t]+){2,}|\…|\t+|\s{3,}|_{2,})(.*?)(\d+)$'
        )

        # 2. НОВЫЙ ПАТТЕРН: Номер Название (6 Об авторе)
        self.item_pattern_start = re.compile(r'^(\d+)\s+([А-ЯA-Z].+)$')

        # 3. СЛАБЫЙ ПАТТЕРН
        self.loose_item_pattern = re.compile(
            r'^((?:Глава|Chapter|Часть|Раздел|§|[IVXLCDM]+\.|[0-9]+(?:\.[0-9]+)*\.?).+?)(\s+|\t+)(\d+)$',
            re.IGNORECASE
        )

        # 4. НАЧАЛО СТРУКТУРЫ
        self.structure_start = re.compile(
            r'^\s*(Глава|Chapter|Часть|Part|Раздел|§|Введение|Предисловие|Заключение|Об авторе|Благодарности|' +
            r'Приложения|Примечания|Литература|Библиография|Указатель|' +
            r'[IVXLCDM]+\.|[0-9]+(?:\.[0-9]+)+\.?|[0-9]+\.)(?:\s*|(?=[А-ЯA-Z]))',
            re.IGNORECASE
        )

    def parse_toc(self, text: str) -> TocNode:
        lines = text.split('\n')
        root = TocNode("Root", 0)

        current_chapter = None
        pending_title = ""
        pending_level = 0

        seen_titles = set()
        toc_started = False
        misses = 0
        MAX_MISSES = 50

        clean_lines = self._preprocess_lines(lines)

        for line in clean_lines:
            line_raw = line.strip()
            if not line_raw: continue

            norm_line = self._normalize(line_raw)

            # --- 1. ПОИСК СТАРТА ---
            if not toc_started:
                if 'краткое' in norm_line: continue
                if len(line_raw) < 50 and (
                        any(m in norm_line for m in self.header_markers) or line_raw.upper() == "ВВЕДЕНИЕ"):
                    toc_started = True
                    if line_raw.upper() == "ВВЕДЕНИЕ":
                        self._add_node(root, current_chapter, line_raw, 1, None)
                        seen_titles.add(self._normalize(line_raw))
                    continue
                # Без явного маркера «оглавление/contents» стартуем ToC только при
                # надёжных паттернах (с лидерами точек или короткий «N Название»).
                # Голый structure_start не запускает ToC — иначе FOREWORD-параграфы
                # вида «1. This standard...» ловятся как пункты содержания.
                if self.item_pattern.match(line_raw):
                    toc_started = True
                elif self.item_pattern_start.match(line_raw) and len(line_raw) < 100:
                    toc_started = True
                else:
                    continue

            # --- 2. ПРОВЕРКА НА ВЫХОД (Конец оглавления) ---
            match_strict = self.item_pattern.match(line_raw)
            match_start = self.item_pattern_start.match(line_raw)
            match_loose = self.loose_item_pattern.match(line_raw)
            has_page = bool(match_strict or match_start or match_loose)

            # Терминатор: после "LIST OF FIGURES" / "СПИСОК ТАБЛИЦ" идут сотни
            # подписей-нелистинговых заголовков. Это засоряет ToC (MIL-STD случай).
            line_low = line_raw.lower().strip()
            if len(line_raw) < 60 and any(m in line_low for m in self.toc_terminator_markers):
                # Сбрасываем pending перед выходом, чтобы не добавить мусор
                pending_title = ""
                break

            if not has_page and self._is_content_start(norm_line, seen_titles):
                break

            if len(line_raw) < 50 and any(m in norm_line for m in self.header_markers):
                continue

            # Длинные строки без page-маркеров — почти наверняка параграф, не пункт ToC
            if len(line_raw) > 200 and not has_page:
                pending_title = ""
                misses += 1
                if misses > MAX_MISSES: break
                continue

            # --- СЦЕНАРИЙ А: ЕСТЬ СТРАНИЦА ---
            if has_page:
                if match_strict:
                    title_part, page_part = match_strict.group(1).strip(), match_strict.group(3)
                elif match_start:
                    page_part, title_part = match_start.group(1), match_start.group(2).strip()
                else:
                    title_part, page_part = match_loose.group(1).strip(), match_loose.group(3)

                if pending_title:
                    if not self.structure_start.match(title_part):
                        # Пересчитываем level от ПОЛНОГО заголовка, а не от обрывка
                        # «3.» / «1.» которые без названия неверно классифицируются.
                        full_title = pending_title + " " + title_part
                        full_level = self._guess_level(full_title)
                        if not current_chapter: full_level = 1
                        self._add_node(root, current_chapter, full_title, full_level, page_part)
                        if full_level == 1 and root.children: current_chapter = root.children[-1]
                        seen_titles.add(self._normalize(full_title))
                        pending_title = ""
                        misses = 0
                        continue
                    else:
                        prev = self._add_node(root, current_chapter, pending_title, pending_level, None)
                        if pending_level == 1: current_chapter = prev
                        seen_titles.add(self._normalize(pending_title))
                        pending_title = ""

                level = self._guess_level(title_part)
                if not current_chapter: level = 1
                new_node = self._add_node(root, current_chapter, title_part, level, page_part)
                if level == 1: current_chapter = new_node
                seen_titles.add(self._normalize(title_part))
                misses = 0
                continue

            # --- СЦЕНАРИЙ Б: ЗАГОЛОВОК БЕЗ СТРАНИЦЫ ---
            if self.structure_start.match(line_raw):
                if pending_title:
                    prev = self._add_node(root, current_chapter, pending_title, pending_level, None)
                    if pending_level == 1: current_chapter = prev
                    seen_titles.add(self._normalize(pending_title))

                # Разделение слипшихся 3.1Пакет
                parts = re.split(r'(?<=[а-яА-Яa-zA-Z])\s+(?=\d+\.\d+)', line_raw)
                if len(parts) > 1:
                    for p in parts[:-1]:
                        node = self._add_node(root, current_chapter, p, self._guess_level(p), None)
                        if self._guess_level(p) == 1: current_chapter = node
                        seen_titles.add(self._normalize(p))
                    pending_title = parts[-1]
                else:
                    pending_title = line_raw

                pending_level = self._guess_level(pending_title)
                misses = 0
                continue

            # --- СЦЕНАРИЙ В: ТЕКСТ (ХВОСТ) ---
            if pending_title:
                if re.match(r'^\d+$', line_raw):
                    # Финальный заголовок — пересчитать level от полного текста
                    final_level = self._guess_level(pending_title)
                    if not current_chapter: final_level = 1
                    self._add_node(root, current_chapter, pending_title, final_level, line_raw)
                    if final_level == 1 and root.children: current_chapter = root.children[-1]
                    seen_titles.add(self._normalize(pending_title))
                    pending_title = ""
                elif len(pending_title + line_raw) < 300:
                    pending_title += " " + line_raw
                    # Пересчитываем level — он мог быть установлен по обрывку «3.»
                    pending_level = self._guess_level(pending_title)
                else:
                    pending_title = ""
                misses = 0
            else:
                misses += 1
                if misses > MAX_MISSES: break

        if pending_title:
            self._add_node(root, current_chapter, pending_title, pending_level, None)
            seen_titles.add(self._normalize(pending_title))

        return root

    def _add_node(self, root, current_chapter, title, level, page):
        title = re.sub(r'^(\d+\.)([А-ЯA-Z])', r'\1 \2', title)
        title = re.sub(r'([._\s\t\…]*){2,}$', '', title).strip()
        title = title.strip('\t')
        node = TocNode(title, level, page)
        if level == 2 and current_chapter:
            current_chapter.add_child(node)
        elif level >= 3 and current_chapter:
            # Вложить в последний level-2 узел текущей главы; если его нет —
            # прикрепить прямо к главе (деградация до level-2).
            if current_chapter.children:
                current_chapter.children[-1].add_child(node)
            else:
                current_chapter.add_child(node)
        else:
            root.add_child(node)
        return node

    def _guess_level(self, text):
        # Числовые паттерны проверяем первыми — они точнее ключевых слов.
        if re.match(r'^\s*\d+\.\d+\.\d+', text):
            return 3
        # Голая нумерация без названия («1.», «3.», «II.») — главный раздел.
        # Это типично для PDF с колоночной версткой ToC, где «3.» и «DEFINITIONS»
        # извлекаются как отдельные строки и parser склеивает их через pending.
        if re.match(r'^\s*\d+\.?\s*$', text) or re.match(r'^\s*[IVXLCDM]+\.?\s*$', text):
            return 1
        if re.match(r'^\s*\d+\.\s+[А-ЯA-Z]', text):
            return 1
        # Ключевые слова — только в начале строки (startswith), чтобы «Подраздел»
        # не давал level=1 из-за подстроки «раздел».
        t = text.strip().lower()
        if any(t.startswith(w) for w in
               ['глава', 'chapter', 'часть', 'раздел', 'введение', 'заключение', 'об авторе', 'предисловие',
                'благодарности']):
            return 1
        return 2

    def _preprocess_lines(self, lines):
        cleaned = []
        for line in lines:
            line = line.strip()
            if not line: continue
            line = re.sub(r'^\d+\s+(?=(Глава|Chapter|§|[0-9]+\.|[A-Za-zА-Яа-я]))', '', line)
            cleaned.append(line)
        return cleaned

    def _normalize(self, text):
        return re.sub(r'[\W_]+', '', text).lower()

    def _is_content_start(self, norm_line, seen_titles):
        if len(norm_line) < 10: return False
        for s in seen_titles:
            if len(s) > 10 and norm_line.startswith(s):
                return True
        return False


# ЭТА ФУНКЦИЯ ДОЛЖНА БЫТЬ ЗДЕСЬ (ДЛЯ ИСПРАВЛЕНИЯ IMPORT ERROR)
def toc_to_linear_sequence(node: TocNode) -> list:
    sequence = []
    if node.title != "Root" and not _is_garbage_toc_item(node.title):
        p = int(node.page) if str(node.page).isdigit() else None
        sequence.append({"title": node.title, "level": node.level, "page": p})
    for child in node.children:
        sequence.extend(toc_to_linear_sequence(child))

    for i in range(len(sequence)):
        if sequence[i]['page'] is None and i + 1 < len(sequence):
            sequence[i]['page'] = sequence[i + 1]['page']
    return sequence