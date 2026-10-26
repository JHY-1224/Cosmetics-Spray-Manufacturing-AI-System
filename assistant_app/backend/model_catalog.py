"""Server-owned model choices. Read metadata only; never load weights or start processes."""
import asyncio
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .model_clients import ModelClients, text_artifact_state, vision_artifact_state
from .model_inventory import inventory
from .settings import Settings, ROOT
from .sessions import fail


def read_json(path):
    if path.stat().st_size > 256 * 1024:
        raise ValueError('metadata too large')
    return json.loads(path.read_text())


class ProfileFile(BaseModel):
    model_config = ConfigDict(extra='forbid', hide_input_in_errors=True)
    id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,80}$')
    name: str = Field(min_length=1, max_length=100)
    category: Literal['cloud', 'local_base', 'local_finetuned']
    text_model_base_url: str
    text_served_model: str = Field(min_length=1, max_length=160)
    text_model_path: Path | None = None
    text_adapter_path: Path | None = None
    text_merged_model_path: Path | None = None
    model_verification_file: Path | None = None
    vision_model_base_url: str | None = None
    vision_served_model: str | None = None
    vision_model_path: Path | None = None
    vision_mode: Literal['vision_base', 'approved_cloud', 'finetuned_lora', 'finetuned_merged'] | None = None
    vision_adapter_path: Path | None = None
    vision_merged_model_path: Path | None = None
    vision_verification_file: Path | None = None


@dataclass
class Route:
    settings: Settings
    client: object
    profile: dict


class ModelCatalog:
    def __init__(self, settings, default_client, client_factory=None):
        self.settings = settings
        self.default_client = default_client
        self.client_factory = client_factory or ModelClients
        self.clients = {}
        self.cache = (0, None)

    @staticmethod
    def category(settings):
        return ('cloud' if settings.text_mode == 'approved_cloud' else
                'local_base' if settings.text_mode == 'base_preview' else 'local_finetuned')

    def entry(self, ident, name, settings):
        # Do not include key material in fingerprints or the visitor-facing profile.
        public = {'id': ident, 'name': name, 'category': self.category(settings),
                  'text_model': settings.text_served_model, 'vision_model': settings.vision_served_model,
                  'text_external': settings.text_mode == 'approved_cloud',
                  'vision_external': settings.vision_mode == 'approved_cloud'}
        config = {key: value for key, value in settings.model_dump(mode='json').items()
                  if key in ('text_mode', 'vision_mode', 'text_model_base_url', 'vision_model_base_url',
                    'text_served_model', 'vision_served_model', 'text_model_path', 'text_adapter_path',
                    'text_merged_model_path', 'vision_model_path', 'model_verification_file',
                    'vision_adapter_path', 'vision_merged_model_path', 'vision_verification_file')}
        public['revision'] = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
        return public, settings

    def local_settings(self, path, adapter=False, merged=False):
        s = self.settings
        update = {'text_mode': 'finetuned_lora' if adapter else 'finetuned_merged' if merged else 'base_preview',
                  'allow_base_preview': True, 'text_model_base_url': s.local_text_model_base_url,
                  'text_served_model': s.local_text_served_model, 'text_api_key': s.local_text_api_key,
                  'vision_mode': 'vision_base', 'vision_model_base_url': s.local_vision_model_base_url,
                  'vision_served_model': s.local_vision_served_model, 'vision_api_key': s.local_vision_api_key,
                  'text_adapter_path': None, 'text_merged_model_path': None, 'model_verification_file': None}
        if adapter or merged:
            manifest = read_json(path.parent / 'run-manifest.json')
            base = Path(manifest['model_dir']).expanduser()
            base = base if base.is_absolute() else ROOT / base
            base = base.resolve()
            if not base.is_relative_to(s.model_directory.resolve()):
                raise ValueError('base outside model directory')
            update['text_model_path'] = base
            # A fine-tuned entry must never reuse the original model's served alias.
            update['text_served_model'] = (s.local_text_served_model + '-finetuned-'
                                          + hashlib.sha256(str(path).encode()).hexdigest()[:10])
            update['text_adapter_path' if adapter else 'text_merged_model_path'] = path
            proof = path.parent / 'assistant-verification.json'
            if proof.is_file():
                update['model_verification_file'] = proof
        else:
            update['text_model_path'] = path
            if path.resolve() != s.text_model_path.resolve():
                update['text_served_model'] = 'local-model-' + hashlib.sha256(str(path).encode()).hexdigest()[:12]
        return Settings.model_validate({**s.model_dump(), **update})

    def paths(self):
        root = self.settings.model_directory.expanduser().resolve()
        if not root.is_dir():
            return []
        result = []
        # Known layouts only. Never recurse through caches, hidden directories or symlinks.
        def children(path):
            return [p for p in sorted(path.iterdir())[:256]
                    if p.is_dir() and not p.is_symlink() and not p.name.startswith('.')]
        for child in children(root):
            result.append(child)
            if child.name == 'base':
                result.extend(children(child))
            elif child.name == 'finetuned':
                for family in children(child):
                    for run in children(family):
                        result.extend([p for p in (run / 'adapter', run / 'merged') if p.is_dir()])
        return [p.resolve() for p in result[:256] if p.resolve().is_relative_to(root)]

    def entries(self):
        s = self.settings
        category = self.category(s)
        default_name = '阿里云百炼' if category == 'cloud' else ('基座预览' if category == 'local_base' else
            'Qwen轻量级服务' if s.vision_mode in ('finetuned_lora', 'finetuned_merged') else '自部署微调服务')
        entries = {'configured': self.entry('configured', default_name, s)}
        issues = 0
        for path in self.paths():
            adapter = (path / 'adapter_config.json').is_file()
            if not adapter and not (path / 'config.json').is_file():
                continue
            try:
                config = read_json(path / ('adapter_config.json' if adapter else 'config.json'))
                # A standalone VL model is the vision component, not a text-chat route.
                if not adapter and config.get('model_type') not in ('qwen3_5',):
                    continue
                merged = not adapter and (path.parent / 'run-manifest.json').is_file()
                ident = 'local-' + hashlib.sha256(str(path).encode()).hexdigest()[:20]
                name = (path.parent.parent.name + ' · ' + path.parent.name if adapter or merged else path.name)
                local = self.local_settings(path, adapter, merged)
                # Do not duplicate an already configured local artifact.
                selected_path = s.text_adapter_path or s.text_merged_model_path or s.text_model_path
                if category != 'cloud' and path == selected_path.resolve():
                    continue
                entries[ident] = self.entry(ident, name[:100], local)
            except (OSError, ValueError, TypeError, KeyError):
                issues += 1
        directory = s.model_profiles_dir.expanduser().resolve()
        if directory.is_dir():
            for file in sorted(directory.glob('*.json'))[:64]:
                try:
                    if file.is_symlink():
                        raise ValueError('symlink profile')
                    profile = ProfileFile.model_validate(read_json(file))
                    if profile.id in entries:
                        raise ValueError('duplicate profile id')
                    data = profile.model_dump(exclude_none=True, exclude={'id', 'name', 'category'})
                    for key in list(data):
                        if key.endswith('_path') or key.endswith('_verification_file'):
                            data[key] = (ROOT / data[key]).expanduser().resolve()
                    cloud = profile.category == 'cloud'
                    if cloud and (not profile.vision_model_base_url or not profile.vision_served_model):
                        raise ValueError('cloud profile requires explicit vision route')
                    mode = ('approved_cloud' if cloud else 'base_preview' if profile.category == 'local_base'
                            else 'finetuned_merged' if profile.text_merged_model_path else 'finetuned_lora')
                    local = self.local_settings(s.text_model_path)
                    base = s if cloud else local
                    data.update(text_mode=mode, allow_base_preview=mode == 'base_preview')
                    if cloud:
                        data['vision_mode'] = 'approved_cloud'
                    configured = Settings.model_validate({**base.model_dump(), **data})
                    entries[profile.id] = self.entry(profile.id, profile.name, configured)
                except (OSError, ValueError, TypeError, KeyError, ValidationError):
                    issues += 1
        return entries, issues

    def resolve(self, ident=None):
        entries, _ = self.entries()
        ident = ident or 'configured'
        if ident not in entries:
            fail('model_not_found', '所选模型已移除或尚未登记，请刷新模型列表', 422)
        profile, settings = entries[ident]
        fingerprint = profile['id'] + ':' + profile['revision']
        if ident == 'configured':
            client = self.default_client
        else:
            if fingerprint not in self.clients:
                if len(self.clients) >= 128:
                    fail('model_catalog_capacity', '模型配置变更次数过多，请由服务端重载配置', 503)
                self.clients[fingerprint] = self.client_factory(settings)
            client = self.clients[fingerprint]
        return Route(settings, client, dict(profile))

    @staticmethod
    def artifact(settings):
        state = text_artifact_state(settings)
        if settings.vision_mode in ('finetuned_lora', 'finetuned_merged'):
            vision = vision_artifact_state(settings)
            if vision not in ('finetuned_lora', 'finetuned_merged'):
                return 'vision_' + vision
        if state == 'base_preview':
            try:
                inventory(settings.text_model_path, 'qwen3_5')
            except (OSError, ValueError, KeyError):
                return 'files_incomplete'
        return state

    async def listing(self, refresh=False):
        stamp, cached = self.cache
        if cached and not refresh and time.monotonic() - stamp < 8:
            return cached
        entries, issues = self.entries()
        async def inspect(ident):
            route = self.resolve(ident)
            state = self.artifact(route.settings)
            valid = state in ('approved_cloud', 'base_preview', 'finetuned_lora', 'finetuned_merged')
            online = await route.client.available('text') if valid else False
            if online and route.settings.vision_mode in ('finetuned_lora', 'finetuned_merged'):
                online = await route.client.available('vision')
            # Every choice points to an explicit served model; files alone never mean it is deployed.
            return {**route.profile, 'state': state if not valid else 'ready' if online else 'not_deployed',
                    'selectable': valid and online}
        options = await asyncio.gather(*(inspect(key) for key in entries))
        result = {'default_id': 'configured', 'models': options, 'unreadable_entries': issues}
        self.cache = time.monotonic(), result
        return result

    async def close(self):
        await asyncio.gather(*(client.close() for client in self.clients.values()))
