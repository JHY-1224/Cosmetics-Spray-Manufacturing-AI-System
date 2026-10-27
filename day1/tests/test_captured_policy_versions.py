import importlib.util
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'day1/4-validation'))
spec = importlib.util.spec_from_file_location('versioned_capture_probe', ROOT/'day1/4-validation/probe_app_text.py')
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def test_old_captures_keep_their_original_policy_and_current_captures_use_shared_policy():
    from assistant_app.backend import prompts, runtime_prompts
    assert probe.captured_policy({'policy': prompts.policy_metadata()}) == prompts.SYSTEM
    assert probe.captured_policy({'policy': runtime_prompts.policy_metadata()}) == runtime_prompts.SYSTEM
    assert prompts.SYSTEM != runtime_prompts.SYSTEM


def test_capture_policy_version_and_both_hashes_are_checked():
    from assistant_app.backend.runtime_prompts import policy_metadata
    for key, value in [('version', 'unregistered'), ('text_sha256', '0'*64), ('vision_sha256', '0'*64)]:
        metadata = policy_metadata()
        metadata[key] = value
        with pytest.raises(ValueError, match='policy'):
            probe.captured_policy({'policy': metadata})
