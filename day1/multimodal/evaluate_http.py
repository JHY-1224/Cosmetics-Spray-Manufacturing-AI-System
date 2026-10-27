"""Frozen development visual scoring against an explicit loopback model server.

Default checks files only. --run sends real image requests, saves raw responses,
and never retries unknown outcomes. No training, sealed test or deployment.
HTTP finish_reason=stop is recorded as such; it is not claimed to prove EOS IDs.
"""
import argparse
import base64
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import sys
import time
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
from verify_official_base import load_evaluation


def selected_cases(rows, ids=None, limit=None):
    development = [r for r in rows if r['split'] == 'development']
    if ids:
        pool = {r['id']: r for r in development}
        if limit or len(ids) != len(set(ids)) or any(i not in pool for i in ids):
            raise ValueError('Require unique development IDs without a simultaneous limit')
        return [pool[i] for i in ids], len(development)
    return development[:limit] if limit else development, len(development)


def http_messages(evaluation, case):
    messages = evaluation.messages(case)
    for item in messages[-1]['content']:
        if item['type'] == 'image':
            buffer = BytesIO()
            item.pop('image').save(buffer, format='PNG')
            item.update(type='image_url', image_url={
                'url': 'data:image/png;base64,' + base64.b64encode(buffer.getvalue()).decode()})
    return messages


def summary(rows, expected, variants):
    result = {}
    for variant in variants:
        selected = [r for r in rows if r['variant'] == variant]
        total = sum(r['score']['fields_total'] for r in selected)
        correct = sum(r['score']['fields_correct'] for r in selected)
        critical = sum(r['score']['critical_errors'] for r in selected)
        complete = len(selected) == expected
        eligible = (complete and total > 0 and correct / total >= .95 and critical == 0
                    and all(r['normal_finish'] and r['score']['schema_valid']
                            and not r['repeated_output'] for r in selected))
        result[variant] = dict(cases=len(selected), expected_cases=expected, complete=complete,
            schema_valid=sum(r['score']['schema_valid'] for r in selected),
            fields_correct=correct, fields_total=total, field_accuracy=correct / total if total else None,
            critical_errors=critical, normal_stops=sum(r['normal_finish'] for r in selected),
            truncated=sum(r['hit_output_limit'] for r in selected),
            repeated=sum(r['repeated_output'] for r in selected),
            eligible_for_human_review=eligible, quality_acceptance='pending_human_review')
    return result


def run(args, evaluation, cases, expected, variants):
    import httpx
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(str(args.tokenizer.resolve()), local_files_only=True,
                                              trust_remote_code=False)
    results = []
    with httpx.Client(trust_env=False, follow_redirects=False, timeout=180) as client:
        inventory = client.get(args.base_url.rstrip('/') + '/models')
        inventory.raise_for_status()
        available = {m['id'] for m in inventory.json()['data']}
        if not set(variants.values()) <= available:
            raise ValueError('Requested model identities are not served')
        with (args.output / 'responses.jsonl').open('x', encoding='utf-8') as report:
            for case in cases:
                messages = http_messages(evaluation, case)
                for variant, model in variants.items():
                    payload = dict(model=model, messages=messages, temperature=0, max_tokens=768,
                        seed=3407, stream=False, response_format={'type': 'json_object'},
                        chat_template_kwargs={'enable_thinking': False})
                    # Save the payload fingerprint before a real call; references are never in it.
                    fingerprint = hashlib.sha256(json.dumps(payload, ensure_ascii=False,
                                                            sort_keys=True).encode()).hexdigest()
                    intent = dict(id=case['id'], variant=variant, model=model,
                        request_sha256=fingerprint, status='prepared_no_confirmed_response',
                        created_at=datetime.now(timezone.utc).isoformat())
                    with (args.output / (case['id'] + '-' + variant + '-request.json')).open('x') as f:
                        json.dump(intent, f, ensure_ascii=False)
                        f.flush()
                        os.fsync(f.fileno())
                    start = time.monotonic()
                    response = client.post(args.base_url.rstrip('/') + '/chat/completions', json=payload)
                    raw = response.content
                    record = dict(id=case['id'], variant=variant, model=model,
                        request_sha256=fingerprint, http_status=response.status_code,
                        elapsed_seconds=round(time.monotonic() - start, 3))
                    # Preserve even rejected/error responses, without retrying the request.
                    with (args.output / (case['id'] + '-' + variant + '-http.json')).open('xb') as f:
                        f.write(raw)
                    response.raise_for_status()
                    if len(raw) > 256 * 1024:
                        raise ValueError('Oversized server response')
                    body = response.json()
                    choice = body['choices'][0]
                    message = choice['message']
                    answer = message.get('content')
                    if body.get('model') != model or not isinstance(answer, str):
                        raise ValueError('Model identity or response content mismatch')
                    if message.get('tool_calls') or message.get('function_call'):
                        raise ValueError('Unexpected tools in extraction response')
                    normal = choice.get('finish_reason') == 'stop'
                    ids = tokenizer(answer, add_special_tokens=False)['input_ids']
                    record.update(answer=answer, finish_reason=choice.get('finish_reason'),
                        normal_finish=normal, ended_with_eos=None,
                        hit_output_limit=choice.get('finish_reason') == 'length',
                        repeated_output=evaluation.repeated_tail(ids),
                        repetition_check='retokenized response text heuristic',
                        usage=body.get('usage'), score=evaluation.score(answer, case))
                    results.append(record)
                    report.write(json.dumps(record, ensure_ascii=False) + '\n')
                    report.flush()
                    os.fsync(report.fileno())
                    (args.output / 'summary.json').write_text(json.dumps(
                        summary(results, expected, variants), ensure_ascii=False, indent=2))
                    print(json.dumps({k: record[k] for k in
                        ('id', 'variant', 'finish_reason', 'repeated_output', 'score')}, ensure_ascii=False), flush=True)
    return summary(results, expected, variants)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--evaluation-root', type=Path, required=True)
    p.add_argument('--training-data', type=Path, required=True)
    p.add_argument('--tokenizer', type=Path, required=True)
    p.add_argument('--policy-file', type=Path, required=True)
    p.add_argument('--base-url', required=True)
    p.add_argument('--base-model', required=True)
    p.add_argument('--adapter-model', required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--limit', type=int)
    p.add_argument('--case-ids', nargs='+')
    p.add_argument('--variant', choices=('base', 'finetuned', 'both'), default='both')
    p.add_argument('--run', action='store_true')
    args = p.parse_args()
    url = urlsplit(args.base_url)
    if (url.scheme != 'http' or url.hostname not in ('127.0.0.1', 'localhost', '::1')
            or url.path.rstrip('/') != '/v1' or url.username or url.password or url.query or url.fragment):
        p.error('Explicit loopback HTTP /v1 endpoint required')
    if args.base_model == args.adapter_model:
        p.error('Base and adapter identities must differ')
    if args.limit is not None and args.limit < 1:
        p.error('Positive development limit required')
    if args.output.exists() or args.output.is_symlink():
        p.error('Refusing to overwrite an earlier report')
    evaluation = load_evaluation(args.evaluation_root)
    all_rows, card = evaluation.load_suite(args.evaluation_root / 'data/vision-eval-v2', args.training_data)
    selected, expected_cases = selected_cases(all_rows, args.case_ids, args.limit)
    policy = args.policy_file.read_text()
    if not policy.strip() or len(policy) > 12000:
        p.error('Invalid candidate policy')
    evaluation.VISION = policy
    variants = {'base': args.base_model, 'finetuned': args.adapter_model}
    if args.variant != 'both':
        variants = {args.variant: variants[args.variant]}
    state = dict(status='files_checked_no_http_inference', cases=len(selected),
        case_ids=[r['id'] for r in selected], expected_cases=expected_cases,
        split='development', cases_sha256=card['cases_sha256'],
        policy_sha256=hashlib.sha256(policy.encode()).hexdigest(), base_url=args.base_url,
        variants=variants, production_accepted=False,
        stop_evidence='HTTP finish_reason only; raw generated EOS IDs unavailable')
    if args.run:
        args.output.mkdir(parents=True, exist_ok=False)
        (args.output / 'candidate-policy.txt').write_text(policy)
        state['started_at'] = datetime.now(timezone.utc).isoformat()
        try:
            state['groups'] = run(args, evaluation, selected, expected_cases, variants)
            state['status'] = 'completed_pending_human_review'
        except (Exception, KeyboardInterrupt) as exc:
            state.update(status='failed_no_automatic_retry', error_type=type(exc).__name__, error=str(exc))
            raise
        finally:
            state['finished_at'] = datetime.now(timezone.utc).isoformat()
            (args.output / 'run.json').write_text(json.dumps(state, ensure_ascii=False, indent=2))
    print(json.dumps(state, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
