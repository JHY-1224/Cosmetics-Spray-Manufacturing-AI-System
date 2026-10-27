"""Narrow unit typography canonicalization after strict evidence validation.

No numeric conversion, inferred unit, status repair, or raw-text rewrite. Keep
the transformation record separate from the model's observations and warnings.
"""
import copy
import re


def canonical_units(evidence):
    result = copy.deepcopy(evidence)
    changes = []
    for index, field in enumerate(result['extracted_fields']):
        unit = field['unit_text']
        if isinstance(unit, str) and (unit == '℃' or re.fullmatch(r'°[ \t\u00a0\u202f]+C', unit)):
            field['unit_text'] = '°C'
            changes.append(dict(field_index=index, label=field['label'], original=unit, canonical='°C',
                                reason='Celsius typography only; no unit or numeric conversion'))
    return result, changes
