from . import models as m
from .security import aware, current_time


def normalize_terms(service, terms, process, line_id):
    entries = service.db.scalars(
        service.scoped(m.Knowledge).where(m.Knowledge.category == "defect", m.Knowledge.status == "published")
    ).all()
    mapping = {}
    for entry in entries:
        if aware(entry.valid_until) < current_time() or entry.scope.get("process") not in ("all", process):
            continue
        if entry.scope.get("line_ids") and line_id not in entry.scope["line_ids"]:
            continue
        for alias in [entry.title, *entry.rules.get("aliases", [])]:
            mapping.setdefault(alias, []).append(entry)
    result = []
    for term in terms:
        matches = mapping.get(term, [])
        canonical = {e.title for e in matches}
        result.append(
            {
                "source_term": term,
                "canonical_term": next(iter(canonical)) if len(canonical) == 1 else None,
                "ontology_ids": [e.id for e in matches],
                "status": "known" if len(canonical) == 1 else "conflict" if matches else "unknown",
            }
        )
    return result
