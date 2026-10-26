"""Shared, offline data validation and native-template assistant loss masking."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / '化妆品资料采集包_20261025/cosmetics_sft_dataset_20261025_1650'
PREPARED = ROOT / 'day1/2-prepare-data'


def native_tokenizer(model_dir, tokenizer=None):
    """Load local tokenizer and, when needed, the official legacy VL template."""
    if tokenizer is None:
        from transformers import AutoTokenizer
        tokenizer = AutoTokenizer.from_pretrained(model_dir, local_files_only=True, trust_remote_code=False)
    legacy = Path(model_dir) / 'chat_template.json'
    if not getattr(tokenizer, 'chat_template', None) and legacy.is_file():
        template = json.loads(legacy.read_text())['chat_template']
        if not isinstance(template, str) or not template.strip():
            raise ValueError('Official chat template is empty or invalid')
        tokenizer.chat_template = template
    return tokenizer


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_messages(path):
    rows = []
    for line in Path(path).read_text(encoding='utf-8').splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        messages = row.get('messages')
        if not isinstance(messages, list) or not messages:
            raise ValueError('Expected nonempty messages')
        expected = 'user'
        for i, message in enumerate(messages):
            if not isinstance(message.get('content'), str) or not message['content'].strip():
                raise ValueError('Expected nonempty text content')
            if i == 0 and message.get('role') == 'system':
                continue
            if message.get('role') != expected:
                raise ValueError('Expected alternating user/assistant turns')
            expected = 'assistant' if expected == 'user' else 'user'
        if messages[-1]['role'] != 'assistant':
            raise ValueError('Conversation must end with assistant')
        rows.append(row)
    return rows


def verified_rows():
    metadata = json.loads((PREPARED / 'dataset-manifest.json').read_text())
    path = PREPARED / 'train.messages.jsonl'
    if sha256(path) != metadata['train_sha256']:
        raise ValueError('Prepared data changed: rerun preparation and review the manifest')
    rows = read_messages(path)
    if not rows or len(rows) != metadata['train_rows']:
        raise ValueError('Empty data or inconsistent row count')
    return rows, metadata


def tokenize_example(tokenizer, messages, max_length):
    """Mask system/user and assistant headers; supervise every assistant answer + EOS.

    Check exact token prefixes on every turn; refuse to guess/truncate if a model
    template changes historical messages, validate content spans against the full
    native rendering and exact tokenizer offsets. No invented reasoning is added.
    """
    def render(history, generation=False):
        return tokenizer.apply_chat_template(
            history, tokenize=True, return_dict=False, add_generation_prompt=generation,
            enable_thinking=False, preserve_thinking=True)

    ids = render(messages)
    if len(ids) > max_length:
        raise ValueError(f'{len(ids)} tokens exceeds max_length={max_length}; increase limit, no silent truncation')
    labels = [-100] * len(ids)
    for index, message in enumerate(messages):
        if message['role'] != 'assistant':
            continue
        prompt = render(messages[:index], generation=True)
        completed = render(messages[:index + 1])
        if completed[:len(prompt)] != prompt or ids[:len(completed)] != completed:
            return _tokenize_full_template(tokenizer, messages, ids)
        if len(prompt) >= len(completed):
            raise ValueError('Assistant target has zero tokens')
        labels[len(prompt):len(completed)] = ids[len(prompt):len(completed)]
    if not any(x != -100 for x in labels):
        raise ValueError('No assistant targets')
    return {'input_ids': ids, 'attention_mask': [1] * len(ids), 'labels': labels}


def _tokenize_full_template(tokenizer, messages, ids):
    """Qwen3.5 removes empty think blocks from historical assistant turns.

    Locate each answer in the final native rendering, then require exact content,
    token boundaries and EOS. Never label user/header/thinking tokens by guessing
    that separately rendered turn prefixes remain unchanged.
    """
    error = 'Native template token prefixes differ: cannot safely mask assistant loss'
    if not getattr(tokenizer, 'is_fast', False):
        raise ValueError(error)

    def render(history):
        return tokenizer.apply_chat_template(
            history, tokenize=False, add_generation_prompt=False,
            enable_thinking=False, preserve_thinking=True)

    text = render(messages)
    encoded = tokenizer(text, add_special_tokens=False, return_offsets_mapping=True)
    if encoded['input_ids'] != ids or len(encoded['offset_mapping']) != len(ids):
        raise ValueError(error)
    offsets = encoded['offset_mapping']
    labels = [-100] * len(ids)
    for index, message in enumerate(messages):
        if message['role'] != 'assistant':
            continue
        marker = f'__ASSISTANT_SPAN_{index}__'
        if marker in text:
            raise ValueError(error)
        marked = render([dict(m, content=marker) if i == index else m for i, m in enumerate(messages)])
        answer = message['content'].strip()
        if marked.count(marker) != 1 or marked.replace(marker, answer, 1) != text:
            raise ValueError(error)
        start = marked.index(marker)
        end = start + len(answer)
        selected = []
        for position, (left, right) in enumerate(offsets):
            if right > start and left < end:
                if not start <= left < right <= end:
                    raise ValueError(error)
                selected.append(position)
        if not selected or offsets[selected[0]][0] != start or offsets[selected[-1]][1] != end:
            raise ValueError(error)
        eos = selected[-1] + 1
        if eos >= len(ids) or ids[eos] != tokenizer.eos_token_id or offsets[eos][0] != end:
            raise ValueError(error)
        for position in [*selected, eos]:
            labels[position] = ids[position]
    if not any(x != -100 for x in labels):
        raise ValueError('No assistant targets')
    return {'input_ids': ids, 'attention_mask': [1] * len(ids), 'labels': labels}


class AssistantCollator:
    def __init__(self, pad_token_id):
        self.pad_token_id = pad_token_id

    def __call__(self, features):
        import torch
        size = max(len(x['input_ids']) for x in features)
        fills = {'input_ids': self.pad_token_id, 'attention_mask': 0, 'labels': -100}
        return {key: torch.tensor([row[key] + [fill] * (size - len(row[key]))
                                   for row in features], dtype=torch.long)
                for key, fill in fills.items()}
