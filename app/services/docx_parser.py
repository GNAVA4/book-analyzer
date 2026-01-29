import docx
from docx.oxml.text.paragraph import CT_P
from docx.oxml.table import CT_Tbl
from docx.table import Table
from docx.text.paragraph import Paragraph
from lxml import etree
from app.models import BookNode

# Пространства имен
NAMESPACES = {
    'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main',
    'm': 'http://schemas.openxmlformats.org/officeDocument/2006/math'
}


def recurse_omml(element):
    """
    Рекурсивно обходит структуру формулы OMML и превращает теги в символы.
    """
    tag = element.tag
    # Убираем пространство имен для удобства проверки (например {http...}f -> f)
    local_tag = tag.split('}')[-1] if '}' in tag else tag

    # 1. Текст внутри формулы (переменные, цифры)
    if local_tag == 't':
        return element.text if element.text else ""

    # 2. Дробь (Fraction) -> (числитель / знаменатель)
    if local_tag == 'f':
        num_node = element.find('.//m:num', NAMESPACES)
        den_node = element.find('.//m:den', NAMESPACES)
        num_text = recurse_omml(num_node) if num_node is not None else ""
        den_text = recurse_omml(den_node) if den_node is not None else ""
        return f"({num_text}/{den_text})"

    # 3. Корень (Radical) -> √(база)
    if local_tag == 'rad':
        # m:deg - это степень корня (например, 3 для кубического), она опциональна
        deg_node = element.find('.//m:deg', NAMESPACES)
        base_node = element.find('.//m:e', NAMESPACES)

        base_text = recurse_omml(base_node) if base_node is not None else ""

        # Если есть степень корня (например кубический)
        deg_text = ""
        if deg_node is not None:
            # Проверяем, есть ли текст внутри узла степени (иногда он пуст)
            raw_deg = "".join([recurse_omml(c) for c in deg_node])
            if raw_deg:
                deg_text = f"^{raw_deg}"

        return f"√{deg_text}({base_text})"

    # 4. Верхний индекс / Степень (Superscript) -> база^степень
    if local_tag == 'sSup':
        base_node = element.find('.//m:e', NAMESPACES)
        sup_node = element.find('.//m:sup', NAMESPACES)
        base_text = recurse_omml(base_node) if base_node is not None else ""
        sup_text = recurse_omml(sup_node) if sup_node is not None else ""
        return f"{base_text}^({sup_text})"

    # 5. Нижний индекс (Subscript) -> база_индекс
    if local_tag == 'sSub':
        base_node = element.find('.//m:e', NAMESPACES)
        sub_node = element.find('.//m:sub', NAMESPACES)
        base_text = recurse_omml(base_node) if base_node is not None else ""
        sub_text = recurse_omml(sub_node) if sub_node is not None else ""
        return f"{base_text}_({sub_text})"

    # 6. Индекс сверху и снизу (SubSup) -> база_индекс^степень
    if local_tag == 'sSubSup':
        base_node = element.find('.//m:e', NAMESPACES)
        sub_node = element.find('.//m:sub', NAMESPACES)
        sup_node = element.find('.//m:sup', NAMESPACES)
        base_text = recurse_omml(base_node) if base_node is not None else ""
        sub_text = recurse_omml(sub_node) if sub_node is not None else ""
        sup_text = recurse_omml(sup_node) if sup_node is not None else ""
        return f"{base_text}_({sub_text})^({sup_text})"

    # Если тег не специальный (например, просто контейнер m:oMath, m:r),
    # просто собираем текст со всех его детей
    result = ""
    for child in element:
        result += recurse_omml(child)

    return result


def get_paragraph_text_with_math(para_element):
    """
    Извлекает текст из параграфа, преобразуя формулы в читаемый вид.
    """
    text = ""
    for child in para_element:
        # 1. Обычный текст (Run)
        if child.tag.endswith('r'):
            t_tags = child.findall('.//w:t', NAMESPACES)
            for t in t_tags:
                text += t.text if t.text else ""

        # 2. Формулы (oMath или oMathPara)
        elif child.tag.endswith('oMath') or child.tag.endswith('oMathPara'):
            # Запускаем рекурсивный парсер для формулы
            formula_str = recurse_omml(child)
            if formula_str:
                # Добавляем пробелы, чтобы формула не слиплась с текстом
                text += f" {formula_str} "

    return text.strip()


def check_page_breaks(element, current_page):
    """
    Проверяет XML элемента на наличие маркеров разрыва страницы.
    """
    xml_str = etree.tostring(element, encoding='unicode')
    # <w:br w:type="page"/>
    hard_breaks = xml_str.count('type="page"')
    # <w:lastRenderedPageBreak/>
    soft_breaks = xml_str.count('lastRenderedPageBreak')
    return current_page + hard_breaks + soft_breaks


def parse_docx(file_path) -> list[BookNode]:
    doc = docx.Document(file_path)
    nodes = []
    nodes.append({"title": "ROOT", "content": "", "level": 0, "page": 1})
    current_page = 1

    if doc.element.body is not None:
        body_elements = doc.element.body
    else:
        return nodes

    for element in body_elements:
        current_page = check_page_breaks(element, current_page)

        if isinstance(element, CT_P):
            para = Paragraph(element, doc)
            text = get_paragraph_text_with_math(element)

            if not text:
                continue

            style_name = para.style.name.lower() if para.style else ""

            if 'heading' in style_name or 'заголовок' in style_name:
                try:
                    level_str = ''.join(filter(str.isdigit, style_name))
                    level = int(level_str) if level_str else 1
                except:
                    level = 1

                nodes.append({
                    "title": text,
                    "content": "",
                    "level": level,
                    "page": current_page
                })
            else:
                if nodes:
                    nodes[-1]["content"] += text + "\n"

        elif isinstance(element, CT_Tbl):
            tbl = Table(element, doc)
            table_text = "\n[ТАБЛИЦА]\n"
            try:
                for row in tbl.rows:
                    row_data = [cell.text.strip() for cell in row.cells]
                    table_text += " | ".join(row_data) + "\n"
            except:
                pass

            if nodes:
                nodes[-1]["content"] += table_text

    return nodes