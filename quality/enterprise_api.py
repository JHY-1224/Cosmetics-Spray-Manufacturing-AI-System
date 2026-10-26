"""Authenticated endpoints compose deterministic services through existing request idempotency."""

from fastapi import Depends, Request
from . import models as m
from . import enterprise_schemas as s
from . import enterprise as domain
from . import quality_decisions
from .records import COLLECTIONS
from .querying import get_scope
from .security import fail
from .service import serialize

RESOURCES = {
    "definitions": (m.DecisionDefinition, ["code", "name", "kind", "status", "revision"], "read"),
    "quality-assessments": (
        m.QualityAssessment,
        ["object_id", "outcome", "source_kind", "occurred_at"],
        "read",
    ),
    "process-decisions": (m.ProcessDecision, ["object_id", "status", "next_step_id"], "read"),
    "production-cohorts": (m.ProductionCohort, ["external_id", "product_id", "granularity"], "read"),
    "materials": (m.MaterialItem, ["code", "name", "unit"], "read"),
    "formulations": (m.MaterialFormulation, ["code", "name", "status", "revision"], "read"),
    "calculations": (m.FormulationCalculation, ["status", "basis", "formulation_id"], "read"),
    "mixing-batches": (m.MixingBatch, ["external_id", "status", "mixed_at"], "read"),
    "policies": (m.AutomationPolicy, ["code", "name", "status", "revision"], "read"),
    "policy-decisions": (m.PolicyDecision, ["action", "object_id"], "read"),
    "digital-tasks": (m.DigitalTask, ["kind", "status", "attempts", "version"], "read"),
    "connectors": (m.BusinessConnector, ["name", "system", "authority"], "business_read"),
    "business-commands": (
        m.BusinessCommand,
        ["action", "target_id", "status", "external_id"],
        "business_read",
    ),
    "business-attempts": (
        m.BusinessCommandAttempt,
        ["command_id", "kind", "status", "fencing"],
        "business_read",
    ),
    "business-receipts": (m.BusinessReceipt, ["command_id", "external_id", "status"], "business_read"),
    "boms": (m.BillOfMaterials, ["code", "name", "status", "revision"], "read"),
    "orders": (m.BusinessOrder, ["external_id", "source_system", "customer_reference"], "supply"),
    "order-lines": (m.BusinessOrderLine, ["external_line", "product_id", "bom_id", "order_id"], "supply"),
    "inventory": (m.InventorySnapshot, ["warehouse", "lot", "quality_status", "source_version"], "read"),
    "supply-plans": (m.SupplyPlan, ["order_id", "status", "version"], "supply"),
    "demands": (m.MaterialDemand, ["plan_id", "order_line_id", "material_id"], "supply"),
    "reservations": (m.InventoryReservation, ["status", "inventory_id", "source_reference"], "supply"),
    "actual-usage": (m.ActualMaterialUsage, ["source_id", "purpose", "material_id", "occurred_at"], "read"),
    "business-documents": (
        m.BusinessDocument,
        ["external_id", "kind", "currency", "source_system"],
        "finance",
    ),
    "document-lines": (m.BusinessDocumentLine, ["external_line", "material_id", "lot"], "finance"),
    "cost-reports": (m.CostReport, ["order_id", "currency", "status", "allocation_version"], "finance"),
    "tool-invocations": (m.ToolInvocation, ["tool", "role", "status", "tool_version"], "admin"),
}


def register_enterprise(app, service, command):
    for key, item in RESOURCES.items():
        COLLECTIONS["industrial-" + key] = item

    from fastapi import Response

    @app.get("/api/readiness/dependencies")
    def dependency_readiness(response: Response, svc=Depends(service)):
        from .readiness import dependencies

        result = dependencies(svc)
        response.status_code = 200 if result["ready"] else 503
        return result

    @app.get("/api/readiness/business")
    def business_readiness(response: Response, svc=Depends(service)):
        from .readiness import business

        result = business(svc)
        response.status_code = 200 if result["ready"] else 503
        return result

    @app.get("/api/industrial/status")
    def status(scope=Depends(get_scope), svc=Depends(service)):
        svc.actor.require("read")
        result = {
            "contract": "industrial-coordination-v1",
            "schema": "0012",
            "mode": svc.settings.mode,
            "equipment_control": "prohibited",
            "business_writes": "connector_and_action_authorization_required",
            "model": "configured" if svc.settings.model_url else "deterministic_tools",
            "facts_authority": svc.settings.node_role,
            "field_acceptance": "not_performed",
        }
        if svc.actor.role == "admin":
            from .operations import industrial_metrics

            result["runtime_metrics"] = industrial_metrics(svc, scope)
        return result

    @app.post("/api/industrial/definitions")
    def create_definition(data: s.DefinitionInput, request: Request, svc=Depends(service)):
        return command(svc, request, data.model_dump(mode="json"), lambda: domain.definition(svc, data))

    @app.post("/api/industrial/governance/{kind}/{id}")
    def review_definition(
        kind: str, id: str, data: s.GovernanceAction, request: Request, svc=Depends(service)
    ):
        return command(svc, request, data.model_dump(mode="json"), lambda: domain.review(svc, kind, id, data))

    @app.post("/api/industrial/quality-assessments")
    def assess(data: s.QualityObservation, request: Request, svc=Depends(service)):
        return command(
            svc, request, data.model_dump(mode="json"), lambda: quality_decisions.assess(svc, data)
        )

    @app.post("/api/industrial/quality-plans/assess")
    def assess_plan(data: s.PlanAssessmentInput, request: Request, svc=Depends(service)):
        return command(
            svc, request, data.model_dump(mode="json"), lambda: quality_decisions.assess_plan(svc, data)
        )

    @app.post("/api/industrial/production-cohorts")
    def cohort(data: s.CohortInput, request: Request, svc=Depends(service)):
        return command(
            svc, request, data.model_dump(mode="json"), lambda: quality_decisions.cohort(svc, data)
        )

    @app.post("/api/industrial/process-decisions")
    def process_decision(data: s.ProcessDecisionInput, request: Request, svc=Depends(service)):
        from .process_decisions import select_mode

        return command(svc, request, data.model_dump(mode="json"), lambda: select_mode(svc, data))

    @app.post("/api/industrial/passages/actual")
    def actual_passage(data: s.ActualPassageInput, request: Request, svc=Depends(service)):
        from .actual_passages import capture

        return command(svc, request, data.model_dump(mode="json"), lambda: capture(svc, data))

    @app.post("/api/industrial/passages/import")
    def actual_passage_import(data: s.ActualPassageBatch, request: Request, svc=Depends(service)):
        from .actual_passages import capture
        from fastapi import HTTPException

        def apply():
            results = []
            for index, item in enumerate(data.records):
                try:
                    with svc.db.begin_nested():
                        result = capture(svc, item)
                    results.append({"index": index, **result})
                except HTTPException as exc:
                    results.append({"index": index, "status": "rejected", "error": exc.detail})
            return {
                "results": results,
                "retained_count": sum(r.get("original_retained", False) for r in results),
                "normalized_count": sum(bool(r.get("passage")) for r in results),
            }

        return command(svc, request, data.model_dump(mode="json"), apply)

    @app.post("/api/industrial/materials")
    def material(data: s.MaterialItemInput, request: Request, svc=Depends(service)):
        from .material_inventory import material as create

        return command(svc, request, data.model_dump(mode="json"), lambda: create(svc, data))

    @app.post("/api/industrial/inventory")
    def inventory(data: s.InventoryInput, request: Request, svc=Depends(service)):
        from .material_inventory import inventory as create

        return command(svc, request, data.model_dump(mode="json"), lambda: create(svc, data))

    @app.post("/api/industrial/formulations")
    def formulation(data: s.FormulationInput, request: Request, svc=Depends(service)):
        from .formulations import create

        return command(svc, request, data.model_dump(mode="json"), lambda: create(svc, data))

    @app.post("/api/industrial/calculations")
    def calculate(data: s.CalculationInput, request: Request, svc=Depends(service)):
        from .formulations import calculate as run

        return command(svc, request, data.model_dump(mode="json"), lambda: run(svc, data))

    @app.post("/api/industrial/mixing-batches")
    def mix(data: s.MixingInput, request: Request, svc=Depends(service)):
        from .formulations import mix as record_mix

        return command(svc, request, data.model_dump(mode="json"), lambda: record_mix(svc, data))

    @app.post("/api/industrial/reservations")
    def reserve(data: s.ReservationInput, request: Request, svc=Depends(service)):
        from .material_inventory import reserve as propose

        return command(svc, request, data.model_dump(mode="json"), lambda: propose(svc, data))

    @app.post("/api/industrial/boms")
    def bom(data: s.BomInput, request: Request, svc=Depends(service)):
        from .supply import create

        return command(svc, request, data.model_dump(mode="json"), lambda: create(svc, data))

    @app.post("/api/industrial/orders")
    def order(data: s.OrderInput, request: Request, svc=Depends(service)):
        from .supply import order as create

        return command(svc, request, data.model_dump(mode="json"), lambda: create(svc, data))

    @app.post("/api/industrial/orders/{id}/revision")
    def order_revision(id: str, data: s.OrderRevisionInput, request: Request, svc=Depends(service)):
        from .supply import order as revise

        return command(svc, request, data.model_dump(mode="json"), lambda: revise(svc, data, id))

    @app.post("/api/industrial/supply-plans")
    def supply_plan(data: s.SupplyPlanInput, request: Request, svc=Depends(service)):
        from .supply import plan

        return command(svc, request, data.model_dump(mode="json"), lambda: plan(svc, data))

    @app.post("/api/industrial/actual-usage")
    def usage(data: s.UsageInput, request: Request, svc=Depends(service)):
        from .supply import usage as record_usage

        return command(svc, request, data.model_dump(mode="json"), lambda: record_usage(svc, data))

    @app.post("/api/industrial/business-documents")
    def document(data: s.DocumentInput, request: Request, svc=Depends(service)):
        from .costs import document as record_document

        return command(svc, request, data.model_dump(mode="json"), lambda: record_document(svc, data))

    @app.post("/api/industrial/cost-reports")
    def cost_report(data: s.CostInput, request: Request, svc=Depends(service)):
        from .costs import reconcile

        return command(svc, request, data.model_dump(mode="json"), lambda: reconcile(svc, data))

    @app.post("/api/industrial/policies")
    def policy(data: s.PolicyInput, request: Request, svc=Depends(service)):
        from .digital_tasks import create_policy

        return command(svc, request, data.model_dump(mode="json"), lambda: create_policy(svc, data))

    @app.post("/api/industrial/digital-tasks")
    def task(data: s.DigitalTaskInput, request: Request, svc=Depends(service)):
        from .digital_tasks import enqueue

        return command(svc, request, data.model_dump(mode="json"), lambda: enqueue(svc, data))

    @app.post("/api/industrial/digital-tasks/batch")
    def batch(data: s.DigitalBatchInput, request: Request, svc=Depends(service)):
        from .digital_tasks import enqueue
        from fastapi import HTTPException

        def apply():
            results = []
            batch_id = m.uid()
            for index, item in enumerate(data.tasks):
                try:
                    with svc.db.begin_nested():
                        result = enqueue(svc, item, preview=not data.execute_valid, batch_id=batch_id)
                    results.append({"index": index, **result})
                except HTTPException as exc:
                    results.append(
                        {"index": index, "status": "blocked", "error": exc.detail, "task_created": False}
                    )
            svc.audit(
                "digital.batch_prepared",
                batch_id,
                {"count": len(results), "execute_valid": data.execute_valid},
            )
            return {
                "batch_id": batch_id,
                "preview_only": not data.execute_valid,
                "results": results,
                "created_count": sum(bool(r.get("task_created")) for r in results),
                "blocked_count": sum(r["status"] == "blocked" for r in results),
            }

        return command(svc, request, data.model_dump(mode="json"), apply)

    @app.post("/api/industrial/digital-tasks/{id}/review")
    def review_task(id: str, data: s.ApprovalInput, request: Request, svc=Depends(service)):
        from .digital_tasks import approve

        return command(svc, request, data.model_dump(mode="json"), lambda: approve(svc, id, data))

    @app.post("/api/industrial/digital-tasks/{id}/control")
    def control_task(id: str, data: s.TaskAction, request: Request, svc=Depends(service)):
        from .digital_tasks import control

        return command(svc, request, data.model_dump(mode="json"), lambda: control(svc, id, data))

    @app.post("/api/industrial/connectors")
    def connector(data: s.ConnectorInput, request: Request, svc=Depends(service)):
        from .business_gateway import connector

        return command(svc, request, data.model_dump(mode="json"), lambda: connector(svc, data))

    @app.post("/api/industrial/connectors/{id}/revision")
    def revise_connector(id: str, data: s.ConnectorRevision, request: Request, svc=Depends(service)):
        from .business_gateway import connector

        return command(
            svc, request, data.model_dump(mode="json"), lambda: connector(svc, data, id, data.version)
        )

    @app.post("/api/industrial/business-commands")
    def business_command(data: s.BusinessCommandInput, request: Request, svc=Depends(service)):
        from .business_gateway import create

        return command(svc, request, data.model_dump(mode="json"), lambda: create(svc, data))

    @app.post("/api/industrial/business-commands/{id}/control")
    def business_control(id: str, data: s.CommandAction, request: Request, svc=Depends(service)):
        from .business_gateway import control

        return command(svc, request, data.model_dump(mode="json"), lambda: control(svc, id, data))

    @app.get("/api/industrial/business-runtime")
    def business_runtime(svc=Depends(service)):
        from .business_gateway import runtime

        return runtime(svc)

    @app.post("/api/industrial/reservations/{id}/control")
    def reservation_control(id: str, data: s.CommandAction, request: Request, svc=Depends(service)):
        from .material_inventory import cancel_reservation

        return command(svc, request, data.model_dump(mode="json"), lambda: cancel_reservation(svc, id, data))

    @app.post("/api/industrial/business-runtime/reconcile")
    def business_recovery(data: s.BusinessRecoveryInput, request: Request, svc=Depends(service)):
        from .business_gateway import unfreeze

        return command(svc, request, data.model_dump(mode="json"), lambda: unfreeze(svc, data))

    @app.post("/api/industrial/business-callbacks/{id}")
    async def business_callback(id: str, request: Request):
        import hmac
        import json
        from .business_gateway import credential, receive, safe
        from .security import Actor
        from .service import Service

        with app.state.database.session() as db:
            svc = Service(
                db, app.state.settings, Actor("business-callback", "admin", app.state.settings.sites)
            )
            row = svc.get(m.BusinessCommand, id, True)
            conn = svc.get(m.BusinessConnector, row.connector_id)
            secret = credential(conn)
            if not hmac.compare_digest(request.headers.get("authorization", ""), "Bearer " + secret):
                fail(403, "business_callback_auth", "缺少有效的业务连接身份凭据")
            import asyncio

            raw = bytearray()
            try:
                async with asyncio.timeout(app.state.settings.business_timeout):
                    async for chunk in request.stream():
                        raw.extend(chunk)
                        if len(raw) > 65536:
                            fail(413, "business_receipt_size", "业务回执超出固定合同大小")
            except TimeoutError:
                fail(408, "business_receipt_timeout", "业务回执接收超过固定时间预算")
            try:
                envelope = json.loads(raw)
                outcome = receive(svc, row, envelope, secret)
            except (ValueError, TypeError, KeyError):
                fail(422, "business_receipt_contract", "回执不符合签名强类型合同")
            row.version += 1
            return {"outcome": outcome, "command": safe(row)}

    @app.get("/api/industrial/tools")
    def tools(svc=Depends(service)):
        from .industrial_tools import registry

        return registry(svc)

    from .industrial_tools import InvokeInput

    @app.post("/api/industrial/tools/invoke")
    def invoke_tool(data: InvokeInput, request: Request, svc=Depends(service)):
        from .industrial_tools import invoke

        return command(svc, request, data.model_dump(mode="json"), lambda: invoke(svc, data))

    @app.get("/api/industrial/{resource}/{id}")
    def record(resource: str, id: str, svc=Depends(service)):
        if resource not in RESOURCES:
            fail(404, "industrial_resource", "不支持任意表查询")
        cls, _, permission = RESOURCES[resource]
        svc.actor.require(permission)
        row = svc.get(cls, id)
        if cls == m.DigitalTask:
            from .digital_tasks import can_read

            if not can_read(svc.actor, row.kind):
                fail(403, "digital_task_scope", "没有本类数字任务的读取或审核权限")
        if cls == m.PolicyDecision:
            from .digital_tasks import can_read

            if not can_read(svc.actor, row.action):
                fail(403, "policy_decision_scope", "没有本类业务策略判定的读取权限")
        kind = {
            "definitions": "definition",
            "formulations": "formulation",
            "policies": "policy",
            "boms": "bom",
        }.get(resource)
        result = domain.detail(svc, kind, row) if kind else serialize(row)
        if resource == "inventory":
            from .material_inventory import available, reconciled_usage_ids

            result["included_usage_ids"] = reconciled_usage_ids(svc, row)
            result["availability"] = domain.json_value(available(svc, row))
        elif resource == "orders":
            from .supply import order_detail

            result = order_detail(svc, row)
        elif resource == "supply-plans":
            from .supply import plan_validation

            result["current_validation"] = plan_validation(svc, row)
            result["demands"] = [
                serialize(r)
                for r in svc.db.scalars(svc.scoped(m.MaterialDemand).where(m.MaterialDemand.plan_id == id))
            ]
        elif resource == "business-documents":
            result["lines"] = [
                serialize(r)
                for r in svc.db.scalars(
                    svc.scoped(m.BusinessDocumentLine).where(m.BusinessDocumentLine.document_id == id)
                )
            ]
        return {k: v for k, v in result.items() if k not in {"authorization_hash", "lease_token"}}
