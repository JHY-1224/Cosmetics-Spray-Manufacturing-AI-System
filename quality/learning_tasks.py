"""Durable learning jobs run in bounded child processes, outside ingestion's loop.

A lease token and fresh authorization are checked before the final transaction.
No model training, arbitrary command, URL, SQL or parser plugin is exposed.
"""

import csv
import io
import json
import os
import subprocess
import sys
import time
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from sqlalchemy import func, or_, text
from . import models as m
from .db import Database
from .security import Actor, aware, current_time, digest, fail
from .service import Service
from .learning import get_record, ready_for_publication, touch, ancestors, root_id, deactivate_record


def task_actor(db, settings, task):
    user = db.get(m.User, task.actor_id)
    if (
        not user
        or user.tenant_id != settings.tenant_id
        or not user.active
        or not user.central_active
        or (user.lease_expires_at and aware(user.lease_expires_at) <= current_time())
    ):
        fail(403, "task_authorization", "贡献/审核账号已失效，任务不得继续应用")
    actor = Actor(user.id, user.role, user.allowed_sites, user.username, user.allowed_lines)
    svc = Service(db, settings, actor, "learning-task:" + task.id)
    if svc.command_authorization() != task.authorization_hash or user.auth_revision != task.data.get(
        "auth_revision", user.auth_revision
    ):
        fail(403, "task_authorization", "授权已改变，请由当前有权人员重新核对后重试")
    actor.require(
        "knowledge_review" if task.kind == "activate" else "knowledge", task.site_id, task.scope_line_id
    )
    svc.get(m.LearningRecord, task.record_id)
    return svc


def extract_material(filename, raw):
    """Machine extraction remains a candidate even if text extraction succeeded."""
    from .storage import parse_document

    suffix = Path(filename).suffix.lower()
    if suffix in {".png", ".jpg", ".jpeg", ".webp"}:
        return {
            "status": "needs_check",
            "text": "",
            "media_kind": "image",
            "byte_count": len(raw),
            "limitation": "原图可回查；未启用OCR/视觉判定，专家可人工核对录入候选说明",
            "locations": [],
            "parser_version": "evidence-extract-v1",
        }
    if suffix in {".wav", ".mp3", ".mp4"}:
        return {
            "status": "needs_check",
            "text": "",
            "media_kind": "audio" if suffix != ".mp4" else "video",
            "limitation": "只保存原件/人工转写与时间片；自动ASR/视频理解未启用",
            "locations": [],
            "parser_version": "evidence-extract-v1",
        }
    if suffix in {".csv", ".json"}:
        content = raw.decode("utf-8-sig")
        rows = list(csv.DictReader(io.StringIO(content))) if suffix == ".csv" else json.loads(content)
        if not isinstance(rows, list):
            rows = [rows]
        subset = rows[:200]
        return {
            "status": "partial" if len(rows) > 200 else "needs_check",
            "text": json.dumps(subset, ensure_ascii=False)[:200000],
            "row_count": len(rows),
            "preview_rows": len(subset),
            "locations": [{"row": i + 1, "candidate": r} for i, r in enumerate(subset)],
            "limitation": "表格提取仅供字段核对，不自动写入测量或知识事实",
            "parser_version": "evidence-extract-v1",
        }
    result = parse_document(filename, raw)
    result["text"] = result.get("text", "")[:200000]
    result["locations"] = result.get("locations", [])[:300]
    return {
        **result,
        "parser_state": result.get("status"),
        "status": "needs_check",
        "parser_version": "evidence-extract-v1",
    }


def replay_rule(svc, row, task):
    from .learning import get_record
    from .diagnostics import rank_candidates, timestamp
    from .scopes import applicable
    from .learning import applicability

    dataset = get_record(svc, task.data["dataset_id"], "dataset")
    if (
        dataset.status != "effective"
        or dataset.payload["split"] not in {"functional", "development"}
        or dataset.payload["task"] != "post_inspection_diagnosis"
    ):
        fail(409, "heldout_replay", "保留集不能用于规则开发回放")
    if digest(dataset.payload) != task.data["dataset_hash"] or digest(row.payload) != task.data["rule_hash"]:
        fail(409, "replay_stale", "候选/数据版本已变化")
    previous = get_record(svc, row.previous_id, "rule") if row.previous_id else None

    def candidate(payload, id):
        return SimpleNamespace(id=id, category="root_cause", title=payload["title"], rules=payload["rule"])

    results = []
    for sample in dataset.snapshot["items"]:
        measurements = [
            SimpleNamespace(**{**r, "occurred_at": timestamp(r["occurred_at"])})
            for r in sample["feature_measurements"]
        ]
        item = sample["item"]
        process = sample["case"]["process"]
        defect = sample["defect_input"]

        def fits(payload):
            return applicable(
                applicability(payload["scope"], row.site_id),
                svc.settings,
                process,
                sample["case"]["line_id"],
                sample["case"].get("context", {}),
            )

        after = rank_candidates(
            [candidate(row.payload, row.id)] if fits(row.payload) else [],
            defect,
            measurements,
            process,
            item["decision_time"],
        )
        before = (
            rank_candidates(
                [candidate(previous.payload, previous.id)],
                defect,
                measurements,
                process,
                item["decision_time"],
            )
            if previous and fits(previous.payload)
            else []
        )
        predicted = bool(after and after[0]["status"] == "evidence_supported")
        baseline = bool(before and before[0]["status"] == "evidence_supported")
        expected = item["expected_match"]
        results.append(
            {
                "case_id": item["case_id"],
                "case_version": item["case_version"],
                "sample_role": item["sample_role"],
                "expected_match": expected,
                "before_match": baseline,
                "after_match": predicted,
                "after": after,
                "feature_hash": digest(sample["feature_measurements"]),
                "decision_time": item["decision_time"],
                "label_time": item["label_time"],
            }
        )

    def counts(key):
        known = [r for r in results if r["expected_match"] is not None]
        return {
            "labelled": len(known),
            "true_positive": sum(r[key] and r["expected_match"] for r in known),
            "false_positive": sum(r[key] and not r["expected_match"] for r in known),
            "false_negative": sum(not r[key] and r["expected_match"] for r in known),
            "true_negative": sum(not r[key] and not r["expected_match"] for r in known),
        }

    before, after = counts("before_match"), counts("after_match")
    roles = {r["sample_role"] for r in results if r["expected_match"] is not None}
    gate = (
        roles == {"normal", "abnormal", "failed"}
        and after["true_positive"] >= 1
        and after["false_positive"] == 0
        and after["false_negative"] <= before["false_negative"]
    )
    return {
        "rule_id": row.id,
        "rule_hash": digest(row.payload),
        "dataset_id": dataset.id,
        "dataset_hash": digest(dataset.payload),
        "dataset_split": dataset.payload["split"],
        "sample_count": len(results),
        "before": before,
        "after": after,
        "results": results,
        "functional_gate_passed": gate,
        "independent_effect_evaluation": "not_performed",
        "model_training": "not_enabled",
        "limitations": "同一开发/功能数据可反复调规则；计数仅是已核对开发样本匹配，不代表新生产诊断准确率、因果或跨厂泛化。",
    }


def activate(svc, row):
    from .learning import applicability

    ready_for_publication(svc, row)
    chain = ancestors(svc, row)
    allowed = {r.id for r in chain}
    root = root_id(svc, row)
    for active in svc.db.scalars(
        svc.scoped(m.LearningRecord).where(
            m.LearningRecord.kind == row.kind,
            m.LearningRecord.scope_line_id == row.scope_line_id,
            m.LearningRecord.status == "effective",
            or_(
                m.LearningRecord.snapshot["root_id"].as_string() == root,
                m.LearningRecord.snapshot["root_id"].as_string().is_(None),
            ),
        )
    ):
        if root_id(svc, active) == root and active.id not in allowed:
            fail(409, "activation_branch", "另一个版本分支已生效，请从当前版本重新修订")
    previous = next((r for r in chain if r.status == "effective"), None)
    last_applied = next((r for r in chain if r.activation.get("applied_at")), None)
    if row.kind in {"experience", "rule"}:
        if row.kind == "experience":
            case = svc.get(m.Case, row.case_id)
            scope = {
                "process": case.process,
                "factory_ids": [case.site_id],
                "line_ids": [case.line_id],
                "product_ids": [case.context["product_id"]] if case.context.get("product_id") else [],
                "substrate_ids": [case.context["substrate_id"]] if case.context.get("substrate_id") else [],
                "product_families": [case.context["product_family"]]
                if case.context.get("product_family")
                else [],
                "shapes": [case.context["shape"]] if case.context.get("shape") else [],
            }
            text_value = "\n".join(
                [
                    row.title,
                    case.context["defect"],
                    *[
                        "候选意见："
                        + o["hypothesis"]
                        + "；适用："
                        + o["applicability"]
                        + "；核对事实："
                        + o["checked_facts"]
                        + "；未知："
                        + o["unknowns"]
                        for o in row.payload["opinions"]
                    ],
                    "处置/失败/对照：" + row.payload["outcome_notes"],
                    "同时变化因素：" + "；".join(row.payload["other_factors"]),
                    row.payload.get("feedback", ""),
                ]
            )
            rules = {}
            category = "case"
        else:
            scope = applicability(row.payload["scope"], row.site_id)
            rules = row.payload["rule"]
            category = "root_cause"
            text_value = (
                row.payload["rationale"]
                + "\n候选："
                + str(rules["cause"])
                + "\n"
                + "；".join(rules["defects"])
            )
        knowledge = svc.add(
            m.Knowledge,
            category=category,
            title=row.title,
            content=text_value,
            status="published",
            author_id=row.author_id,
            reviewer_id=row.reviewer_id,
            scope=scope,
            rules=rules,
            previous_id=last_applied.knowledge_id if last_applied else None,
            revision=row.revision,
            source={
                "learning_record_id": row.id,
                "origin_node": svc.settings.node_id,
                "case_id": row.case_id,
                "snapshot_hash": digest(row.snapshot),
                "provenance": "demo" if svc.settings.mode == "demo" else "manual",
                "lineage_groups": row.snapshot.get("groups", []),
                "file_ids": sorted({r["file_id"] for r in row.snapshot.get("materials", [])}),
                "allow_training": False,
            },
            valid_until=current_time() + timedelta(days=365),
        )
        row.knowledge_id = knowledge.id
        svc.db.flush()
        # Keyword query is the effective retrieval channel; no pretend vector-index success.
        from .retrieval import search

        if row.kind == "experience":
            probe_context = case.context
        else:
            probe_context = {
                target: (scope.get(source) or [""])[0]
                for source, target in [
                    ("product_ids", "product_id"),
                    ("product_families", "product_family"),
                    ("substrate_ids", "substrate_id"),
                    ("shapes", "shape"),
                    ("step_ids", "step_id"),
                    ("route_ids", "route_id"),
                ]
            }
        available = search(
            svc,
            row.title,
            scope["process"],
            probe_context.get("product_id", ""),
            line_id=row.scope_line_id,
            context=probe_context,
            entry_id=knowledge.id,
        )
        if not available:
            fail(409, "knowledge_apply_failed", "新版本未能在授权查询中读取")
    if previous and previous.status == "effective":
        previous.status = "retired"
        deactivate_record(previous)
        touch(svc, previous, "superseded")
        if previous.knowledge_id:
            svc.get(m.Knowledge, previous.knowledge_id, True).status = "retired"
    row.status = "effective"
    row.activation = {
        **row.activation,
        "applied_at": current_time().isoformat(),
        "keyword_status": "active" if row.knowledge_id else "not_applicable",
        "dictionary_status": "active" if row.kind == "condition" else "not_applicable",
        "vector_status": "not_requested",
        "node_id": svc.settings.node_id,
        "node_distribution": "separate_receipts",
    }
    touch(svc, row, "effective")
    return {
        "record_id": row.id,
        "status": row.status,
        "knowledge_id": row.knowledge_id,
        "activation": row.activation,
    }


def run_claimed(settings, task_id, lease_token):
    database = Database(settings)
    extraction = None
    # Read and parse outside the commit transaction: the API remains able to cancel/ingest.
    with database.session() as db:
        task = db.get(m.LearningTask, task_id)
        if not task or task.status != "running" or task.lease_token != lease_token:
            return
        svc = task_actor(db, settings, task)
        row = get_record(svc, task.record_id)
        if task.kind == "extract":
            from .storage import Storage

            evidence = svc.get(m.Evidence, row.file_id)
            filename = evidence.filename
            raw = Storage(settings).get(evidence.object_key, evidence.sha256)
    if task.kind == "extract":
        extraction = extract_material(filename, raw)
    with database.session() as db:
        task = db.get(m.LearningTask, task_id, with_for_update=True)
        if not task or task.status != "running" or task.lease_token != lease_token:
            return
        svc = task_actor(db, settings, task)
        row = get_record(svc, task.record_id, lock=True)
        if row.version != task.data["record_version"]:
            fail(409, "task_record_version", "任务绑定的专家版本已经改变")
        if task.kind == "extract":
            row.snapshot = {
                "machine": extraction,
                "source_file": {"id": evidence.id, "sha256": evidence.sha256, "filename": filename},
                "original_retained": True,
            }
            row.status = extraction["status"]
            touch(svc, row, "material_extracted")
            result = {"record_id": row.id, "status": row.status}
        elif task.kind == "activate":
            if row.status != "pending_apply":
                fail(409, "activation_state", "只有已审核待应用版本可生效")
            result = activate(svc, row)
        elif task.kind == "replay":
            result = replay_rule(svc, row, task)
            row.activation = {**row.activation, "replay": result}
            touch(svc, row, "rule_replayed")
            from .learning import new_record

            report = new_record(
                svc,
                "replay",
                row.title + " 开发回放",
                row.scope_line_id,
                result,
                snapshot={"task_id": task.id},
                status="recorded",
            )
            result = {**result, "report_id": report.id}
        else:
            fail(422, "learning_task_kind", "未知任务，禁止执行任意命令")
        task.status = "succeeded"
        task.result = result
        task.lease_token = ""
        task.lease_until = None
        svc.audit("learning.task_succeeded", task.id, {"kind": task.kind, "record_id": row.id})
    database.engine.dispose()


def claim(settings, database):
    with database.session() as db:
        if db.bind.dialect.name == "sqlite":
            db.execute(text("BEGIN IMMEDIATE"))
        else:
            db.execute(
                text("SELECT pg_advisory_xact_lock(:key)"),
                {"key": int(digest(settings.tenant_id + ":" + settings.node_id)[:15], 16)},
            )
        svc = Service(db, settings, Actor("learning-dispatcher", "admin", settings.sites))
        now = current_time()
        running = (
            db.scalar(
                svc.scoped(m.LearningTask)
                .where(m.LearningTask.status == "running", m.LearningTask.lease_until > now)
                .with_only_columns(func.count())
            )
            or 0
        )
        if running >= settings.learning_concurrency:
            return None
        task = db.scalar(
            svc.scoped(m.LearningTask)
            .where(
                or_(
                    (m.LearningTask.status == "queued") & (m.LearningTask.next_at <= now),
                    (m.LearningTask.status == "running") & (m.LearningTask.lease_until <= now),
                )
            )
            .order_by(m.LearningTask.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if task is None:
            return None
        if task.attempts >= 3:
            task.status = "failed"
            task.error_code = "lease_attempts_exhausted"
            task.lease_token = ""
            task.lease_until = None
            row = svc.get(m.LearningRecord, task.record_id, True)
            if row.version == task.data["record_version"] and task.kind in {"activate", "extract"}:
                row.status = "apply_failed" if task.kind == "activate" else "parse_failed"
                touch(svc, row, "lease_exhausted")
            svc.audit("learning.task_failed", task.id, {"code": task.error_code})
            return None
        task.attempts += 1
        task.status = "running"
        task.lease_token = m.uid()
        task.lease_until = now + timedelta(seconds=settings.learning_timeout + 15)
        return task.id, task.lease_token


def mark_failed(settings, database, id, lease, error):
    with database.session() as db:
        task = db.get(m.LearningTask, id, with_for_update=True)
        if not task or task.status != "running" or task.lease_token != lease:
            return
        task.status = "failed"
        task.error_code = error
        task.lease_token = ""
        task.lease_until = None
        row = db.get(m.LearningRecord, task.record_id)
        if row and row.version == task.data["record_version"] and task.kind in {"activate", "extract"}:
            row.status = "apply_failed" if task.kind == "activate" else "parse_failed"
            row.version += 1
            row.activation = {
                **row.activation,
                "error_code": error,
                "last_failed_at": current_time().isoformat(),
            }
        svc = Service(db, settings, Actor("learning-worker", "admin", settings.sites))
        svc.audit("learning.task_failed", task.id, {"code": error})


def launch_claimed(
    settings, database, claim_result, stopping=None, *, runner="learning", timeout=None, fail_task=None
):
    id, lease = claim_result
    module = {"learning": "quality.learning_tasks", "digital": "quality.digital_tasks"}[runner]
    timeout = timeout or settings.learning_timeout
    fail_task = fail_task or mark_failed
    env = os.environ.copy()
    env.update(
        {
            "QUALITY_" + k.upper(): json.dumps(v) if isinstance(v, (dict, list, bool)) else str(v)
            for k, v in settings.model_dump(mode="json").items()
        }
    )
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    # Results and classified errors are durable in the task ledger; parser stdout is discarded.
    with open(os.devnull, "wb") as output:
        child = subprocess.Popen(
            [sys.executable, "-m", module, id, lease], env=env, stdout=output, stderr=output
        )
        started = time.monotonic()
        while child.poll() is None:
            if stopping is not None and stopping.is_set():
                child.terminate()
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=5)
                fail_task(settings, database, id, lease, "worker_stopped")
                return
            if time.monotonic() - started > timeout:
                child.kill()
                child.wait(timeout=5)
                fail_task(settings, database, id, lease, "task_timeout")
                return
            time.sleep(0.05)
        if child.returncode:
            fail_task(settings, database, id, lease, "task_failed")


def run_once(settings, database):
    claimed = claim(settings, database)
    if claimed:
        launch_claimed(settings, database, claimed)
    return bool(claimed)


def serve(settings, stopping):
    from concurrent.futures import ThreadPoolExecutor

    database = Database(settings)
    futures = []
    with ThreadPoolExecutor(
        max_workers=settings.learning_concurrency, thread_name_prefix="learning-child"
    ) as pool:
        while not stopping.is_set():
            futures = [f for f in futures if not f.done()]
            if len(futures) < settings.learning_concurrency:
                try:
                    claimed = claim(settings, database)
                    if claimed:
                        futures.append(pool.submit(launch_claimed, settings, database, claimed, stopping))
                except Exception:
                    # Transient database faults must not terminate the independent dispatcher.
                    stopping.wait(1)
            stopping.wait(0.25)
    database.engine.dispose()


if __name__ == "__main__":
    from .config import Settings

    settings = Settings()
    try:
        run_claimed(settings, sys.argv[1], sys.argv[2])
    except Exception as exc:
        # Classified errors only; no raw material, credential or model payload in logs.
        from fastapi import HTTPException

        code = exc.detail.get("code", "task_failed") if isinstance(exc, HTTPException) else type(exc).__name__
        mark_failed(settings, Database(settings), sys.argv[1], sys.argv[2], code)
        print("learning_job_failed " + code)
        raise SystemExit(1)
