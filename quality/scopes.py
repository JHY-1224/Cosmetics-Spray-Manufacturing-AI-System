def applicable(scope, settings, process, line_id="", context=None):
    context = context or {}
    if scope.get("process") not in (process, "all"):
        return False
    values = {
        "factory_ids": settings.site_id,
        "line_ids": line_id,
        "product_ids": context.get("product_id", ""),
        "product_families": context.get("product_family", ""),
        "shapes": context.get("shape", ""),
        "step_ids": context.get("step_id", ""),
        "substrate_ids": context.get("substrate_id", ""),
        "route_ids": context.get("route_id", ""),
        "recipe_ids": context.get("recipe_id", ""),
    }
    return all(not scope.get(key) or value in scope[key] for key, value in values.items())


def knowledge_write_scope(actor, scope):
    """A line grant permits changes only within those lines, never factory-shared publication."""
    from .security import fail
    lines = scope.get('line_ids', [])
    if actor.lines and (not lines or not set(lines).issubset(actor.lines)):
        fail(403, 'knowledge_scope_grant', '产线授权账号只能维护已授权产线范围；共享或跨线知识需要相应工厂范围授权')
