from datetime import UTC, datetime, timedelta
from .conftest import post
from .test_workflow import case, file
from sqlalchemy import select
from quality.db import Database
from quality.learning_tasks import run_once


def drain(settings):
    db = Database(settings)
    for _ in range(30):
        if not run_once(settings, db):
            break
    else:
        raise AssertionError("learning queue did not drain")


def review(client, row, action, **kwargs):
    return post(
        client,
        f"/learning/{row['id']}/review",
        {
            "version": row["version"],
            "action": action,
            "reason": "合成开发夹具独立核对，不是现场验收",
            **kwargs,
        },
    )


def publish_condition(core, stable_id="actual_air", data_type="number", unit="degC"):
    settings, _, clients, lines = core
    line = lines[0]
    body = {
        "stable_id": stable_id,
        "name": "区段空气温度，不是瓶体温度",
        "scope": {"line_id": line["id"], "process": line["data"]["process"]},
        "dimension": "process",
        "data_type": data_type,
        "unit": unit,
        "value_type": "manual",
        "event_semantics": "操作人员核对仪表记录后人工录入",
        "quality_required": "manual_verified",
        "meaning": "仅用来验证字典版本、性质和缺测处理，未提供生产参数",
    }
    row = post(clients["engineer"], "/learning/conditions", body)
    row = review(clients["engineer"], row, "submit")
    row = review(clients["approver"], row, "publish")
    assert row["status"] == "pending_apply"
    drain(settings)
    row = clients["engineer"].get("/api/learning/" + row["id"]).json()
    assert row["status"] == "effective"
    return row


def value(definition, value, **extra):
    stamp = datetime.now(UTC) - timedelta(seconds=10)
    return {
        "definition_id": definition["id"],
        "definition_version": definition["revision"],
        "value": value,
        "unit": definition["payload"]["unit"],
        "occurred_at": stamp.isoformat(),
        "available_at": stamp.isoformat(),
        "quality": "manual_verified",
        "source_kind": "manual",
        "reason": "合成仪表读数仅用于可重复功能验证",
        **extra,
    }


def opinion():
    return {
        "expert_name": "软件验证专家",
        "dimension": "process",
        "hypothesis": "候选原因尚待现场核实",
        "checked_facts": "核查了本案已保留的原始输入",
        "check_process": "核对记录与反证，未自动认定因果",
        "applicability": "仅适用本测试工艺及产线",
        "unknowns": "没有真实客户样本",
    }


def test_expert_draft_extraction_independent_activation_reuse_revision_and_rollback(core):
    settings, _, clients, lines = core
    engineer = clients["engineer"]
    approver = clients["approver"]
    c = case(engineer, lines[0])
    evidence = file(engineer)
    result = post(
        engineer,
        "/learning/materials",
        {"case_id": c["id"], "file_id": evidence, "title": "专家现场说明原件"},
    )
    assert result["record"]["status"] == "queued"
    drain(settings)
    material = engineer.get("/api/learning/" + result["record"]["id"]).json()
    assert material["status"] == "needs_check" and material["snapshot"]["machine"]["text"]
    body = {
        "case_id": c["id"],
        "case_version": c["version"],
        "title": "颗粒人工经验候选",
        "opinions": [opinion()],
        "material_ids": [material["id"]],
        "conclusion": "failed",
        "outcome_notes": "反例：本次整改未证明改善",
        "other_factors": ["材料与环境同时变化，无法唯一归因"],
    }
    post(engineer, "/learning/experiences", body, expected=409)
    material = post(
        engineer,
        f"/learning/materials/{material['id']}/check",
        {
            "version": material["version"],
            "reviewed_text": "专家核对后的候选原文",
            "confirmed_fields": ["说明段落"],
            "reason": "已逐项核对原件，提取没有成为测量",
        },
    )
    original_text = material["snapshot"]["machine"]["text"]
    row = post(engineer, "/learning/experiences", body, key="expert-experience-original")
    assert post(engineer, "/learning/experiences", body, key="expert-experience-original")["id"] == row["id"]
    assert row["snapshot"]["case"]["version"] == c["version"]
    row = post(
        engineer,
        f"/learning/{row['id']}/save",
        {"version": row["version"], "payload": {**body, "feedback": "保留不同意见及失败结果"}},
    )
    row = review(engineer, row, "submit")
    post(
        engineer,
        f"/learning/{row['id']}/review",
        {"version": row["version"], "action": "publish", "reason": "不能自审自己的贡献"},
        expected=403,
    )
    before = engineer.get(
        "/api/knowledge-search",
        params={"q": "颗粒人工经验候选", "process": c["process"], "line_id": c["line_id"]},
    ).json()
    assert not any(r["entry"]["source"].get("learning_record_id") == row["id"] for r in before)
    row = review(approver, row, "publish")
    assert row["status"] == "pending_apply" and row["knowledge_id"] is None
    drain(settings)
    row = engineer.get("/api/learning/" + row["id"]).json()
    assert row["status"] == "effective" and row["knowledge"]["status"] == "published"
    assert (
        row["activation"]["keyword_status"] == "active"
        and row["activation"]["vector_status"] == "not_requested"
    )
    found = engineer.get(
        "/api/knowledge-search",
        params={"q": "颗粒人工经验候选", "process": c["process"], "line_id": c["line_id"]},
    ).json()
    assert row["knowledge_id"] in [r["entry"]["id"] for r in found]
    post(
        approver,
        f"/knowledge/{row['knowledge_id']}/review",
        {"action": "retire", "reason": "旁路接口不能废止专家治理版本"},
        expected=409,
    )
    another = case(engineer, lines[0])
    analysis = post(engineer, f"/cases/{another['id']}/diagnose", {"version": another["version"]})
    assert any(r["record_id"] == row["id"] for r in analysis["diagnosis"]["experience_references"])
    frozen = analysis["context"]["analysis_snapshot"]
    revision = post(
        engineer,
        f"/learning/{row['id']}/revision",
        {"version": row["version"], "reason": "追加真实结果反馈并保留原版本"},
    )
    revision = post(
        engineer,
        f"/learning/{revision['id']}/save",
        {
            "version": revision["version"],
            "payload": {
                **revision["payload"],
                "title": "颗粒人工经验修订",
                "feedback": "失败经验和分歧保留，不自动变成根因标签",
            },
        },
    )
    revision = review(engineer, revision, "submit")
    revision = review(approver, revision, "publish")
    drain(settings)
    revision = engineer.get("/api/learning/" + revision["id"]).json()
    assert engineer.get("/api/learning/" + row["id"]).json()["status"] == "retired"
    assert engineerer_snapshot(engineer, another["id"]) == frozen
    revision = review(approver, revision, "rollback")
    assert engineer.get("/api/learning/" + row["id"]).json()["status"] == "effective"
    assert (
        engineer.get("/api/learning/" + material["id"]).json()["snapshot"]["machine"]["text"] == original_text
    )
    assert engineer.get("/api/learning/status").json()["model_training"] == "not_enabled_not_validated"


def engineerer_snapshot(client, id):
    return client.get("/api/cases/" + id).json()["context"]["analysis_snapshot"]


def test_condition_types_units_quality_and_frozen_meaning(core):
    settings, _, clients, lines = core
    engineer = clients["engineer"]
    definition = publish_condition(core)
    c = case(engineer, lines[0])
    assert c["context"]["condition_dictionary"][0]["revision"] == 1
    post(
        engineer,
        f"/cases/{c['id']}/conditions",
        {"version": c["version"], "values": [value(definition, 12, unit="K")]},
        expected=422,
    )
    post(
        engineer,
        f"/cases/{c['id']}/conditions",
        {"version": c["version"], "values": [value(definition, True)]},
        expected=422,
    )
    recorded = post(
        engineer, f"/cases/{c['id']}/conditions", {"version": c["version"], "values": [value(definition, 0)]}
    )
    assert recorded["conditions"]["fields"][0]["observation"]["value"] == 0
    assert recorded["conditions"]["fields"][0]["observation"]["source_kind"] == "manual"
    assert (
        recorded["case"]["version"] == c["version"]
    )  # A noncritical observation does not invalidate approved context.
    revision = post(
        engineer,
        f"/learning/{definition['id']}/revision",
        {"version": definition["version"], "reason": "改变字段含义须有独立新版本"},
    )
    revision = post(
        engineer,
        f"/learning/{revision['id']}/save",
        {
            "version": revision["version"],
            "payload": {**revision["payload"], "meaning": "新版仅作字典版本验证，不能改写旧案例"},
        },
    )
    revision = review(engineer, revision, "submit")
    review(clients["approver"], revision, "publish")
    drain(settings)
    old = engineer.get(f"/api/cases/{c['id']}/conditions").json()
    assert old["definitions"][0]["id"] == definition["id"] and old["definitions"][0]["revision"] == 1
    future = case(engineer, lines[0])
    assert future["context"]["condition_dictionary"][0]["revision"] == 2
    assert future["context"]["condition_dictionary"][0]["id"] == revision["id"]
    bad = post(
        engineer,
        f"/cases/{c['id']}/conditions",
        {"version": c["version"], "values": [value(definition, 3, quality="Bad")]},
    )
    assert bad["conditions"]["fields"][0]["state"] == "invalid_quality"
    boolean = publish_condition(core, "cleaning_checked", "boolean", "")
    future = case(engineer, lines[0])
    response = post(
        engineer,
        f"/cases/{future['id']}/conditions",
        {"version": future["version"], "values": [value(boolean, False)]},
    )
    assert (
        next(f for f in response["conditions"]["fields"] if f["id"] == boolean["id"])["observation"]["value"]
        is False
    )


def test_rule_replay_requires_reviewed_three_way_data_and_prevents_holdout_reuse(core):
    settings, _, clients, lines = core
    engineer = clients["engineer"]
    approver = clients["approver"]
    definition = publish_condition(core)
    cases = []
    for reading in [0, 10, 0]:
        c = case(engineer, lines[0])
        post(
            engineer,
            f"/cases/{c['id']}/conditions",
            {"version": c["version"], "values": [value(definition, reading)]},
        )
        cases.append(c)
    stamp = datetime.now(UTC).isoformat()
    scope = {"line_id": lines[0]["id"], "process": lines[0]["data"]["process"]}
    dataset = post(
        engineer,
        "/learning/datasets",
        {
            "title": "三个合成开发样本",
            "scope": scope,
            "split": "development",
            "usage_rights": "合成数据，仅限功能开发",
            "target_definition": "候选规则是否满足，非现场因果标签",
            "items": [
                {
                    "case_id": c["id"],
                    "case_version": c["version"],
                    "sample_role": role,
                    "expected_match": expected,
                    "label_basis": "合成固定身份与人工读数，非现场诊断答案",
                    "decision_time": stamp,
                    "label_time": stamp,
                }
                for c, role, expected in zip(cases, ["normal", "abnormal", "failed"], [False, True, False])
            ],
        },
    )
    dataset = review(approver, dataset, "publish", confirm_labels=True)
    rule = post(
        engineer,
        "/learning/rules",
        {
            "title": "空气记录候选规则",
            "scope": scope,
            "definition_ids": [definition["id"]],
            "rationale": "开发夹具只证明合同和状态，不是生产阈值",
            "rule": {
                "dimension": "process",
                "defects": ["颗粒"],
                "cause": "温度候选待核查",
                "next_checks": ["人工核对反证"],
                "conditions": [
                    {
                        "metric": "actual_air",
                        "operator": "gt",
                        "value": 5,
                        "unit": "degC",
                        "value_type": "manual",
                    }
                ],
                "relative_score": 20,
            },
        },
    )
    post(
        engineer,
        f"/learning/{rule['id']}/review",
        {"version": rule["version"], "action": "submit", "reason": "没有回放时不得提交发布"},
        expected=409,
    )
    job = post(
        engineer, f"/learning/{rule['id']}/replay", {"version": rule["version"], "dataset_id": dataset["id"]}
    )
    drain(settings)
    report = engineer.get("/api/learning-tasks/" + job["id"]).json()
    assert report["status"] == "succeeded", report
    assert report["result"]["functional_gate_passed"] and report["result"]["after"]["false_positive"] == 0
    assert (
        report["result"]["sample_count"] == 3
        and report["result"]["independent_effect_evaluation"] == "not_performed"
    )
    rule = engineer.get("/api/learning/" + rule["id"]).json()
    rule = review(engineer, rule, "submit")
    review(approver, rule, "publish")
    drain(settings)
    assert engineer.get("/api/learning/" + rule["id"]).json()["status"] == "effective"
    heldcase = case(engineer, lines[0])
    data = {
        "title": "独立保留样本",
        "scope": scope,
        "split": "locked_test",
        "usage_rights": "独立合成保留集，仅验证隔离",
        "target_definition": "独立锁定字段和身份，未做效果实验",
        "items": [
            {
                "case_id": heldcase["id"],
                "case_version": heldcase["version"],
                "sample_role": "abnormal",
                "expected_match": True,
                "label_basis": "仅用于独立隔离机制核对",
                "decision_time": stamp,
                "label_time": stamp,
            }
        ],
    }
    held = post(clients["admin"], "/learning/datasets", data)
    assert clients["viewer"].get("/api/learning/" + held["id"]).status_code == 403
    experience = post(
        engineer,
        "/learning/experiences",
        {
            "case_id": heldcase["id"],
            "case_version": heldcase["version"],
            "title": "保留集禁止回流",
            "opinions": [opinion()],
        },
    )
    post(
        engineer,
        f"/learning/{experience['id']}/review",
        {"version": experience["version"], "action": "submit", "reason": "不得用保留集形成通用经验"},
        expected=409,
    )
    post(
        engineer,
        "/learning/datasets",
        {**data, "title": "同源样本不能换集", "split": "development"},
        expected=409,
    )
    # Even the generic evidence reader cannot reveal independent evaluation answers.
    assert clients["viewer"].get("/api/evidence-records/learning/" + held["id"]).status_code == 403


def test_durable_job_authorization_cancel_and_retry_do_not_fake_effective(core):
    import json
    from fastapi.testclient import TestClient
    from quality import models as m

    settings, app, clients, lines = core
    engineer = clients["engineer"]
    c = case(engineer, lines[0])
    row = post(
        engineer,
        "/learning/experiences",
        {
            "case_id": c["id"],
            "case_version": c["version"],
            "title": "待生效任务授权",
            "opinions": [opinion()],
        },
    )
    row = review(engineer, row, "submit")
    row = review(clients["approver"], row, "publish")
    jobs = clients["approver"].get("/api/records/learning-tasks").json()
    job = jobs["items"][0]
    cancelled = post(
        clients["approver"],
        f"/learning-tasks/{job['id']}/action",
        {"action": "cancel", "reason": "取消排队，不能误报已生效"},
    )
    assert cancelled["status"] == "cancelled"
    assert engineer.get("/api/learning/" + row["id"]).json()["status"] == "apply_failed"
    post(
        clients["approver"],
        f"/learning-tasks/{job['id']}/action",
        {"action": "retry", "reason": "按当前审核授权重新应用"},
    )
    reviewer = clients["approver"].get("/api/auth/me").json()["id"]
    with Database(settings).session() as db:
        user = db.get(m.User, reviewer)
        user.allowed_lines = [lines[1]["id"]]
        user.auth_revision += 1
    drain(settings)
    with Database(settings).session() as db:
        failed = db.get(m.LearningTask, job["id"])
        assert failed.status == "failed" and failed.error_code == "task_authorization"
        assert db.get(m.LearningRecord, row["id"]).status == "apply_failed"
    password = json.loads((settings.data_dir / "demo-credentials.json").read_text())["approver"]
    narrowed = TestClient(app)
    post(narrowed, "/auth/login", {"username": "approver", "password": password})
    assert narrowed.get("/api/learning/" + row["id"]).status_code == 404


def test_worker_timeout_does_not_block_ingestion_or_api_and_preserves_raw(core, monkeypatch):
    """An actual delayed child is fault injected; this does not benchmark a real parser or ASR."""
    import subprocess
    import sys
    import time
    from concurrent.futures import ThreadPoolExecutor
    from fastapi.testclient import TestClient
    from quality import learning_tasks, models as m
    from .test_visual import PNG
    from quality.security import current_time
    from .conftest import post
    from uuid import uuid4

    settings, app, clients, lines = core
    settings.learning_timeout = 2
    engineer, admin = clients["engineer"], clients["admin"]
    c = case(engineer, lines[0])
    original = file(engineer)
    created = post(
        engineer,
        "/learning/materials",
        {"case_id": c["id"], "file_id": original, "title": "超时故障注入资料"},
    )
    adapter = post(
        admin,
        "/adapters",
        {
            "name": "超时期间视觉采集夹具",
            "protocol": "visual",
            "config": {"line_id": lines[0]["id"]},
            "provenance": "reference",
            "enabled": True,
        },
    )
    source = TestClient(app)
    headers = {"Authorization": "Bearer " + adapter["token"], "Idempotency-Key": str(uuid4())}
    image = source.post(
        "/api/ingest/visual/" + adapter["id"] + "/files",
        files={"file": ("synthetic.png", PNG, "image/png")},
        headers=headers,
    )
    assert image.status_code == 200
    real_popen = subprocess.Popen

    def delayed_child(args, **kwargs):
        if len(args) > 2 and args[1:3] == ["-m", "quality.learning_tasks"]:
            args = [sys.executable, "-c", "import time;time.sleep(20)"]
        return real_popen(args, **kwargs)

    monkeypatch.setattr(learning_tasks.subprocess, "Popen", delayed_child)
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=1) as pool:
        job = pool.submit(learning_tasks.run_once, settings, Database(settings))
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            tasks = engineer.get("/api/records/learning-tasks").json()["items"]
            if tasks and tasks[0]["status"] == "running":
                break
            time.sleep(0.02)
        else:
            raise AssertionError("isolated task did not run")
        for n in range(12):
            response = source.post(
                "/api/ingest/visual/" + adapter["id"],
                json={
                    "record_id": f"timeout-ingestion-{n}",
                    "line_id": lines[0]["id"],
                    "object_id": f"SYNTHETIC-{n}",
                    "granularity": "bottle",
                    "association": "physical_tracking",
                    "occurred_at": current_time().isoformat(),
                    "status": "OK",
                    "defects": [],
                    "image_ids": [image.json()["id"]],
                    "model_version": "synthetic",
                    "config_version": "fixture-v1",
                    "inspected_count": 1,
                    "defective_count": 0,
                    "process": lines[0]["data"]["process"],
                },
                headers={**headers, "Idempotency-Key": str(uuid4())},
            )
            assert response.status_code == 200, response.text
            assert admin.get("/api/records/inspections?page_size=1").status_code == 200
        assert job.result(timeout=5)
    assert time.monotonic() - started < 8
    tasks = engineer.get("/api/records/learning-tasks").json()["items"]
    task = next(t for t in tasks if t["id"] == created["task"]["id"])
    assert task["status"] == "failed" and task["error_code"] == "task_timeout"
    assert engineer.get("/api/learning/" + created["record"]["id"]).json()["status"] == "parse_failed"
    assert engineer.get("/api/files/" + original).content == b"manual measurement evidence"
    with Database(settings).session() as db:
        assert len(db.scalars(select(m.RawRecord).where(m.RawRecord.source_id == adapter["id"])).all()) == 12


def test_material_originals_cannot_escape_holdout_and_features_do_not_backdate(core):
    from quality.security import current_time
    from .conftest import post

    settings, _, clients, lines = core
    engineer, admin = clients["engineer"], clients["admin"]
    original = file(engineer)
    c = post(
        engineer,
        "/cases",
        {
            "title": "独立保留原件案例",
            "line_id": lines[0]["id"],
            "process": lines[0]["data"]["process"],
            "defect": "颗粒",
            "evidence_ids": [original],
        },
    )
    now = current_time().isoformat()
    post(
        admin,
        "/learning/datasets",
        {
            "title": "封存前独立原件组",
            "scope": {"line_id": lines[0]["id"], "process": lines[0]["data"]["process"]},
            "split": "locked_test",
            "usage_rights": "软件合成数据独立验证",
            "target_definition": "源组不能进入知识",
            "items": [
                {
                    "case_id": c["id"],
                    "case_version": c["version"],
                    "sample_role": "abnormal",
                    "expected_match": True,
                    "label_basis": "合成锁定来源组",
                    "decision_time": now,
                    "label_time": now,
                }
            ],
        },
    )
    another = case(engineer, lines[0])
    created = post(
        engineer,
        "/learning/materials",
        {"case_id": another["id"], "file_id": original, "title": "不得回流的同一原件"},
    )
    drain(settings)
    material = engineer.get("/api/learning/" + created["record"]["id"]).json()
    checked = post(
        engineer,
        f"/learning/materials/{material['id']}/check",
        {
            "version": material["version"],
            "reviewed_text": "人工说明，原件仍属于锁定组",
            "reason": "核对原件出处",
        },
    )
    post(
        engineer,
        "/learning/experiences",
        {
            "title": "拒绝绕开原件分组",
            "case_id": another["id"],
            "case_version": another["version"],
            "material_ids": [checked["id"]],
            "opinions": [opinion()],
        },
        expected=409,
    )
    definition = publish_condition(core)
    fresh = case(engineer, lines[0])
    before = current_time()
    recorded = post(
        engineer,
        f"/cases/{fresh['id']}/conditions",
        {
            "version": fresh["version"],
            "values": [
                value(
                    definition,
                    0,
                    occurred_at=(before - timedelta(minutes=5)).isoformat(),
                    available_at=(before - timedelta(minutes=5)).isoformat(),
                )
            ],
        },
    )
    data = post(
        engineer,
        "/learning/datasets",
        {
            "title": "信息可用时点隔离",
            "scope": definition["payload"]["scope"],
            "split": "development",
            "usage_rights": "软件合成数据开发核对",
            "target_definition": "不使用事后录入条件",
            "items": [
                {
                    "case_id": fresh["id"],
                    "case_version": recorded["case"]["version"],
                    "sample_role": "normal",
                    "expected_match": False,
                    "label_basis": "时点前尚未录入，不得回填特征",
                    "decision_time": before.isoformat(),
                    "label_time": current_time().isoformat(),
                }
            ],
        },
    )
    assert data["snapshot"]["items"][0]["feature_measurements"] == []


def test_production_dataset_roles_require_real_facts_not_functional_labels(core):
    from quality.security import current_time

    settings, _, clients, lines = core
    c = case(clients["engineer"], lines[0])
    now = current_time().isoformat()
    body = {
        "title": "禁止用口头标签冒充生产对照",
        "scope": {"line_id": lines[0]["id"], "process": lines[0]["data"]["process"]},
        "split": "development",
        "usage_rights": "只用临时软件合成事实",
        "target_definition": "需要检验或复测依据",
        "items": [
            {
                "case_id": c["id"],
                "case_version": c["version"],
                "sample_role": "normal",
                "expected_match": False,
                "label_basis": "缺少检验不能声称正常",
                "decision_time": now,
                "label_time": now,
            }
        ],
    }
    settings.mode = "production"
    try:
        error = post(clients["engineer"], "/learning/datasets", body, expected=422)
        assert error["detail"]["code"] == "dataset_normal_evidence"
        body["items"][0]["sample_role"] = "abnormal"
        error = post(clients["engineer"], "/learning/datasets", body, expected=422)
        assert error["detail"]["code"] == "dataset_abnormal_evidence"
        body["items"][0]["sample_role"] = "failed"
        error = post(clients["engineer"], "/learning/datasets", body, expected=422)
        assert error["detail"]["code"] == "dataset_failure_evidence"
    finally:
        settings.mode = "integration"


def test_handoff_cannot_forge_sample_or_field_acceptance_via_draft_save(core):
    _, _, clients, lines = core
    engineer = clients["engineer"]
    original = file(engineer)
    stages = [
        {
            "segment": segment,
            "owner": "交付责任岗",
            "receiver": "接收责任岗",
            "location": "隔离开发位置",
            "format": "合成JSON合同",
            "authorization": "仅测试只读",
            "error_handling": "保留失败并交人工核对",
            "state": "unconfirmed",
        }
        for segment in ["device_to_server", "server_to_platform", "platform_processing"]
    ]
    payload = {
        "title": "三段交接合成合同",
        "scope": {"line_id": lines[0]["id"], "process": lines[0]["data"]["process"]},
        "stages": stages,
    }
    row = post(engineer, "/learning/handoffs", payload)
    stages[0] = {**stages[0], "state": "sample_validated"}
    post(
        engineer, f"/learning/{row['id']}/save", {"version": row["version"], "payload": payload}, expected=422
    )
    stages[0] = {**stages[0], "state": "field_accepted", "acceptance_evidence_ids": [original]}
    error = post(
        engineer, f"/learning/{row['id']}/save", {"version": row["version"], "payload": payload}, expected=422
    )
    assert error["detail"]["code"] == "handoff_field_evidence"
    stages[0] = {**stages[0], "state": "sample_validated", "sample_ids": [original]}
    saved = post(engineer, f"/learning/{row['id']}/save", {"version": row["version"], "payload": payload})
    assert saved["payload"]["stages"][0]["state"] == "sample_validated"


def test_same_image_bytes_new_file_id_cannot_cross_holdout_or_other_line(core):
    import json
    from uuid import uuid4
    from quality import models as m
    from quality.security import current_time
    from .test_visual import PNG

    settings, _, clients, lines = core
    engineer, admin = clients["engineer"], clients["admin"]

    def image():
        response = engineer.post(
            "/api/files",
            files={"file": ("synthetic.png", PNG, "image/png")},
            headers={"Idempotency-Key": str(uuid4())},
        )
        assert response.status_code == 200
        return response.json()["id"]

    first = image()
    c = post(
        engineer,
        "/cases",
        {
            "title": "锁定图像来源组",
            "line_id": lines[0]["id"],
            "process": lines[0]["data"]["process"],
            "defect": "颗粒",
            "evidence_ids": [first],
        },
    )
    now = current_time().isoformat()
    post(
        admin,
        "/learning/datasets",
        {
            "title": "同图像锁定组",
            "scope": {"line_id": lines[0]["id"], "process": lines[0]["data"]["process"]},
            "split": "locked_test",
            "usage_rights": "合成图像只作隔离验证",
            "target_definition": "同内容不能因新文件ID变独立",
            "items": [
                {
                    "case_id": c["id"],
                    "case_version": c["version"],
                    "sample_role": "abnormal",
                    "expected_match": True,
                    "label_basis": "软件合成图像隔离",
                    "decision_time": now,
                    "label_time": now,
                }
            ],
        },
    )
    with Database(settings).session() as db:
        user = db.scalar(select(m.User).where(m.User.username == "engineer"))
        user.allowed_lines = [lines[1]["id"]]
        user.auth_revision += 1
    credentials = json.loads((settings.data_dir / "demo-credentials.json").read_text())
    post(engineer, "/auth/login", {"username": "engineer", "password": credentials["engineer"]})
    assert engineer.get("/api/cases/" + c["id"]).status_code == 404
    second = image()
    assert second != first
    clone = post(
        engineer,
        "/cases",
        {
            "title": "另一产线重传同图像",
            "line_id": lines[1]["id"],
            "process": lines[1]["data"]["process"],
            "defect": "颗粒",
            "evidence_ids": [second],
        },
    )
    draft = post(
        engineer,
        "/learning/experiences",
        {
            "title": "不得回流的同图像经验",
            "case_id": clone["id"],
            "case_version": clone["version"],
            "opinions": [opinion()],
        },
    )
    rejected = post(
        engineer,
        f"/learning/{draft['id']}/review",
        {"version": draft["version"], "action": "submit", "reason": "核对同源图像而非文件ID"},
        expected=409,
    )
    assert rejected["detail"]["code"] == "heldout_provenance"
    legacy = post(
        engineer,
        "/knowledge",
        {
            "title": "不得旁路发布相同图像",
            "category": "case",
            "content": "合成资料不回流锁定组",
            "process": clone["process"],
            "scope": {"line_ids": [lines[1]["id"]]},
            "source": {"file_id": second},
        },
    )
    post(engineer, f"/knowledge/{legacy['id']}/review", {"action": "submit", "reason": "进入独立审核校验"})
    error = post(
        clients["approver"],
        f"/knowledge/{legacy['id']}/review",
        {"action": "publish", "reason": "核对来源与集合隔离"},
        expected=409,
    )
    assert error["detail"]["code"] == "heldout_knowledge"


def test_failed_intermediate_revision_never_leaves_two_effective_ancestors(core):
    settings, _, clients, lines = core
    engineer, approver = clients["engineer"], clients["approver"]
    c = case(engineer, lines[0])
    first = post(
        engineer,
        "/learning/experiences",
        {
            "title": "失败分支版本链",
            "case_id": c["id"],
            "case_version": c["version"],
            "opinions": [opinion()],
        },
    )
    first = review(engineer, first, "submit")
    first = review(approver, first, "publish")
    drain(settings)
    first = engineer.get("/api/learning/" + first["id"]).json()
    failed = post(
        engineer,
        f"/learning/{first['id']}/revision",
        {"version": first["version"], "reason": "保留失败中间版本"},
    )
    failed = review(engineer, failed, "submit")
    failed = review(approver, failed, "publish")
    job = next(
        t
        for t in approver.get("/api/records/learning-tasks").json()["items"]
        if t["record_id"] == failed["id"]
    )
    post(
        approver,
        f"/learning-tasks/{job['id']}/action",
        {"action": "cancel", "reason": "合成取消故障，保留原有效版"},
    )
    failed = engineer.get("/api/learning/" + failed["id"]).json()
    assert failed["status"] == "apply_failed"
    next_version = post(
        engineer,
        f"/learning/{failed['id']}/revision",
        {"version": failed["version"], "reason": "修订失败草稿并恢复受控发布"},
    )
    next_version = review(engineer, next_version, "submit")
    next_version = review(approver, next_version, "publish")
    drain(settings)
    current = engineer.get("/api/learning/" + next_version["id"]).json()
    prior = engineer.get("/api/learning/" + first["id"]).json()
    assert current["status"] == "effective" and current["revision"] == 3
    assert prior["status"] == "retired" and prior["knowledge"]["status"] == "retired"
    assert prior["activation"]["keyword_status"] == "inactive"
    current = review(approver, current, "rollback")
    restored = engineer.get("/api/learning/" + first["id"]).json()
    assert current["status"] == "rolled_back" and restored["status"] == "effective"
    assert (
        restored["knowledge"]["status"] == "published"
        and restored["activation"]["keyword_status"] == "active"
    )


def test_condition_investigation_step_requires_bound_route_and_matches_case(core):
    from .test_delivery_integrity import published_route

    settings, _, clients, lines = core
    line = lines[0]
    route = published_route(clients, line)
    step1, step2 = route["steps"][0]["id"], route["steps"][1]["id"]
    body = {
        "stable_id": "focused_step_note",
        "name": "单工序调查人工条目",
        "scope": {
            "line_id": line["id"],
            "process": line["data"]["process"],
            "route_ids": [route["id"]],
            "step_ids": ["unknown-step"],
        },
        "dimension": "management",
        "data_type": "text",
        "value_type": "manual",
        "quality_required": "manual_verified",
        "event_semantics": "人工核对该调查工序记录",
        "meaning": "只限制明确调查工序；采集位置在来源台账登记",
    }
    post(clients["engineer"], "/learning/conditions", body, expected=422)
    body["scope"]["step_ids"] = [step1]
    definition = post(clients["engineer"], "/learning/conditions", body)
    definition = review(clients["engineer"], definition, "submit")
    review(clients["approver"], definition, "publish")
    drain(settings)
    data = {
        "title": "明确调查工序合成案例",
        "line_id": line["id"],
        "process": line["data"]["process"],
        "route_id": route["id"],
        "defect": "颗粒",
    }
    post(clients["engineer"], "/cases", {**data, "step_id": "unknown-step"}, expected=422)
    first = post(clients["engineer"], "/cases", {**data, "step_id": step1})
    second = post(clients["engineer"], "/cases", {**data, "step_id": step2})
    assert [r["id"] for r in first["context"]["condition_dictionary"]] == [definition["id"]]
    assert second["context"]["condition_dictionary"] == []
