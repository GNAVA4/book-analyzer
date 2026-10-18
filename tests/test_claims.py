"""Тесты разбора ответов LLM в app/analysis/claims.py (без модели: подменённый клиент)."""
from app.analysis.claims import claim_profile, extract_claims, match_claims, parse_json_array


class FakeLLM:
    def __init__(self, answer):
        self.answer = answer

    def complete(self, user_text, use_cache=True):
        return self.answer


class TestParse:
    def test_array_with_text_around(self):
        assert parse_json_array('Вот ответ:\n[{"claim": "x", "type": "факт"}]\nГотово') == [
            {'claim': 'x', 'type': 'факт'}]

    def test_truncated_array_salvages_whole_objects(self):
        text = '[{"claim": "a", "type": "факт"}, {"claim": "b", "type": "общее"}, {"claim": "c", "ty'
        assert [o['claim'] for o in parse_json_array(text)] == ['a', 'b']


class TestExtract:
    def test_unknown_type_becomes_general(self):
        llm = FakeLLM('[{"claim": "Это важно", "type": "оценка"}, {"claim": "Метод X сходится", "type": "Результат"}]')
        cl = extract_claims(llm, 'абзац')
        assert [c['type'] for c in cl] == ['общее', 'результат']

    def test_profile(self):
        cl = [{'claim': '1', 'type': 'результат'}, {'claim': '2', 'type': 'факт'},
              {'claim': '3', 'type': 'общее'}, {'claim': '4', 'type': 'связка'}]
        p = claim_profile(cl, 500)
        assert p['substantive_per_1000'] == 4.0 and p['general_share'] == 0.5


class TestMatch:
    A = [{'claim': 'a1'}, {'claim': 'a2'}, {'claim': 'a3'}]
    B = [{'claim': 'b1'}, {'claim': 'b2'}]

    def test_line_format(self):
        llm = FakeLLM('A1 -> B2 да\nA2 -> - нет\nA3 -> B1 частично')
        assert [(m['b'], m['verdict']) for m in match_claims(llm, self.A, self.B)] == [
            (1, 'да'), (None, 'нет'), (0, 'частично')]

    def test_missing_line_is_unrated_not_no(self):
        # сбой session 017: модель ответила только про A1 — остальные не должны стать «нет»
        llm = FakeLLM('A1 -> B1 да')
        assert [m['verdict'] for m in match_claims(llm, self.A, self.B)] == ['да', 'не оценено', 'не оценено']

    def test_out_of_range_b_ignored(self):
        llm = FakeLLM('A1 -> B9 да\nA2 -> B1 нет\nA3 -> B2 да')
        assert match_claims(llm, self.A, self.B)[0]['b'] is None
