"""Тесты смыслового слоя (app/analysis/semantic.py) без LM Studio: синтетические векторы."""
import numpy as np

from app.analysis.semantic import coverage, is_formula, make_chunks, novelty, redundant_pairs


def unit(v):
    v = np.array(v, dtype=np.float32)
    return v / np.linalg.norm(v)


class TestFormula:
    def test_prose_is_not_formula(self):
        assert not is_formula('Миварный подход объединяет базы данных и логический вывод в одну технологию.')

    def test_broken_formula_is_formula(self):
        assert is_formula('1. ξ ≠ 0: =ζ ′ ∂+η ∂ ⎡ ′−η ζ +ζ⎤ + ⎡ ′ ′−η ζ +ηζ⎤ − ⎡ ′−η ζ')

    def test_number_table_is_formula(self):
        assert is_formula('A 0 0,8 0001 B 0,7 0 0,5 0 0 0,8 C000 0,4 0,8 0,5 D0000 0,4')


class TestChunks:
    def test_short_paragraphs_merged_long_split(self):
        long_para = ('Это предложение средней длины для проверки нарезки. ' * 60).strip()
        parsed = {'meta': {'abstract': 'Аннотация статьи.'},
                  'sections': [{'title': 'Введение', 'paragraphs': ['Коротко.'] * 3 + ['Текст ' * 80]},
                               {'title': 'Метод', 'paragraphs': [long_para]}]}
        ch = make_chunks(parsed)
        assert ch[0]['abstract'] and ch[0]['section'] == 'Аннотация'
        intro = [c for c in ch if c['section'] == 'Введение']
        assert len(intro) == 1 and intro[0]['text'].startswith('Коротко.')
        method = [c for c in ch if c['section'] == 'Метод']
        assert len(method) >= 2 and all(len(c['text']) <= 1500 for c in method)


class TestMetrics:
    def test_coverage_and_novelty(self):
        a = np.vstack([unit([1, 0, 0]), unit([0, 1, 0])])
        b = np.vstack([unit([1, 0, 0])])
        assert abs(coverage(a, b) - 0.5) < 1e-6            # одна мысль из двух есть в B
        nov = novelty(a, b)
        assert abs(nov[0]) < 1e-6 and abs(nov[1] - 1.0) < 1e-6

    def test_redundant_pairs_skip_neighbours_abstract_formula(self):
        v = unit([1, 0.05, 0])
        m = np.vstack([v, v, unit([0, 1, 0]), v, v])
        chunks = [{'abstract': True}, {}, {}, {}, {'formula': True}]
        pairs = redundant_pairs(m, chunks, thr=0.9)
        # 0 — аннотация, 4 — формула, 1↔3 — несоседние двойники; соседей (i, i+1) не берём
        assert [(i, j) for i, j, _ in pairs] == [(1, 3)]
