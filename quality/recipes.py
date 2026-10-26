from fastapi import Depends, Request
from pydantic import Field
from .querying import get_scope, apply_scope
from . import models as m
from .schemas import Strict, ReviewInput, Action
from .security import current_time, fail
from .service import serialize


class RecipeInput(Strict):
    code: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=2, max_length=200)
    line_id: str
    process: str
    parameters: dict = Field(default_factory=dict)
    source: str = Field(min_length=3, max_length=3000)
    previous_id: str | None = None


def validate_recipe(svc, row):
    data = row.data
    line = svc.get(m.Master, data.get("line_id", ""))
    if line.kind != "line" or line.data.get("process") != data.get("process"):
        fail(422, "recipe_line", "配方与产线工艺不一致")
    if not data.get("parameters"):
        fail(422, "recipe_parameters", "正式发布前须填写有批准依据的参数；不能用空参考配方代替")
    for metric, p in data["parameters"].items():
        if (
            not isinstance(p, dict)
            or set(p) != {"value", "unit", "sop_id"}
            or not isinstance(p["value"], (int, float))
            or isinstance(p["value"], bool)
        ):
            fail(422, "recipe_parameter", "参数须为value、unit和sop_id")
        sop = svc.get(m.Knowledge, p["sop_id"])
        window = sop.rules.get("parameters", {}).get(metric, {})
        if p["unit"] != window.get("unit"):
            fail(422, "recipe_unit", "配方参数单位须与批准SOP一致")
        svc.valid_actions(
            [
                Action(
                    type="sop_action",
                    description="人工执行的已批准配方参数",
                    sop_id=sop.id,
                    parameters={metric: p["value"]},
                )
            ],
            data["process"],
            "",
            line.id,
            {"route_id": line.data.get("route_id", "")},
        )


def register_recipes(app, service, command):
    @app.get("/api/recipes")
    def listing(scope=Depends(get_scope), svc=Depends(service)):
        return [
            serialize(r)
            for r in svc.db.scalars(
                apply_scope(svc, svc.scoped(m.Master), m.Master, scope, shared=True).where(m.Master.kind == "recipe").order_by(m.Master.created_at.desc())
            ).all()
        ]

    @app.post("/api/recipes")
    def create(data: RecipeInput, request: Request, svc=Depends(service)):
        svc.actor.require("master", svc.settings.site_id)

        def apply():
            line = svc.get(m.Master, data.line_id)
            if line.kind != "line" or line.data.get("process") != data.process:
                fail(422, "recipe_line", "须选择匹配工艺的真实产线")
            old = svc.get(m.Master, data.previous_id) if data.previous_id else None
            if old and (
                old.kind != "recipe"
                or old.data.get("line_id") != line.id
                or old.data.get("recipe_code") != data.code
            ):
                fail(422, "recipe_family", "新版本必须属于同一配方及产线")
            revision = old.version + 1 if old else 1
            row = svc.add(
                m.Master,
                site=line.site_id,
                kind="recipe",
                code=data.code + "@v" + str(revision),
                name=data.name,
                version=revision,
                data={
                    **data.model_dump(exclude={"name"}),
                    "recipe_code": data.code,
                    "status": "draft",
                    "author_id": svc.actor.id,
                    "reviewer_id": None,
                    "route_id": line.data.get("route_id", ""),
                },
            )
            svc.audit("recipe.draft", row.id, {"version": revision})
            return serialize(row)

        return command(svc, request, data.model_dump(), apply)

    @app.post("/api/recipes/{id}/review")
    def review(id: str, data: ReviewInput, request: Request, svc=Depends(service)):
        def apply():
            row = svc.get(m.Master, id, True)
            if row.kind != "recipe":
                fail(422, "recipe_kind", "不是配方版本")
            value = dict(row.data)
            current = value.get("status", "draft")
            if data.action == "submit":
                svc.actor.require("master", row.site_id, row.scope_line_id)
                if current != "draft":
                    fail(409, "recipe_state", "仅草稿可提交")
                validate_recipe(svc, row)
                value["status"] = "pending_review"
            else:
                svc.actor.require("recipe_review", row.site_id, row.scope_line_id)
                if value.get("author_id") == svc.actor.id:
                    fail(403, "separation_of_duties", "配方作者不能审核自己")
                required = {
                    "publish": "pending_review",
                    "reject": "pending_review",
                    "retire": "published",
                    "rollback": "published",
                }
                if current != required.get(data.action):
                    fail(409, "recipe_state", "配方状态已变化")
                previous = svc.get(m.Master, value["previous_id"], True) if value.get("previous_id") else None
                if data.action == "publish":
                    validate_recipe(svc, row)
                    if previous:
                        if previous.data.get("status") != "published":
                            fail(409, "recipe_branch", "前版本已变化，须重新核验分支")
                        previous.data = {**previous.data, "status": "retired"}
                    value.update(
                        status="published", reviewer_id=svc.actor.id, published_at=current_time().isoformat()
                    )
                elif data.action == "reject":
                    value["status"] = "draft"
                elif data.action == "retire":
                    value["status"] = "retired"
                else:
                    if (
                        not previous
                        or previous.data.get("status") != "retired"
                        or not previous.data.get("reviewer_id")
                    ):
                        fail(409, "recipe_rollback", "没有先前独立审核的有效版本")
                    validate_recipe(svc, previous)
                    previous.data = {**previous.data, "status": "published"}
                    value["status"] = "rolled_back"
                    for line in svc.db.scalars(
                        svc.scoped(m.Master)
                        .where(m.Master.kind == "line", m.Master.data["recipe_id"].as_string() == row.id)
                        .with_for_update()
                    ).all():
                        line.data = {**line.data, "recipe_id": previous.id}
                        line.version += 1
                value["reviewed_at"] = current_time().isoformat()
            row.data = value
            svc.audit("recipe." + data.action, row.id, {"reason": data.reason, "version": row.version})
            return serialize(row)

        return command(svc, request, data.model_dump(), apply)

    @app.post("/api/lines/{line_id}/recipe/{id}")
    def bind(line_id: str, id: str, request: Request, svc=Depends(service)):
        svc.actor.require("master", svc.settings.site_id, line_id)

        def apply():
            line = svc.get(m.Master, line_id, True)
            recipe = svc.get(m.Master, id)
            if (
                recipe.kind != "recipe"
                or recipe.data.get("status") != "published"
                or not recipe.data.get("reviewer_id")
                or recipe.data.get("line_id") != line_id
            ):
                fail(422, "recipe_binding", "配方必须经过审核、发布并适用于此产线")
            validate_recipe(svc, recipe)
            line.data = {**line.data, "recipe_id": recipe.id}
            line.version += 1
            svc.audit("recipe.bound", line.id, {"recipe_id": id})
            return serialize(line)

        return command(svc, request, {"recipe_id": id}, apply)
