from types import SimpleNamespace

from fastapi import Depends, Request

from . import models as m
from .agent import score_candidates, validate_rules
from .scopes import applicable
from .security import current_time, aware, digest, fail


def register_regression(app, service, command):
    @app.post("/api/knowledge/{id}/evaluate")
    def evaluate(id: str, data: dict, request: Request, svc=Depends(service)):
        svc.actor.require("knowledge", svc.settings.site_id)
        rule = svc.get(m.Knowledge, id)
        if rule.category != "root_cause":
            fail(422, "regression_category", "本评估仅适用确定性根因规则，不评价模型准确率")
        validate_rules(rule.rules)
        ids = data.get("case_ids", [])
        if set(data) - {"case_ids"} or not isinstance(ids, list) or len(ids) > 200:
            fail(422, "regression_input", "请选择不超过200个已授权的历史案件")

        def apply():
            cases = (
                [svc.get(m.Case, key) for key in ids]
                if ids
                else svc.db.scalars(svc.scoped(m.Case).order_by(m.Case.created_at.desc()).limit(20)).all()
            )
            results = []
            for case in cases:
                from .learning import require_not_holdout
                require_not_holdout(svc, case)
                snapshot = case.context.get("analysis_snapshot", {})
                measurements = [SimpleNamespace(**r) for r in snapshot.get("measurements", [])]
                historical = [SimpleNamespace(**k) for k in snapshot.get("knowledge", [])]
                before = score_candidates(historical, case.context["defect"], measurements, case.process, snapshot.get("anchor"))
                fits = (
                    applicable(rule.scope, svc.settings, case.process, case.line_id, case.context)
                    and aware(rule.valid_until) > current_time()
                )
                after_rules = [k for k in historical if k.id not in (rule.id, rule.previous_id)]
                if fits:
                    after_rules.append(rule)
                after = score_candidates(after_rules, case.context["defect"], measurements, case.process, snapshot.get("anchor"))
                results.append(
                    {
                        "case_id": case.id,
                        "case_version": case.version,
                        "snapshot_hash": digest(snapshot),
                        "applicable": fits,
                        "changed": before != after,
                        "before": before,
                        "after": after,
                    }
                )
            result = {
                "rule_id": id,
                "revision": rule.revision,
                "evaluated_at": current_time().isoformat(),
                "sample_count": len(results),
                "changed_count": sum(r["changed"] for r in results),
                "results": results,
                "mode": "deterministic_historical_regression",
                "limitations": "仅比较历史证据下候选排序与缺证变化；不调用模型、不修改案件、不构成准确率或因果验证。无快照案件仅评价规则匹配。",
            }
            svc.audit(
                "rule.regression_evaluated",
                id,
                {
                    "report_hash": digest(result),
                    "sample_count": len(results),
                    "changed_count": result["changed_count"],
                },
            )
            return result

        return command(svc, request, data, apply)
