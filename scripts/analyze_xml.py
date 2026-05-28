"""
Анализ XML-результатов pipeline: структура, качество контента, проблемы.

Usage:
    python scripts/analyze_xml.py [xml_dir]
    python scripts/analyze_xml.py test_v2
"""

import sys
import os
import xml.etree.ElementTree as ET
from pathlib import Path
import re

XML_DIR = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("test_v2")

# Паттерны мусора в контенте
GARBAGE_PATTERNS = [
    re.compile(r'[一-鿿㐀-䶿]'),          # CJK
    re.compile(r'[^\x00-\x7fЀ-ӿ̀-ͯ]{5,}'),  # много не-Latin/Cyrillic
    re.compile(r'(\.\s*){5,}'),                             # точки-лидеры ToC
    re.compile(r'^\s*\d{1,4}\s*$', re.MULTILINE),          # строки только из цифр
]

CJK_RE = re.compile(r'[一-鿿㐀-䶿]')
TOC_LINE_RE = re.compile(r'(?:\.\s*){4,}\d{1,4}\s*$|[_\-]{3,}\s*\d{1,4}\s*$')


def check_content(text: str) -> list[str]:
    """Возвращает список замеченных проблем в контенте."""
    issues = []
    if not text:
        return []
    cjk = len(CJK_RE.findall(text))
    if cjk > 0:
        issues.append(f"CJK:{cjk}chars")
    lines = [l for l in text.splitlines() if l.strip()]
    toc_lines = sum(1 for l in lines[:15] if TOC_LINE_RE.search(l))
    if lines and toc_lines / max(len(lines[:15]), 1) > 0.4:
        issues.append(f"TOC-LIKE(первые {min(15,len(lines))} строк)")
    # Слишком короткий контент
    if len(text.strip()) < 80:
        issues.append(f"SHORT({len(text.strip())}chars)")
    return issues


def analyze_xml(path: Path) -> dict:
    try:
        tree = ET.parse(path)
        root = tree.getroot()
    except Exception as e:
        return {"error": str(e), "file": path.name}

    sections = root.findall('.//section')
    total = len(sections)
    not_found = []
    short = []
    cjk_contaminated = []
    toc_like = []
    by_strategy = {}
    content_lengths = []
    low_conf = []
    page_cut = []

    for sec in sections:
        title = sec.get('title', '')
        conf = float(sec.get('confidence', '0'))
        strat = sec.get('match_strategy', '')
        page = sec.get('page', '')
        content = sec.text or ''
        clen = len(content.strip())

        by_strategy[strat] = by_strategy.get(strat, 0) + 1
        content_lengths.append(clen)

        if conf == 0.0:
            not_found.append({'title': title, 'page': page, 'strat': strat})
        elif conf < 0.50:
            low_conf.append({'title': title, 'conf': conf, 'strat': strat})

        if strat == 'page_cut':
            page_cut.append({'title': title, 'page': page, 'len': clen})

        issues = check_content(content)
        if 'CJK' in str(issues):
            cjk_contaminated.append({'title': title, 'issues': issues})
        if 'TOC-LIKE' in str(issues):
            toc_like.append({'title': title, 'issues': issues})
        if 'SHORT' in str(issues) and conf > 0.0:
            short.append({'title': title, 'conf': conf, 'strat': strat, 'len': clen})

    real = sum(1 for l in content_lengths if l > 50)
    avg_len = int(sum(content_lengths) / max(total, 1))
    avg_conf_val = 0.0
    confs = [float(s.get('confidence', '0')) for s in sections]
    if confs:
        avg_conf_val = round(sum(confs) / len(confs), 3)

    return {
        'file': path.name,
        'total': total,
        'real_content': real,
        'not_found': not_found,
        'low_conf': low_conf,
        'page_cut': page_cut,
        'short_sections': short,
        'cjk_contaminated': cjk_contaminated,
        'toc_like_content': toc_like,
        'avg_content_len': avg_len,
        'avg_conf': avg_conf_val,
        'by_strategy': by_strategy,
        'content_lengths': sorted(content_lengths),
    }


def print_report(r: dict):
    if 'error' in r:
        print(f"\n{'='*60}")
        print(f"ERROR: {r['file']}: {r['error']}")
        return

    total = r['total']
    real = r['real_content']
    pct = int(real / max(total, 1) * 100)
    nf = len(r['not_found'])
    cjk = len(r['cjk_contaminated'])
    toc = len(r['toc_like_content'])
    short = len(r['short_sections'])
    pc = len(r['page_cut'])

    status = "✅" if nf == 0 and cjk == 0 and toc == 0 else ("⚠" if nf <= 3 and cjk == 0 else "❌")
    print(f"\n{'='*60}")
    print(f"{status} {r['file']}")
    print(f"   Секций: {real}/{total} ({pct}%)  avg_conf={r['avg_conf']}  avg_content={r['avg_content_len']}chars")
    print(f"   Стратегии: {r['by_strategy']}")

    if r['not_found']:
        print(f"   ❌ НЕ НАЙДЕНО ({nf}):")
        for s in r['not_found']:
            print(f"      p={s['page']:>5}  {s['title'][:60]}  [{s['strat']}]")

    if r['page_cut']:
        print(f"   🔪 page_cut ({pc}) — позиция по номеру страницы:")
        for s in r['page_cut']:
            print(f"      p={s['page']:>5}  len={s['len']:>5}  {s['title'][:55]}")

    if r['low_conf']:
        print(f"   ⚠ Низкая уверенность conf<0.5 ({len(r['low_conf'])}):")
        for s in r['low_conf'][:5]:
            print(f"      conf={s['conf']:.2f}  {s['title'][:55]}  [{s['strat']}]")

    if cjk:
        print(f"   🈶 CJK в контенте ({cjk}):")
        for s in r['cjk_contaminated'][:3]:
            print(f"      {s['title'][:55]}  {s['issues']}")

    if toc:
        print(f"   📋 ToC-подобный контент ({toc}):")
        for s in r['toc_like_content'][:3]:
            print(f"      {s['title'][:55]}  {s['issues']}")

    if short:
        print(f"   📭 Пустые/короткие секции с контентом ({short}):")
        for s in r['short_sections'][:5]:
            print(f"      conf={s['conf']:.2f}  len={s['len']}  {s['title'][:55]}  [{s['strat']}]")


def print_samples(path: Path, max_sections: int = 3, max_content: int = 400):
    """Выводит примеры контента первых нескольких секций."""
    try:
        tree = ET.parse(path)
        root = tree.getroot()
    except:
        return

    sections = root.findall('.//section')
    # Выбираем секции с реальным контентом
    with_content = [s for s in sections if len(s.text or '') > 100][:max_sections]
    if not with_content:
        return

    print(f"\n  --- Примеры контента ({path.name}) ---")
    for sec in with_content:
        title = sec.get('title', '')
        conf = sec.get('confidence', '')
        strat = sec.get('match_strategy', '')
        content = (sec.text or '').strip()[:max_content]
        print(f"  [{conf} {strat}] {title[:50]}")
        print(f"  {content[:300]!r}")
        print()


def main():
    xml_files = sorted(XML_DIR.glob("*.xml"))
    if not xml_files:
        print(f"Нет XML файлов в {XML_DIR}")
        return

    print(f"Анализ {len(xml_files)} файлов из {XML_DIR}/\n")

    results = [analyze_xml(f) for f in xml_files]

    print("\n" + "="*60)
    print("СВОДНАЯ ТАБЛИЦА")
    print("="*60)
    print(f"{'Файл':<45} {'Секц':>5} {'Real':>5} {'NF':>4} {'pc':>4} {'CJK':>4} {'ToC':>4} {'avgL':>6}")
    print("-"*80)
    for r in results:
        if 'error' in r:
            print(f"{'ERROR: '+r['file']:<45}")
            continue
        nf = len(r['not_found'])
        pc = len(r['page_cut'])
        cjk = len(r['cjk_contaminated'])
        toc = len(r['toc_like_content'])
        flag = "✅" if nf == 0 and cjk == 0 and toc == 0 else ("⚠" if nf <= 3 else "❌")
        fname = r['file'][:44]
        print(f"{flag} {fname:<44} {r['total']:>5} {r['real_content']:>5} {nf:>4} {pc:>4} {cjk:>4} {toc:>4} {r['avg_content_len']:>6}")

    # Детальный отчёт по каждой книге
    for r in results:
        print_report(r)

    # Примеры контента для книг с проблемами
    print("\n\n" + "="*60)
    print("ПРИМЕРЫ КОНТЕНТА (первые секции с контентом по каждой книге)")
    print("="*60)
    for f in xml_files:
        print_samples(f, max_sections=2, max_content=300)


if __name__ == "__main__":
    main()
