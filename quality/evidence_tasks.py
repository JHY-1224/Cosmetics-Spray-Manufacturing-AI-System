"""Evidence follow-up tasks are persisted with a diagnostic run and audited case revision."""

from fastapi import Depends, Request
from pydantic import Field
from typing import Literal
from . import models as m
from .schemas import Strict
from .security import current_time, fail
from .service import serialize


class TaskDecision(Strict):
    version: int = Field(ge=1)
    action: Literal["complete", "cancel"]
    reason: str = Field(min_length=4, max_length=2000)
    evidence_ids: list[str] = Field(default_factory=list, max_length=100)


def register_evidence_tasks(app, service, command):
    @app.post("/api/cases/{id}/evidence-tasks/{task_id}")
    def decide(id: str, task_id: str, data: TaskDecision, request: Request, svc=Depends(service)):
        svc.actor.require("case", svc.settings.site_id)

        def apply():
            case = svc.get(m.Case, id, True)
            svc.version(case, data.version)
            if case.status in {"closed", "cancelled"}:
                fail(409, "immutable_case", "已结案补证任务不可更改")
            tasks = [dict(t) for t in case.diagnosis.get("evidence_tasks", [])]
            task = next((t for t in tasks if t["id"] == task_id), None)
            if not task or task["status"] != "pending":
                fail(409, "evidence_task_state", "任务不存在、已办理或属于旧调查版本")
            if data.action == "complete":
                allowed = set(case.context.get("evidence_ids", []))
                for inspection in case.context.get("inspection_snapshots", []):
                    allowed.update(inspection.get("result", {}).get("image_ids", []))
                if not data.evidence_ids or set(data.evidence_ids) - allowed:
                    fail(
                        422, "task_evidence", "完成补证任务前须通过案件补证流程关联实际证据，旧审批会相应失效"
                    )
                svc.evidence(data.evidence_ids, case.line_id)
            task.update(
                status="completed" if data.action == "complete" else "cancelled",
                reason=data.reason,
                evidence_ids=data.evidence_ids,
                actor_id=svc.actor.id,
                decided_at=current_time().isoformat(),
            )
            case.diagnosis = {**case.diagnosis, "evidence_tasks": tasks}
            run = svc.get(m.Run, case.diagnosis["run_id"], True)
            run.data = {**run.data, "evidence_tasks": tasks}
            svc.advance(case, case.status, "evidence_task." + task["status"])
            return serialize(case)

        return command(svc, request, data.model_dump(), apply)
