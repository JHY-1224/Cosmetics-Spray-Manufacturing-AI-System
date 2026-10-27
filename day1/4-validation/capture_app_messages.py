"""Capture development model messages from the actual assistant request path.

Uses an isolated temporary database and a capture-only mock model. This tests
message construction, never model quality, inference, training or deployment.
Only the frozen development question is submitted; review answers are excluded.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from fastapi.testclient import TestClient
from assistant_app.backend.app import create_app
from assistant_app.backend.settings import Settings
from assistant_app.backend.prompts import policy_metadata, SYSTEM

CASES_SHA = '960bcba3298b40e969ed75f754f29963211a8ecf4aecff61e561af09e9849661'


class CaptureModels:
    is_mock = True

    def __init__(self):
        self.calls = []

    async def close(self):
        pass

    async def available(self, kind):
        return True

    async def call(self, kind, messages, json_mode=False):
        if kind != 'text':
            raise AssertionError('Pure text development cases must not call vision')
        self.calls.append(messages)
        return 'capture-only: no inference performed', {'model': 'capture-only'}


def capture(cases):
    captured = []
    with tempfile.TemporaryDirectory(prefix='cosmetics-app-message-capture-') as folder:
        root = Path(folder)
        settings = Settings(data_dir=root / 'data', public_origin='http://testserver',
            text_mode='base_preview', allow_base_preview=True,
            model_directory=root / 'models', model_profiles_dir=root / 'profiles')
        model = CaptureModels()
        with TestClient(create_app(settings, model)) as client:
            session = client.post('/api/assistant/session')
            session.raise_for_status()
            client.headers['X-CSRF-Token'] = session.json()['csrf_token']
            for case in cases:
                if captured:
                    # Keep the application's 12 messages/minute visitor limit.
                    time.sleep(5.1)
                conversation = client.post('/api/assistant/conversations')
                conversation.raise_for_status()
                before = len(model.calls)
                response = client.post('/api/assistant/conversations/' + conversation.json()['id'] + '/messages',
                    json={'text': case['prompt'], 'attachment_ids': [], 'request_key': 'capture-' + case['id']})
                response.raise_for_status()
                ident = response.json()['request_id']
                deadline = time.monotonic() + 10
                while True:
                    result = client.get('/api/assistant/requests/' + ident)
                    result.raise_for_status()
                    if result.json()['phase'] in ('done', 'failed', 'cancelled', 'interrupted'):
                        break
                    if time.monotonic() > deadline:
                        raise RuntimeError('Capture request did not complete: ' + case['id'])
                    time.sleep(.01)
                if result.json()['phase'] != 'done' or len(model.calls) != before + 1:
                    raise RuntimeError('Capture request failed: ' + case['id'])
                messages = model.calls[-1]
                if (len(messages) != 2 or messages[0] != {'role': 'system', 'content': SYSTEM}
                        or json.loads(messages[1]['content'])['user_original_question'] != case['prompt']):
                    raise ValueError('Actual application messages differ from expected single-turn envelope')
                captured.append(dict(id=case['id'], split='development', messages=messages))
    return captured


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--cases', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    if args.output.exists() or args.output.is_symlink():
        p.error('Refusing to overwrite a previous capture')
    if hashlib.sha256(args.cases.read_bytes()).hexdigest() != CASES_SHA:
        p.error('Frozen development cases changed')
    cases = json.loads(args.cases.read_text())
    if len(cases) != 18 or any(c.get('split') != 'development' for c in cases):
        p.error('Require exactly 18 frozen development cases; sealed cases are not allowed')
    rows = capture(cases)
    args.output.mkdir(parents=True, exist_ok=False)
    target = args.output / 'messages.jsonl'
    with target.open('x', encoding='utf-8') as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + '\n')
    metadata = dict(status='captured_actual_app_messages_no_inference', cases=len(rows),
        cases_sha256=CASES_SHA, messages_sha256=hashlib.sha256(target.read_bytes()).hexdigest(),
        policy=policy_metadata(), review_answers_sent=False, production_accepted=False)
    (args.output / 'capture.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2))
    print(json.dumps(metadata, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
