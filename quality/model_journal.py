"""Durable request reservation: an unknown call is never automatically issued again."""

import fcntl
import json
import os
from contextlib import contextmanager

from .security import digest


@contextmanager
def analysis_slot(settings, case_id):
    """Serialize a case's checkpoint across API/worker processes without a DB write lock."""
    from .security import fail

    root = settings.data_dir / 'model-journal' / digest([settings.tenant_id, settings.node_id, case_id])
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (root / '.analysis.lock').open('a') as lock:
        os.chmod(lock.name, 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            fail(409, 'analysis_in_progress', '本案件正在诊断，请等待当前结果并刷新，不重复发起模型请求')
        yield


@contextmanager
def model_slot(settings):
    """Nonblocking node-local limit shared by API/worker processes on the same volume."""
    root = settings.data_dir / 'model-journal' / '_slots'
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    held = None
    try:
        for index in range(settings.model_concurrency):
            candidate = (root / str(index)).open('a')
            os.chmod(candidate.name, 0o600)
            try:
                fcntl.flock(candidate, fcntl.LOCK_EX | fcntl.LOCK_NB)
                held = candidate
                break
            except BlockingIOError:
                candidate.close()
        if held is None:
            raise RuntimeError('model_concurrency_busy')
        yield
    finally:
        if held is not None:
            held.close()


@contextmanager
def locked_case(settings, case_id):
    root = settings.data_dir / "model-journal" / digest([settings.tenant_id, settings.node_id, case_id])
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (root / ".lock").open("a") as lock:
        os.chmod(root / ".lock", 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield root


def write(path, body):
    temp = path.with_suffix(".tmp")
    with temp.open("w") as handle:
        os.chmod(temp, 0o600)
        json.dump(body, handle, ensure_ascii=False)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def reserve(settings, case_id, run_id, input_hash, tokens, cost, historical):
    with locked_case(settings, case_id) as root:
        path = root / (digest(run_id) + ".json")
        if path.exists():
            saved = json.loads(path.read_text())
            if saved["input_hash"] != input_hash:
                raise ValueError("model_call_input_conflict")
            return saved
        previous = [json.loads(p.read_text()) for p in root.glob("*.json")]
        journal_ids = {r["run_id"] for r in previous}
        legacy = [r for r in historical if r.id not in journal_ids]
        spent_tokens = sum(r["reserved_tokens"] for r in previous) + sum(
            r.data.get("model_record", {}).get("reserved_tokens", 0) for r in legacy
        )
        spent_cost = sum(r["reserved_cost"] for r in previous) + sum(
            r.data.get("model_record", {}).get("reserved_cost", 0) for r in legacy
        )
        if spent_tokens + tokens > settings.max_case_tokens or spent_cost + cost > settings.max_case_cost:
            return {"status": "budget_exhausted"}
        record = {
            "status": "reserved",
            "run_id": run_id,
            "input_hash": input_hash,
            "reserved_tokens": tokens,
            "reserved_cost": cost,
            "new_reservation": True,
        }
        write(path, {k: v for k, v in record.items() if k != "new_reservation"})
        return record


def finish(settings, case_id, run_id, outcome):
    with locked_case(settings, case_id) as root:
        path = root / (digest(run_id) + ".json")
        saved = json.loads(path.read_text())
        write(path, {**saved, "status": "completed", "outcome": outcome})
