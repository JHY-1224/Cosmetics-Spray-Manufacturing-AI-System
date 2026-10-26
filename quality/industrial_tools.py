"""Seven business capabilities backed by fixed typed tools, with real invocation ledgers."""

import time
from typing import Literal
from fastapi import HTTPException
from pydantic import Field, ValidationError
from . import enterprise_schemas as s
from . import models as m
from .schemas import Strict
from .security import digest, fail
from .service import serialize


class RecordInput(Strict):
    kind: Literal["measurement", "inspection", "lab", "raw_record"]
    id: str = Field(min_length=1, max_length=100)


class KnowledgeInput(Strict):
    line_id: str
    query: str = Field(default="", max_length=1000)
    product_id: str = Field(default="", max_length=120)


class InvokeInput(Strict):
    tool: str = Field(min_length=3, max_length=100)
    input: dict


class InvokeResult(Strict):
    invocation_id: str
    tool: str
    tool_version: Literal["industrial-tools-v1"] = "industrial-tools-v1"
    engine: Literal["deterministic_tools"] = "deterministic_tools"
    status: Literal["success", "no_data", "forbidden", "invalid", "stale", "timeout", "failure"]
    result: dict
    error_code: str = ""


def record(svc, data):
    cls = {
        "measurement": m.Measurement,
        "inspection": m.Inspection,
        "lab": m.LabResult,
        "raw_record": m.RawRecord,
    }[data.kind]
    svc.actor.require("admin" if cls == m.RawRecord else "read")
    return {"kind": data.kind, "record": serialize(svc.get(cls, data.id)), "source_fact_modified": False}


def knowledge(svc, data):
    from .workspace import AssistantInput, assistant_result
    from .querying import QueryScope

    result = assistant_result(
        svc,
        "A1",
        AssistantInput(query=data.query, scope=QueryScope(line_id=data.line_id, product_id=data.product_id)),
    )
    return {**result, "training_performed": False, "publication_requires_independent_review": True}


def resolve(name):
    from .quality_decisions import assess
    from .formulations import calculate
    from .process_decisions import select_mode
    from .business_gateway import create
    from .supply import plan
    from .costs import reconcile

    tools = {
        "data.reconcile": ("数据核对", RecordInput, "read", "none", record),
        "quality.assess": ("质量", s.QualityObservation, "inspect", "append_quality_assessment", assess),
        "formulation.calculate": (
            "工艺与配方",
            s.CalculationInput,
            "industrial_write",
            "append_calculation",
            calculate,
        ),
        "process.select": (
            "工艺与配方",
            s.ProcessDecisionInput,
            "industrial_write",
            "append_candidate_only",
            select_mode,
        ),
        "closure.retest_intent": (
            "闭环执行",
            s.BusinessCommandInput,
            "business_write",
            "propose_intent_only",
            create,
        ),
        "supply.plan": ("计划与供应", s.SupplyPlanInput, "supply", "append_supply_plan", plan),
        "supply.inventory_intent": (
            "计划与供应",
            s.BusinessCommandInput,
            "business_write",
            "propose_intent_only",
            create,
        ),
        "finance.cost": ("财务与成本", s.CostInput, "finance", "append_cost_report", reconcile),
        "finance.purchase_intent": (
            "财务与成本",
            s.BusinessCommandInput,
            "business_write",
            "propose_intent_only",
            create,
        ),
        "knowledge.retrieve": ("知识改进", KnowledgeInput, "read", "none", knowledge),
    }
    if name not in tools:
        fail(422, "tool_not_registered", "工具必须来自固定能力注册表，禁止任意SQL、Shell、文件或HTTP动作")
    return tools[name]


NAMES = [
    "data.reconcile",
    "quality.assess",
    "formulation.calculate",
    "process.select",
    "closure.retest_intent",
    "supply.plan",
    "supply.inventory_intent",
    "finance.cost",
    "finance.purchase_intent",
    "knowledge.retrieve",
]


def registry(svc):
    svc.actor.require("read")
    return {
        "workflow_version": "industrial-tools-v1",
        "engine": "deterministic_tools",
        "capabilities": [
            {
                "tool": name,
                "role": resolve(name)[0],
                "input_schema": resolve(name)[1].model_json_schema(),
                "output_schema": InvokeResult.model_json_schema(),
                "permission": resolve(name)[2],
                "scope": "server_derived_tenant_site_line_object",
                "side_effect": resolve(name)[3],
                "preconditions": "current_domain_versions_scopes_evidence_and_authorization",
                "evidence": "validated_file_ids_and_frozen_domain_snapshots",
                "timeout_seconds": 30,
                "idempotency": "actor_and_request_key_plus_typed_input_hash; business_intents_also_have_stable_source_key",
                "errors": ["forbidden", "invalid", "stale", "timeout", "failure"],
                "external_send": "separate_digital_authorization_and_business_worker_only",
            }
            for name in NAMES
        ],
    }


def invoke(svc, data):
    role, schema, permission, effect, function = resolve(data.tool)
    result, code, status, line = {}, "", "success", ""
    started = time.monotonic()
    try:
        with svc.db.begin_nested():
            parsed = schema.model_validate(data.input)
            line = getattr(parsed, "line_id", "")
            svc.actor.require(permission, svc.settings.site_id, line)
            if data.tool == "closure.retest_intent" and parsed.payload.action != "retest.create":
                fail(422, "tool_action_contract", "闭环工具只登记复测工单意图")
            if data.tool == "supply.inventory_intent" and not parsed.payload.action.startswith("inventory."):
                fail(422, "tool_action_contract", "库存工具只登记预占或释放意图")
            if data.tool == "finance.purchase_intent" and not parsed.payload.action.startswith("purchase."):
                fail(422, "tool_action_contract", "采购工具只登记采购意图")
            result = function(svc, parsed)
            if data.tool == "data.reconcile":
                line = result["record"]["scope_line_id"]
            if not line and result.get("scope_line_id"):
                line = result["scope_line_id"]
            if time.monotonic() - started > 30:
                fail(408, "tool_timeout", "确定性工具超出预算，未提交领域结果")
    except ValidationError:
        status, code = "invalid", "tool_input_schema"
    except HTTPException as exc:
        status = {403: "forbidden", 409: "stale", 408: "timeout"}.get(exc.status_code, "invalid")
        code = (
            exc.detail.get("code", "tool_domain_error")
            if isinstance(exc.detail, dict)
            else "tool_domain_error"
        )
    except Exception:
        status, code = "failure", "tool_execution_failed"
    if status != "success":
        result = {}
    ids = data.input.get("evidence_ids", []) if isinstance(data.input.get("evidence_ids", []), list) else []
    row = svc.add(
        m.ToolInvocation,
        actor_id=svc.actor.id,
        role=role,
        tool=data.tool,
        tool_version="industrial-tools-v1",
        status=status,
        input_hash=digest(data.input),
        input=data.input,
        result={"data": result, "error_code": code},
        authorization={
            "permission": permission,
            "role": svc.actor.role,
            "side_effect": effect,
            "elapsed_ms": round((time.monotonic() - started) * 1000),
            "engine": "deterministic_tools",
        },
        evidence_ids=ids,
        scope_line_id=line
        if not svc.actor.lines or line in svc.actor.lines
        else "unassigned:" + svc.actor.id,
    )
    return InvokeResult(
        invocation_id=row.id, tool=data.tool, status=status, result=result, error_code=code
    ).model_dump(mode="json")


def record_execution(svc, task, result):
    """Only called after a real domain operation and final lease/authority checks."""
    name = (
        {
            "retest.create": "closure.retest_intent",
            "inventory.reserve": "supply.inventory_intent",
            "inventory.release": "supply.inventory_intent",
            "purchase.draft": "finance.purchase_intent",
            "purchase.submit": "finance.purchase_intent",
        }
    ).get(task.kind, task.kind)
    role = resolve(name)[0]
    return svc.add(
        m.ToolInvocation,
        actor_id=task.actor_id,
        role=role,
        tool=name,
        tool_version="industrial-tools-v1",
        status="success",
        input_hash=digest(task.input),
        input=task.input,
        result=result,
        evidence_ids=task.input.get("evidence_ids", []),
        authorization={
            "responsibility": task.responsibility,
            "task_id": task.id,
            "generation": task.target_snapshot["generation"],
            "policy_id": task.policy_id,
            "engine": "deterministic_tools",
            "external_applied": False,
        },
        scope_line_id=task.scope_line_id,
    )
