"""Portable image/text JSONL contract; no remote fetches or model imports."""
import hashlib
import json
from pathlib import Path


def load_records(path, allow_unreviewed=False):
    from PIL import Image
    path = Path(path).resolve()
    rows, ids, hashes, groups = [], set(), {}, {}
    for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        ident, group, split = row['id'], row['source_group'], row['split']
        if not isinstance(ident, str) or not ident or ident in ids:
            raise ValueError(f'Row {number}: invalid/duplicate id')
        ids.add(ident)
        if not group or split not in ('train', 'validation', 'test'):
            raise ValueError(f'{ident}: missing group or invalid split')
        if groups.setdefault(group, split) != split:
            raise ValueError(f'{ident}: source group leaks across splits')
        if row.get('expert_reviewed') is not True and not allow_unreviewed:
            raise ValueError(f'{ident}: expert review pending; --allow-unreviewed is for development only')
        if not all(isinstance(row.get(k), str) and row[k].strip() for k in ('question', 'answer', 'source_url')):
            raise ValueError(f'{ident}: missing question, answer or provenance')
        if 'system' in row and (not isinstance(row['system'], str) or not row['system'].strip()):
            raise ValueError(f'{ident}: system must be a nonempty policy string')
        images = row.get('images')
        if not isinstance(images, list) or not 1 <= len(images) <= 3:
            raise ValueError(f'{ident}: require 1–3 images')
        paths = []
        for item in images:
            relative = Path(item['path'])
            image = (path.parent / relative).resolve()
            if relative.is_absolute() or not image.is_relative_to(path.parent) or not image.is_file():
                raise ValueError(f'{ident}: missing image or path escape')
            digest = hashlib.sha256(image.read_bytes()).hexdigest()
            if digest != item['sha256']:
                raise ValueError(f'{ident}: image hash mismatch')
            if hashes.setdefault(digest, split) != split:
                raise ValueError(f'{ident}: same image leaks across splits')
            with Image.open(image) as opened:
                if opened.format not in ('JPEG', 'PNG', 'WEBP') or opened.width * opened.height > 24_000_000:
                    raise ValueError(f'{ident}: unsupported image')
                opened.verify()
            paths.append(image)
        rows.append({**row, '_paths': paths})
    if not rows:
        raise ValueError('Dataset is empty')
    return rows


def conversation(row):
    from PIL import Image
    content = [{'type': 'text', 'text': row['question']}]
    for path in row['_paths']:
        with Image.open(path) as image:
            content.append({'type': 'image', 'image': image.convert('RGB')})
    messages = [{'role': 'system', 'content': row['system']}] if row.get('system') else []
    messages.extend([{'role': 'user', 'content': content},
                     {'role': 'assistant', 'content': [{'type': 'text', 'text': row['answer']}]}])
    return {'messages': messages}


def _conversation_batch(batch):
    """HF transforms receive batches, even for a single integer index."""
    return {'messages': [conversation({'question': question, 'answer': answer,
                                      '_paths': paths, 'system': system})['messages']
                         for question, answer, paths, system in zip(
                             batch['question'], batch['answer'], batch['_paths'],
                             batch['system'], strict=True)]}


def training_dataset(rows):
    """Use TRL's required HF Dataset type; decode images lazily for the collator.

    Store only text/paths in Arrow. PIL objects cannot be inferred as Arrow columns.
    Keep skip_prepare_dataset=True and remove_unused_columns=False in SFTConfig.
    """
    from datasets import Dataset
    data = Dataset.from_list([{'question': row['question'], 'answer': row['answer'],
                               'system': row.get('system', ''),
                               '_paths': [str(path) for path in row['_paths']]} for row in rows])
    return data.with_transform(_conversation_batch)
