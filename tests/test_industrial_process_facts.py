"""Actual abnormal facts survive; approved suggestions cannot rewrite production history."""

from datetime import timedelta
import pytest
from quality.security import Actor, current_time
from quality.db import Database
from quality.service import Service
from quality.ingestion import recover_raw
from quality.actual_passages import capture
from quality.enterprise_schemas import ActualPassageInput
from .conftest import post
from .test_delivery_integrity import published_route
from .test_industrial_quality import original, publish


def passage_body(line, route, file, **patch):
    return {
        "line_id": line["id"],
        "object_id": "ACTUAL-TEST-1",
        "granularity": "bottle",
        "route_id": route["id"],
        "step_id": route["steps"][0]["id"],
        "source_id": "source-mes-test",
        "source_session_id": "run-test-1",
        "source_record_id": "event-1",
        "reported_sequence": 1,
        "entered_at": (current_time() - timedelta(minutes=3)).isoformat(),
        "exited_at": (current_time() - timedelta(minutes=2)).isoformat(),
        "evidence_ids": [file],
        "actual_confirmation": True,
        "notes": "软件验证已实际发生记录，仅为合成夹具",
        **patch,
    }


def test_actual_bad_route_late_fact_duplicate_and_conflict_are_retained(core):
    settings, _, clients, lines = core
    line = lines[0]
    route = published_route(clients, line)
    file = original(clients)
    body = passage_body(line, route, file)
    first = post(clients["engineer"], "/industrial/passages/actual", body)
    assert first["passage"]["data"]["conformance"] == "consistent"
    again = post(clients["engineer"], "/industrial/passages/actual", body)
    assert again["passage"]["id"] == first["passage"]["id"]
    changed = post(
        clients["engineer"], "/industrial/passages/actual", {**body, "notes": "同一源记录出现冲突的修改声明"}
    )
    assert changed["raw_status"] == "conflict" and changed["passage"] is None
    assert changed["original_retained"]
    wrong = post(
        clients["engineer"],
        "/industrial/passages/actual",
        {
            **body,
            "source_record_id": "event-wrong-route",
            "route_id": "unmapped-original-route",
            "step_id": "original-reported-step",
            "reported_sequence": 8,
        },
    )
    assert wrong["passage"]["route_id"] == "unmapped-original-route"
    assert wrong["passage"]["data"]["conformance"] == "review"
    assert "unapproved_or_unmapped_route" in wrong["passage"]["data"]["annotations"]
    assert "cycle" not in wrong["passage"]["data"]
    late = post(
        clients["engineer"],
        "/industrial/passages/actual",
        {
            **body,
            "source_record_id": "late-event",
            "reported_sequence": 2,
            "entered_at": (current_time() - timedelta(days=1)).isoformat(),
            "exited_at": (current_time() - timedelta(days=1) + timedelta(minutes=1)).isoformat(),
        },
    )
    assert "late_or_overlapping_fact" in late["passage"]["data"]["annotations"]
    trace = clients["engineer"].get("/api/trace/ACTUAL-TEST-1").json()
    assert len(trace["passages"]) == 3
    assert len(list((settings.data_dir / "ingestion-raw").glob("*.json"))) == 4


def test_fsynced_actual_fact_recovers_after_database_transaction_failure(core):
    settings, _, clients, lines = core
    line = lines[0]
    route = published_route(clients, line)
    file = original(clients)
    actor_id = clients["engineer"].get("/api/auth/me").json()["id"]
    db = Database(settings)
    body = ActualPassageInput.model_validate(passage_body(line, route, file))
    with pytest.raises(RuntimeError, match="injected precommit crash"):
        with db.session() as session:
            svc = Service(session, settings, Actor(actor_id, "engineer", [settings.site_id]))
            assert capture(svc, body)["passage"]
            raise RuntimeError("injected precommit crash")
    assert clients["admin"].get("/api/records/passages").json()["total"] == 0
    assert len(list((settings.data_dir / "ingestion-raw").glob("*.json"))) == 1
    with db.session() as session:
        assert recover_raw(Service(session, settings, Actor("recovery", "admin", [settings.site_id]))) == 1
    assert clients["admin"].get("/api/records/passages").json()["total"] == 1
    assert len(list((settings.data_dir / "ingestion-raw").glob("*.done"))) == 1
    with db.session() as session:
        assert recover_raw(Service(session, settings, Actor("recovery", "admin", [settings.site_id]))) == 0


def test_process_mode_is_candidate_only_and_every_applicable_sop_constrains_it(core):
    _, _, clients, lines = core
    line = lines[0]
    route = published_route(clients, line)
    file = original(clients)

    def sop(name, low, step_ids=None):
        row = post(
            clients["engineer"],
            "/knowledge",
            {
                "category": "sop",
                "title": name,
                "content": "仅验证批准参数窗口，不是客户生产值",
                "process": line["data"]["process"],
                "scope": {"line_ids": [line["id"]], "step_ids": step_ids or []},
                "source": {"provenance": "reference"},
                "rules": {"parameters": {"test-window": {"min": low, "max": 3, "unit": "test-unit"}}},
            },
        )
        post(
            clients["engineer"],
            f"/knowledge/{row['id']}/review",
            {"action": "submit", "reason": "提交合成窗口"},
        )
        return post(
            clients["approver"],
            f"/knowledge/{row['id']}/review",
            {"action": "publish", "reason": "独立审核合成参数边界"},
        )

    approved = sop("软件验证窗口一", 1)
    mode = publish(
        clients,
        line,
        file,
        {
            "kind": "process_mode",
            "route_id": route["id"],
            "from_step_id": route["steps"][0]["id"],
            "next_step_id": route["steps"][1]["id"],
            "sop_ids": [approved["id"]],
            "parameters": {"test-window": "2"},
            "parameter_units": {"test-window": "test-unit"},
        },
        "MODE",
    )
    body = {
        "line_id": line["id"],
        "route_id": route["id"],
        "mode_id": mode["id"],
        "object_id": "actual-object",
        "product_id": "software-product",
        "from_step_id": route["steps"][0]["id"],
    }
    decision = post(clients["engineer"], "/industrial/process-decisions", body)
    assert decision["status"] == "candidate"
    assert decision["snapshot"]["authorization"] == "not_granted"
    assert clients["admin"].get("/api/records/passages").json()["total"] == 0
    from .test_business_gateway import policy
    from quality.digital_tasks import run_once

    policy(clients, line, file, ["process.select"], "operation")
    task = post(clients["admin"], "/industrial/digital-tasks", {
        "line_id": line["id"], "action": "process.select", "object_id": body["object_id"],
        "object_version": str(mode["version"]), "quantity": "1", "amount": "0", "currency": "CNY",
        "evidence_ids": [file], "payload": body,
    })["task"]
    additional = sop("下一工序适用窗口，即使没有被检索排名选中也必须限制", 2.5, [route["steps"][1]["id"]])
    blocked = post(clients["engineer"], "/industrial/process-decisions", body)
    assert blocked["status"] == "blocked"
    assert any("共同窗口" in reason for reason in blocked["reasons"])
    assert {r["id"] for r in blocked["snapshot"]["sops"]} == {approved["id"], additional["id"]}
    assert len(blocked["snapshot"]["all_applicable_sop_windows"]["test-window"]) == 2
    assert run_once(core[0], Database(core[0]))
    failed = clients["admin"].get("/api/industrial/digital-tasks/" + task["id"]).json()
    assert failed["status"] == "failed" and failed["error_code"] == "digital_target_stale"
    assert clients["admin"].get("/api/records/industrial-process-decisions").json()["total"] == 2

    empty_mode = publish(clients, line, file, {
        **mode["payload"], "parameters": {}, "parameter_units": {},
    }, "MODE-MISSING-PARAMETER")
    missing = post(clients["engineer"], "/industrial/process-decisions", {**body, "mode_id": empty_mode["id"]})
    assert missing["status"] == "blocked"
    assert any("未明确提供" in reason for reason in missing["reasons"])
