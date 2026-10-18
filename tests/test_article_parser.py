"""Тесты правил парсера статей (app/services/article_parser.py) на синтетических строках."""
from app.services.article_parser import (
    Line, Para, _find_title_idx, _join, _plausible_heading, _repair_text, _RUNIN_KNOWN,
    _column_order, _unlabeled_refs_start, assign_zones, build_paragraphs, mark_noise, split_articles,
)


def L(text, page=0, y=100.0, x0=50.0, x1=500.0, size=12.0, bold=False, caps=False, lead=''):
    return Line(page=page, text=text, size=size, bold=bold, caps=caps,
                x0=x0, x1=x1, y0=y, y1=y + size, lead_bold=lead)


GEO = {0: (600, 800), 1: (600, 800), 2: (600, 800)}


class TestJoin:
    def test_hyphenated_word_is_glued(self):
        assert _join('создание ис-', 'кусственного интеллекта') == 'создание искусственного интеллекта'

    def test_dash_before_capital_keeps_space(self):
        assert _join('Москва -', 'Петербург') == 'Москва - Петербург'


class TestPlausibleHeading:
    def test_section_title(self):
        assert _plausible_heading('Понимание смысла образов', 1)

    def test_caption_rejected(self):
        assert not _plausible_heading('Рисунок 1 - Смысл как пирамида контекстов', 1)

    def test_list_item_rejected(self):
        assert not _plausible_heading('1) рефлексный,', 1)

    def test_bold_sentence_rejected(self):
        s = 'Основное положение состоит в том, что смысл представляет собой многоуровневое описание.'
        assert not _plausible_heading(s, 2)

    def test_lowercase_continuation_rejected(self):
        assert not _plausible_heading('интеллекта введены 3 уровня исследований [1]:', 1)


class TestRepair:
    def test_cp1251_mojibake_fixed_inside_mixed_line(self):
        assert _repair_text('замыкание Γ∼(xi è îáðàòíîå íå÷åòêîå') == 'замыкание Γ∼(xi и обратное нечеткое'

    def test_letter_spacing_collapsed(self):
        assert _repair_text('П р и м е р 1') == 'Пример1'

    def test_formula_rejected_as_heading(self):
        assert not _plausible_heading('F :=NW+NB+W', 1)
        assert not _plausible_heading('4 4 η η 3 η η 2 η η 1 ( , ) ( , )', 1)


def TP(text, caps=False, size=12.0):
    p = Para(lines=[L(text, size=size, caps=caps)], text=text, size=size, caps=caps)
    return p


class TestNoKeywordsJournal:
    """Старый журнал: хвост чужой статьи / УДК / авторы / НАЗВАНИЕ / текст, без «Ключевых слов»."""
    def paras(self):
        return [
            TP('14. Курейчик В.В. Концепция эволюционных вычислений // Известия ЮФУ. – 2009. – С. 7-15.'),
            TP('Бублей Сергей Евгеньевич аспирант кафедры систем управления'),
            TP('УДК 004.89'),
            TP('А.В. Козоброд, В.Е. Мешков'),
            TP('АНАЛИЗ АРХИТЕКТУР ГИБРИДНЫХ НЕЙРОСЕТЕВЫХ МОДЕЛЕЙ', caps=True),
            TP('Текст статьи ' * 30),
            TP('Продолжение текста ' * 30),
            TP('1. Мешкова Е.В. Методика построения классификатора // Известия ЮФУ. – 2010. – С. 1-5.'),
            TP('2. Курейчик В.М. Генетические алгоритмы. – М.: Физматлит, 2006.'),
        ]

    def test_title_found_next_to_authors(self):
        assert _find_title_idx(self.paras(), 12.0) == 4

    def test_foreign_tail_cut_before_udk(self):
        assert split_articles(self.paras(), 12.0)[0][0] == 2

    def test_references_without_heading(self):
        assert _unlabeled_refs_start(self.paras(), 5) == 7


class TestColumns:
    @staticmethod
    def row(y, *xs):
        return [((x0, y, x1, y + 10), [{'text': f'{x0}'}]) for x0, x1 in xs]

    def test_two_columns_read_left_then_right(self):
        rows = [self.row(50, (50, 550))]                                  # название во всю ширину
        rows += [self.row(100 + 12 * i, (50, 290), (310, 550)) for i in range(10)]
        order = _column_order(rows, 600)
        xs = [part[0][0][0] for part in order]
        assert xs == [50] + [50] * 10 + [310] * 10

    def test_columns_below_full_width_header(self):
        # 1-я страница: колонтитул (тоже «разделён»), 20 строк шапки во всю ширину, потом 10 строк колонок
        rows = [self.row(30, (50, 200), (400, 550))]
        rows += [self.row(50 + 12 * i, (50, 550)) for i in range(20)]
        rows += [self.row(300 + 12 * i, (50, 290), (310, 550)) for i in range(10)]
        order = _column_order(rows, 600)
        tail = [part[0][0][0] for part in order][-20:]
        assert tail == [50] * 10 + [310] * 10

    def test_justified_single_column_untouched(self):
        # выключка: слова — отдельные фрагменты, пробелы в случайных местах
        import random
        rnd = random.Random(1)
        rows = []
        for i in range(20):
            x, row = 50, []
            while x < 540:
                w = rnd.randint(30, 90)
                row.append(((x, 100 + 12 * i, x + w, 110 + 12 * i), [{'text': 'w'}]))
                x += w + 5
            rows.append(row)
        assert _column_order(rows, 600) is rows


class TestTocAndLists:
    def test_toc_dropped_and_list_items_joined(self):
        paras = [
            TP('СОДЕРЖАНИЕ'),
            TP('ВВЕДЕНИЕ ............................................ 2'),
            TP('Цель работы — провести анализ. Задачи работы:'),
            TP('1) изучить литературу;'),
            TP('2) собрать данные.'),
            TP('Основной текст работы продолжается здесь. ' * 10),
        ]
        res = assign_zones(paras, 12.0)
        body = [t for s in res['sections'] for t in s['paragraphs']]
        assert body[0] == 'Цель работы — провести анализ. Задачи работы:\n1) изучить литературу;\n2) собрать данные.'
        assert not any('....' in t for t in body) and len(body) == 2


class TestTrailingHeader:
    """«Доклады АМАН»: аннотация и ключевые слова ПОСЛЕ литературы — тело не уходит в шапку."""
    def paras(self):
        return [
            TP('УДК 517.95'),
            TP('О единственности решения задачи Дирихле', size=14.3),
            TP('Масаева О.Х.'),
            TP('Рассмотрим уравнение второго порядка с дробной производной. ' * 10),
            TP('В данной работе доказывается единственность решения задачи. ' * 10),
            TP('ЛИТЕРАТУРА'),
            TP('1. Нахушев А.М. Дробное исчисление и его применение. – М.: Физматлит, 2003.'),
            TP('ABSTRACT'),
            TP('The uniqueness of the solution of the Dirichlet problem is proved.'),
            TP('АННОТАЦИЯ'),
            TP('Доказана единственность решения задачи Дирихле.'),
            TP('Ключевые слова: задача Дирихле, дробная производная.'),
        ]

    def test_body_and_tail_meta(self):
        res = assign_zones(self.paras(), 12.0)
        assert res['meta']['title'] == 'О единственности решения задачи Дирихле'
        assert len(res['sections']) == 1 and len(res['sections'][0]['paragraphs']) == 2
        assert 'единственность решения задачи Дирихле' in res['meta']['abstract']
        assert res['meta']['keywords'] == ['задача Дирихле', 'дробная производная']
        assert len(res['meta']['references']) == 1     # англ. аннотация в литературу не попала


class TestRunIn:
    def test_known_runin(self):
        m = _RUNIN_KNOWN.match('Введение. Сахарный диабет входит в число')
        assert m and m.group(0).strip().rstrip('.') == 'Введение'

    def test_word_inside_sentence_not_runin(self):
        assert not _RUNIN_KNOWN.match('Введение в теорию миваров дано в [3]')


class TestNoise:
    def test_running_header_removed_but_same_text_in_body_kept(self):
        lines = [
            L('О.О. ВАРЛАМОВ', page=0, y=200),                      # автор в теле 1-й стр.
            L('О.О. ВАРЛАМОВ', page=1, y=30), L('Текст', page=1, y=300),
            L('О.О. ВАРЛАМОВ', page=2, y=30), L('Текст', page=2, y=300),
            L('57', page=2, y=780),
        ]
        mark_noise(lines, GEO)
        assert [l.noise for l in lines] == [False, True, False, True, False, True]


class TestParagraphs:
    def test_hanging_indent_list_is_one_paragraph(self):
        # пункт списка: первая строка x=57, продолжение x=72 — висячий отступ, не новые абзацы
        lines = [L('(1) Поиск изображений по образцу. Например, существует', x0=57, y=100),
                 L('проект IRMA — совместный проект нескольких', x0=72, y=112),
                 L('департаментов университета Ахена и так далее.', x0=72, y=124)]
        paras, _ = build_paragraphs(lines, GEO)
        assert len(paras) == 1

    def test_first_line_indent_starts_paragraph(self):
        lines = [L('Конец предыдущего абзаца, который занимает всю строку до поля', y=100),
                 L('и продолжается здесь до самого конца строки тоже целиком.', y=112),
                 L('Новый абзац начинается с отступа и идёт до конца строки', x0=70, y=124),
                 L('а дальше возвращается к левому полю, как обычно в тексте.', y=136)]
        paras, _ = build_paragraphs(lines, GEO)
        assert len(paras) == 2

    def test_anchor_word_breaks_paragraph(self):
        lines = [L('Воронеж, Российская Федерация', x0=200, y=100),
                 L('Резюме. Для создания машиностроительного интеллекта применяют', y=112),
                 L('миварные технологии логического искусственного интеллекта.', y=124)]
        paras, _ = build_paragraphs(lines, GEO)
        assert paras[-1].text.startswith('Резюме')


def P(text, heading=False, runin=''):
    p = Para(lines=[L(text)], text=text, heading=heading, runin_title=runin)
    return p


class TestZones:
    def test_front_body_refs(self):
        paras = [
            P('УДК 004.8'),
            P('О МИВАРНОМ ПОДХОДЕ К МОДЕЛИРОВАНИЮ', heading=True),
            P('Варламов О.О., Адамова Л.Е.'),
            P('Аннотация. Целью исследований является создание интеллекта.'),
            P('Ключевые слова: мивар; миварные сети; логический вывод'),
            P('Введение', heading=True),
            P('Текст введения ' * 20),
            P('Список литературы', heading=True),
        ]
        paras[-1].lines = [L('Список литературы')]
        refs = [P('[1] Варламов О.О. Первая работа'), P('[2] Вторая работа //')]
        refs[1].lines = [L('[2] Вторая работа // Вестник ТГТУ. - 2011. - Т.17. - №'),
                         L('3. - С.687-694.')]           # «3.» — не новый пункт (номер не по порядку)
        res = assign_zones(paras + refs, 12.0)
        m = res['meta']
        assert m['udk'] == 'УДК 004.8'
        assert m['title'] == 'О МИВАРНОМ ПОДХОДЕ К МОДЕЛИРОВАНИЮ'
        assert m['authors'] == ['Варламов О.О.', 'Адамова Л.Е.']
        assert m['abstract'].startswith('Целью')
        assert m['keywords'] == ['мивар', 'миварные сети', 'логический вывод']
        assert len(m['references']) == 2 and m['references'][1].endswith('С.687-694.')
        assert [s['title'] for s in res['sections']] == ['Введение']


def test_inline_hyphen_joined():
    from app.services.article_parser import _INLINE_HYPHEN
    assert _INLINE_HYPHEN.sub('', 'модель конфигу- рации и маршру- тизаторы') == 'модель конфигурации и маршрутизаторы'
    assert _INLINE_HYPHEN.sub('', 'метод A- B и Москва - Петербург') == 'метод A- B и Москва - Петербург'
