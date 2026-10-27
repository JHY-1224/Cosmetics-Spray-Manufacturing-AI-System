import pytest

from assistant_app.backend.model_inventory import language_lora_targets


def tensors(module='model.language_model.layers.0.self_attn.q_proj', rank=8):
    prefix='base_model.model.'+module
    return {prefix+'.lora_A.weight':[rank,4096], prefix+'.lora_B.weight':[4096,rank]}


@pytest.mark.parametrize('targets', [
    ['q_proj'],
    r'.*language_model\.layers\.\d+\.self_attn\.(?:q_proj|v_proj)',
])
def test_list_and_regex_targets_accept_the_same_language_tensors(targets):
    assert language_lora_targets(targets,tensors(),8)==['model.language_model.layers.0.self_attn.q_proj']


def test_observed_unsloth_linear_attention_regex_is_supported():
    expression=r'(?:.*?(?:language|text).*?(?:self_attn|attention|attn|mixer|mlp|feed_forward|ffn|dense|mixer).*?\.(?:qkv|proj|linear_fc1|linear_fc2|out_proj|in_proj_qkv|in_proj_z|in_proj_b|in_proj_a|gate_proj|up_proj|down_proj|q_proj|k_proj|v_proj|o_proj))|(?:\bmodel\.layers\.[\d]{1,}(?!.*\.experts\.\d)\..*?(?:self_attn|attention|attn|mixer|mlp|feed_forward|ffn|dense|mixer).*?\.(?:(?:qkv|proj|linear_fc1|linear_fc2|out_proj|in_proj_qkv|in_proj_z|in_proj_b|in_proj_a|gate_proj|up_proj|down_proj|q_proj|k_proj|v_proj|o_proj)))'
    module='model.language_model.layers.0.linear_attn.in_proj_a'
    assert language_lora_targets(expression,tensors(module),8)==[module]


@pytest.mark.parametrize('targets,shapes,rank', [
    ('.*',tensors(),8),
    (['q_proj','visual.q_proj'],tensors(),8),
    (['q_proj'],tensors('model.visual.blocks.0.attn.q_proj'),8),
    (['v_proj'],tensors(),8),
    ('[',tensors(),8),
    (['q_proj'],tensors(),16),
    (['q_proj'],{next(iter(tensors())):[8,4096]},8),
])
def test_vision_targets_mismatches_and_incomplete_pairs_are_still_rejected(targets,shapes,rank):
    with pytest.raises(ValueError):
        language_lora_targets(targets,shapes,rank)
