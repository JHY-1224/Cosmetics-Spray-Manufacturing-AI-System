"""One server-owned policy bundle for every current and future model route.

prompts.py remains byte-for-byte legacy evaluation/training evidence. Live
application calls use this module, with no model-specific policy selector.
"""
import hashlib
from pathlib import Path

from .prompts import injection_signals

PROMPT_VERSION = 'industrial-assistant-v6'
_DIRECTORY = Path(__file__).resolve().parents[1] / 'configs/policies'


def _read(name, expected):
    value = (_DIRECTORY / name).read_text(encoding='utf-8')
    if hashlib.sha256(value.encode()).hexdigest() != expected:
        raise ValueError('Shared policy content differs from its registered version: ' + name)
    return value


SYSTEM = _read('shared-text-v6.txt', '564ea9c74eb89b93989eb3514eb76aab8d948a5f7f7d7eb8bed8a1c53f81c553')
VISION = _read('shared-vision-v3.txt', 'b08b40ceb10ad470381b5589b5f54f91e3eaf646930decd6e6585eadc9f7c9d5')


def policy_metadata():
    return {'version': PROMPT_VERSION,
            'text_sha256': hashlib.sha256(SYSTEM.encode()).hexdigest(),
            'vision_sha256': hashlib.sha256(VISION.encode()).hexdigest(),
            'shared_across_model_routes': True,
            'signals_are_security_guarantee': False}
