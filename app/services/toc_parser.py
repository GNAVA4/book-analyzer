import re


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
                if self.item_pattern.match(line_raw) or self.item_pattern_start.match(
                        line_raw) or self.structure_start.match(line_raw):
                    toc_started = True
                else:
                    continue

            # --- 2. ПРОВЕРКА НА ВЫХОД (Конец оглавления) ---
            match_strict = self.item_pattern.match(line_raw)
            match_start = self.item_pattern_start.match(line_raw)
            match_loose = self.loose_item_pattern.match(line_raw)
            has_page = bool(match_strict or match_start or match_loose)

            if not has_page and self._is_content_start(norm_line, seen_titles):
                break

            if len(line_raw) < 50 and any(m in norm_line for m in self.header_markers):
                continue

            if len(line_raw) > 300:
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
                        full_title = pending_title + " " + title_part
                        self._add_node(root, current_chapter, full_title, pending_level, page_part)
                        if pending_level == 1 and root.children: current_chapter = root.children[-1]
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
                    self._add_node(root, current_chapter, pending_title, pending_level, line_raw)
                    if pending_level == 1 and root.children: current_chapter = root.children[-1]
                    seen_titles.add(self._normalize(pending_title))
                    pending_title = ""
                elif len(pending_title + line_raw) < 300:
                    pending_title += " " + line_raw
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
        else:
            root.add_child(node)
        return node

    def _guess_level(self, text):
        t = text.lower()
        if any(w in t for w in
               ['глава', 'chapter', 'часть', 'раздел', 'введение', 'заключение', 'об авторе', 'предисловие',
                'благодарности']):
            return 1
        if re.match(r'^\s*\d+\.\s+[А-ЯA-Z]', text):
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
    if node.title != "Root":
        p = int(node.page) if str(node.page).isdigit() else None
        sequence.append({"title": node.title, "level": node.level, "page": p})
    for child in node.children:
        sequence.extend(toc_to_linear_sequence(child))

    for i in range(len(sequence)):
        if sequence[i]['page'] is None and i + 1 < len(sequence):
            sequence[i]['page'] = sequence[i + 1]['page']
    return sequence