"""Explicit generation stops shared by offline model diagnostics."""


def stop_token_ids(generation_eos, tokenizer_eos, mode='chat'):
    """Keep both the base EOS and the supervised chat EOS.

    Legacy mode is only for reproducing historical diagnostic results.
    """
    if mode not in ('chat', 'legacy'):
        raise ValueError('Unknown EOS stop mode')
    values = generation_eos if isinstance(generation_eos, list) else [generation_eos]
    values = values + ([tokenizer_eos] if mode == 'chat' else [])
    values = [value for value in values if value is not None]
    if not values or any(type(value) is not int or value < 0 for value in values):
        raise ValueError('Valid explicit EOS token IDs required')
    return list(dict.fromkeys(values))
