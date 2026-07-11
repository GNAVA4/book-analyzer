import fitz, pathlib, glob, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import xml.etree.ElementTree as ET
from app.services.toc_parser import HeuristicParser, toc_to_linear_sequence
from app.services.toc_builder import _split_sticky_toc_lines

xmls = {p.split('\\')[-1]: p for p in glob.glob('test_v2/*.xml')}
print('%-42s %7s %8s' % ('book', 'OLD_nav', 'NEW_heur'))
for pdf in sorted(pathlib.Path('test').glob('*.pdf')):
    stem = pdf.stem
    xml = None
    for k, v in xmls.items():
        if k[:18] == stem[:18] or stem[:18] in k:
            xml = v; break
    old = '-'
    if xml:
        old = len(ET.parse(xml).getroot().findall('.//NavigationTable/Item'))
    d = fitz.open(pdf)
    raw = ''.join(d[i].get_text() + '\n' for i in range(min(20, len(d))))
    new = len(toc_to_linear_sequence(HeuristicParser().parse_toc(_split_sticky_toc_lines(raw))))
    d.close()
    flag = ''
    if not isinstance(old, str):
        if new < old - 1: flag = '  FEWER'
        elif new > old + 1: flag = '  +%d' % (new - old)
    print('%-42s %7s %8s%s' % (stem[:42], old, new, flag))
    sys.stdout.flush()
