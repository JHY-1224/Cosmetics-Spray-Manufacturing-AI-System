"""Validated rolling quality trigger using homogeneous, corrected observations."""

from datetime import datetime, timedelta
from pydantic import Field
from . import models as m
from .schemas import Strict
from .security import aware, digest
from .service import serialize


class AnomalyPolicy(Strict):
    enabled: bool = True
    min_rate: float = Field(default=1, ge=0, le=1)
    min_samples: int = Field(default=1, ge=1, le=10000000)
    min_ng_records: int = Field(default=1, ge=1, le=100000)
    window_seconds: int = Field(default=0, ge=0, le=86400)
    dedup_seconds: int = Field(default=3600, ge=1, le=86400)
    max_lateness_seconds: int = Field(default=86400, ge=1, le=604800)
    same_batch: bool = True
    defects: list[str] = Field(default_factory=list, max_length=100)


def trigger(svc, line, row):
    raw = line.data.get("anomaly_threshold")
    if not raw:
        return None
    policy = AnomalyPolicy.model_validate(raw)
    if not policy.enabled or row.status != "NG":
        return None
    from .security import current_time

    if (current_time() - aware(row.occurred_at)).total_seconds() > policy.max_lateness_seconds:
        return None
    categories = sorted({d["category"] for d in row.result.get("defects", [])})
    targets = [d for d in categories if not policy.defects or d in policy.defects]
    if not targets and policy.defects:
        return None
    if policy.window_seconds:
        rows = svc.db.scalars(
            svc.scoped(m.Inspection)
            .where(
                m.Inspection.line_id == row.line_id,
                m.Inspection.granularity == row.granularity,
                m.Inspection.occurred_at >= row.occurred_at - timedelta(seconds=policy.window_seconds),
                m.Inspection.occurred_at <= row.occurred_at,
            )
            .order_by(m.Inspection.occurred_at.desc())
            .limit(10001)
        ).all()
        if len(rows) > 10000:
            svc.audit("anomaly.window_requires_review", row.id, {"reason": "window_capacity"})
            return None
    else:
        rows = [row]
    from .statistics import normalized_observations
    observations = normalized_observations([serialize(v) for v in rows])
    eligible = [
        v
        for v in observations
        if not v.get('statistical_conflict')
        and v['provenance'] == row.provenance
        and all(v['result'].get(k) == row.result.get(k) for k in
                ('process', 'product_id', 'model_version', 'config_version', 'inspection_round', 'inspection_kind'))
        and (
            not policy.same_batch
            or v['result'].get("production_batch_id") == row.result.get("production_batch_id")
        )
    ]
    inspected = sum(v['result']["inspected_count"] for v in eligible)
    defective = sum(v['result']["defective_count"] for v in eligible)
    ng = [
        v
        for v in eligible
        if v['status'] == "NG"
        and (not policy.defects or any(d["category"] in policy.defects for d in v['result'].get("defects", [])))
    ]
    if (
        inspected < policy.min_samples
        or len(ng) < policy.min_ng_records
        or defective / inspected < policy.min_rate
    ):
        return None
    bucket = int(aware(row.occurred_at).timestamp()) // policy.dedup_seconds
    key = digest(
        [
            row.line_id,
            row.result.get("production_batch_id") if policy.same_batch else "",
            row.granularity,
            bucket,
            targets,
        ]
    )
    return {
        "key": key,
        "inspection_ids": [v['id'] for v in ng[:300]],
        "policy": policy.model_dump(),
        "inspected_count": inspected,
        "defective_count": defective,
        "ng_records": len(ng),
        "window_start": min(aware(datetime.fromisoformat(v['occurred_at'])) for v in eligible).isoformat(),
        "window_end": aware(row.occurred_at).isoformat(),
        "sample_unit": row.granularity,
        "observation_refs": [id for v in eligible for id in v['observation_refs']],
        "trigger_record": serialize(row),
    }
