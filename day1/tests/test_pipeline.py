import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline import read_messages, tokenize_example, verified_rows


class FakeTokenizer:
    """Simple exact-prefix tokenizer to check multi-turn loss selection."""
    def apply_chat_template(self, history, **kwargs):
        tokens = []
        for m in history:
            tokens += [10 if m['role'] == 'user' else 20]
            tokens += [ord(c) + 100 for c in m['content']]
            tokens += [30]
        if kwargs.get('add_generation_prompt'):
            tokens += [20]
        return tokens


class PipelineTests(unittest.TestCase):
    def test_all_assistant_turns_only(self):
        messages = [{'role': role, 'content': text} for role, text in
                    [('user', 'a'), ('assistant', 'b'), ('user', 'c'), ('assistant', 'd')]]
        item = tokenize_example(FakeTokenizer(), messages, 100)
        self.assertEqual([x for x in item['labels'] if x != -100], [198, 30, 200, 30])
        self.assertEqual(len(item['labels']), len(item['input_ids']))

    def test_no_silent_truncation(self):
        with self.assertRaisesRegex(ValueError, 'exceeds'):
            tokenize_example(FakeTokenizer(), [{'role': 'user', 'content': 'a'},
                                               {'role': 'assistant', 'content': 'b'}], 2)

    def test_template_mismatch_rejected(self):
        class WrongTokenizer(FakeTokenizer):
            def apply_chat_template(self, history, **kwargs):
                ids = super().apply_chat_template(history, **kwargs)
                return ids + [999] if kwargs.get('add_generation_prompt') else ids
        with self.assertRaisesRegex(ValueError, 'prefixes differ'):
            tokenize_example(WrongTokenizer(), [{'role': 'user', 'content': 'a'},
                                                {'role': 'assistant', 'content': 'b'}], 100)

    def test_historical_thinking_removed_keeps_all_answer_targets(self):
        class NativeHistoryTokenizer:
            is_fast = True
            eos_token_id = ord('~')

            def apply_chat_template(self, history, **kwargs):
                last_user = max(i for i, m in enumerate(history) if m['role'] == 'user')
                text = ''
                for i, m in enumerate(history):
                    if m['role'] == 'user':
                        text += 'U' + m['content'] + '~'
                    else:
                        text += 'A' + ('<empty-think>' if i > last_user else '') + m['content'] + '~'
                if kwargs.get('add_generation_prompt'):
                    text += 'A<empty-think>'
                return [ord(c) for c in text] if kwargs.get('tokenize', True) else text

            def __call__(self, text, **kwargs):
                return {'input_ids': [ord(c) for c in text],
                        'offset_mapping': [(i, i + 1) for i in range(len(text))]}

        messages = [{'role': role, 'content': value} for role, value in
                    [('user', 'question-one'), ('assistant', 'b'), ('user', 'question-two'), ('assistant', 'd')]]
        item = tokenize_example(NativeHistoryTokenizer(), messages, 200)
        self.assertEqual(''.join(chr(x) for x in item['labels'] if x != -100), 'b~d~')
        self.assertEqual(len(item['labels']), len(item['input_ids']))

        class RewritesAnswers(NativeHistoryTokenizer):
            def apply_chat_template(self, history, **kwargs):
                history = [dict(m, content=m['content'].upper()) if m['role'] == 'assistant' else m
                           for m in history]
                return super().apply_chat_template(history, **kwargs)

        with self.assertRaisesRegex(ValueError, 'cannot safely mask'):
            tokenize_example(RewritesAnswers(), messages, 200)

    def test_invalid_roles_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'invalid.jsonl'
            path.write_text(json.dumps({'messages': [{'role': 'assistant', 'content': 'answer'}]}))
            with self.assertRaises(ValueError):
                read_messages(path)

    def test_approved_export_hash(self):
        rows, manifest = verified_rows()
        self.assertEqual(len(rows), 36)
        self.assertEqual(manifest['validation_rows'], 0)
        self.assertEqual(manifest['test_rows'], 0)


if __name__ == '__main__':
    unittest.main()
