"""Deterministic evidence evaluation; no causal probabilities or invented process limits."""

import math
from collections import defaultdict
from datetime import datetime
from statistics import mean
from typing import Literal

from pydantic import Field, model_validator

from .schemas import Strict
from .security import aware


def timestamp(value):
    return aware(datetime.fromisoformat(value) if isinstance(value, str) else value)


class MetricCondition(Strict):
    metric: str = Field(min_length=1, max_length=100)
    operator: Literal["gt", "gte", "lt", "lte", "eq", "between", "outside"]
    value: float | bool | str
    upper: float | None = None
    unit: str = Field(default="", max_length=40)
    value_type: Literal["measured", "setpoint", "manual", "derived"] = "measured"
    equipment_id: str = Field(default="", max_length=120)
    aggregation: Literal["last", "mean", "min", "max"] = "last"
    max_age_seconds: int = Field(default=3600, ge=1, le=604800)
    min_samples: int = Field(default=1, ge=1, le=100000)

    @model_validator(mode="after")
    def bounds(self):
        numeric = isinstance(self.value,(float,int)) and not isinstance(self.value,bool)
        if numeric and (not math.isfinite(self.value) or not self.unit):
            raise ValueError("数值条件须为有限数且明确单位")
        if not numeric and (self.operator != "eq" or self.aggregation != "last" or self.upper is not None):
            raise ValueError("枚举、布尔、文本条件仅允许逐值eq，不做数值聚合")
        if (self.upper is not None and not math.isfinite(self.upper)):
            raise ValueError("条件数值必须有限")
        if self.operator in {"between", "outside"} and (self.upper is None or self.upper < self.value):
            raise ValueError("范围条件需明确上下限")
        return self


def evaluate_condition(raw, measurements, anchor=None):
    condition = MetricCondition.model_validate(raw)
    rows = [
        v
        for v in measurements
        if v.metric == condition.metric
        and (not condition.equipment_id or v.equipment_id == condition.equipment_id)
    ]
    result = {"metric": condition.metric, "condition": condition.model_dump(), "refs": []}
    if not rows:
        return {**result, "state": "missing"}
    typed = [v for v in rows if getattr(v, "value_type", "measured") == condition.value_type]
    if not typed:
        return {**result, "state": "value_type_mismatch"}
    units = [v for v in typed if v.unit == condition.unit]
    if not units:
        return {**result, "state": "unit_mismatch"}
    def usable(row):
        def numeric(v):
            return isinstance(v, (float, int)) and not isinstance(v, bool)
        compatible = numeric(row.value) and numeric(condition.value) or type(row.value) is type(condition.value)
        return row.quality == "Good" and compatible and (not numeric(row.value) or math.isfinite(row.value))

    valid = [v for v in units if usable(v)]
    if not valid:
        return {**result, "state": "invalid_quality"}
    if anchor:
        valid = [
            v
            for v in valid
            if 0
            <= (timestamp(anchor) - timestamp(v.occurred_at)).total_seconds()
            <= condition.max_age_seconds
        ]
        if not valid:
            return {**result, "state": "stale"}
    if len({v.equipment_id for v in valid}) > 1 and not condition.equipment_id:
        return {**result, "state": "ambiguous_equipment"}
    valid.sort(key=lambda v: (timestamp(v.occurred_at), v.id))
    if len(valid) < condition.min_samples:
        return {**result, "state": "insufficient_samples", "sample_count": len(valid)}
    values = [v.value for v in valid]
    value = {
        "last": lambda: values[-1],
        "mean": lambda: mean(values),
        "min": lambda: min(values),
        "max": lambda: max(values),
    }[condition.aggregation]()
    matches = {
        "gt": lambda: value > condition.value,
        "gte": lambda: value >= condition.value,
        "lt": lambda: value < condition.value,
        "lte": lambda: value <= condition.value,
        "eq": lambda: value == condition.value,
        "between": lambda: condition.value <= value <= condition.upper,
        "outside": lambda: value < condition.value or value > condition.upper,
    }[condition.operator]()
    references = [valid[-1].id] if condition.aggregation == "last" else [v.id for v in valid]
    return {
        **result,
        "state": "matched" if matches else "not_matched",
        "refs": references,
        "value": value,
        "unit": condition.unit,
        "sample_count": len(valid),
    }


def rank_candidates(knowledge, defect, measurements, process, anchor=None):
    candidates = []
    for k in knowledge:
        rules = k.rules
        if k.category != "root_cause" or defect not in rules.get("defects", []):
            continue
        required, support, counter, missing = rules.get("required_metrics", []), [], [], []
        evaluations = []
        for metric in required:
            rows = [
                v
                for v in measurements
                if v.metric == metric
                and v.quality == "Good"
                and getattr(v, "value_type", "measured") == "measured"
            ]
            if not rows:
                missing.append(metric)
            elif len({v.equipment_id for v in rows}) > 1:
                missing.append(metric + ":ambiguous_equipment")
            else:
                support.extend(v.id for v in rows)
        matched = 0
        conditions = rules.get("conditions", [])
        explicit_counter = False
        for category, checks in [("support", conditions), ("counter", rules.get("counter_conditions", []))]:
            for raw in checks:
                result = evaluate_condition(raw, measurements, anchor)
                evaluations.append({"role": category, **result})
                if result["state"] == "matched":
                    if category == "counter":
                        counter.extend(result["refs"])
                        explicit_counter = True
                    else:
                        support.extend(result["refs"])
                        matched += 1
                elif result["state"] == "not_matched":
                    if category == "support":
                        counter.extend(result["refs"])
                else:
                    missing.append(result["metric"] + ":" + result["state"])
        if explicit_counter:
            state = "excluded"
        elif conditions and matched == len(conditions) and not missing:
            state = "evidence_supported"
        elif conditions and rules.get("exclude_when_false", False) and not matched and not missing:
            state = "excluded"
        else:
            state = "unverified"
        base = rules.get("relative_score", 0)
        score = (
            0 if state == "excluded" else round(base * (matched / len(conditions) if conditions else 1), 2)
        )
        candidates.append(
            {
                "dimension": rules["dimension"],
                "cause": rules.get("cause", k.title),
                "support_refs": list(dict.fromkeys([k.id, *support])),
                "counter_refs": list(dict.fromkeys(counter)),
                "missing_evidence": list(dict.fromkeys(missing)),
                "ranking_basis": "专家审核权重×已满足条件比例；显式反证命中则排除；仅有指标不确认原因。相对评分不是因果概率",
                "applicability": process,
                "next_checks": rules.get("next_checks") or ["由工艺专家核实诱因并补充反证"],
                "status": state,
                "relative_score": score,
                "expert_weight": base,
                "condition_evaluations": evaluations,
                "conditions_matched": matched,
                "conditions_total": len(conditions),
            }
        )
    return sorted(candidates, key=lambda c: (-c["relative_score"], c["dimension"], c["cause"]))


def evidence_features(inspections, measurements, passages, knowledge, window_seconds=900):
    """Group actual observations by unit and identity. Concentration is not causal evidence."""
    from .statistics import normalized_observations
    rows = [r for r in normalized_observations(inspections) if not r.get('statistical_conflict')]
    rows.sort(key=lambda r: (timestamp(r["occurred_at"]), r["id"]))
    clusters = []
    for row in [r for r in rows if r["status"] == "NG"]:
        if (
            not clusters
            or (timestamp(row["occurred_at"]) - timestamp(clusters[-1]["end"])).total_seconds()
            > window_seconds
        ):
            clusters.append(
                {
                    "start": row["occurred_at"],
                    "end": row["occurred_at"],
                    "inspection_refs": [],
                    "objects": [],
                    "granularities": [],
                }
            )
        c = clusters[-1]
        c["end"] = row["occurred_at"]
        c["inspection_refs"].append(row["id"])
        if row.get("object_id") and row["object_id"] not in c["objects"]:
            c["objects"].append(row["object_id"])
        if row["granularity"] not in c["granularities"]:
            c["granularities"].append(row["granularity"])
    groups = defaultdict(list)
    for row in rows:
        data = row.get("result", {})
        for key in ("station_id", "production_batch_id", "equipment_run_id"):
            if data.get(key):
                groups[(key, data[key], row["granularity"])].append(row)
        for material in data.get("material_batch_ids", []):
            groups[("material_batch_id", material, row["granularity"])].append(row)
    concentration = []
    for (kind, value, unit), items in sorted(groups.items()):
        inspected = sum(r["result"].get("inspected_count", 0) for r in items)
        defective = sum(r["result"].get("defective_count", 0) for r in items)
        concentration.append(
            {
                "group_by": kind,
                "identity": value,
                "sample_unit": unit,
                "inspected_count": inspected,
                "defective_count": defective,
                "defect_rate": defective / inspected if inspected else None,
                "inspection_refs": [r["id"] for r in items],
            }
        )
    metrics = defaultdict(list)
    for v in measurements:
        metrics[(v.equipment_id, v.metric, v.unit, v.value_type)].append(v)
    parameters = []
    for (equipment, metric, unit, value_type), values in sorted(metrics.items()):
        good = sorted(
            [v for v in values if v.quality == "Good" and math.isfinite(v.value)],
            key=lambda v: (timestamp(v.occurred_at), v.id),
        )
        windows = [
            (k.id, k.rules.get("parameters", {}).get(metric)) for k in knowledge if k.category == "sop"
        ]
        windows = [(ref, w) for ref, w in windows if w]
        applicable = [(ref, w) for ref, w in windows if w["unit"] == unit]
        conflict = bool(
            windows
            and (
                len(applicable) != len(windows)
                or max(w["min"] for _, w in applicable) > min(w["max"] for _, w in applicable)
            )
        )
        deviations = []
        if not conflict and applicable and value_type == "measured":
            low, high = max(w["min"] for _, w in applicable), min(w["max"] for _, w in applicable)
            deviations = [v.id for v in good if not low <= v.value <= high]
        parameters.append(
            {
                "equipment_id": equipment,
                "metric": metric,
                "unit": unit,
                "value_type": value_type,
                "good_samples": len(good),
                "invalid_samples": len(values) - len(good),
                "minimum": min(v.value for v in good) if good else None,
                "maximum": max(v.value for v in good) if good else None,
                "last": good[-1].value if good else None,
                "sop_refs": [ref for ref, _ in windows],
                "window_conflict": conflict,
                "deviation_refs": deviations,
                "status": "manual_review"
                if conflict
                else "deviation"
                if deviations
                else "within_window"
                if applicable and good and value_type == "measured"
                else "no_approved_comparison",
            }
        )
    return {
        "strategy": "deterministic_evidence_v2",
        "time_gap_seconds": window_seconds,
        "time_clusters": clusters,
        "group_concentration": concentration,
        "parameters": parameters,
        "passage_refs": [p.id for p in passages],
        "inspection_count": len(rows),
        "limitations": "仅分析授权历史观察；按实际粒度分组，不把时间/批次集中当作根因确认，不用设定值替代实测值。",
    }
