"""Deterministic assessment keeps source verdict, measurement validity and release separate."""

from decimal import Decimal, ROUND_HALF_EVEN, ROUND_HALF_UP, localcontext
from fastapi import HTTPException
from . import models as m
from . import enterprise_schemas as s
from .enterprise import effective, evidence, fits, json_value, line_scope
from .security import aware, current_time, digest, fail
from .service import serialize


def source_observation(svc, data):
    with localcontext() as context:
        context.prec = 128
        return _source_observation(svc, data)


def _source_observation(svc, data):
    if data.source_kind == "manual":
        return {
            "kind": "manual",
            "value": data.value,
            "unit": data.unit,
            "occurred_at": data.occurred_at,
            "quality": data.quality,
            "association": data.association,
        }
    cls = {"lab": m.LabResult, "measurement": m.Measurement, "inspection": m.Inspection}[data.source_kind]
    row = svc.get(cls, data.source_id)
    actual_version = digest(serialize(row))
    if data.source_version and data.source_version != actual_version:
        fail(422, "quality_source_version", "指定来源版本与当前不可变原记录不一致")
    if row.scope_line_id != data.line_id:
        fail(403, "quality_source_scope", "源事实必须属于当前产线")
    if aware(row.occurred_at) != data.occurred_at or (
        data.source_kind != "inspection" and row.unit != data.unit
    ):
        fail(422, "quality_source_metadata", "不得改写源事实的单位或发生时点")
    if data.source_kind == "lab":
        if row.object_id != data.object_id or row.data.get("granularity") != data.granularity:
            fail(422, "quality_source_identity", "样品/批次及粒度须与原仪器/实验室记录一致")
        if row.data.get("instrument_id") and row.data["instrument_id"] != data.equipment_id:
            fail(422, "quality_source_equipment", "实际仪器身份与检测或校准引用的设备不一致")
        result = {
            "kind": "lab",
            "value": row.value,
            "unit": row.unit,
            "method": row.method,
            "metric": row.metric,
            "occurred_at": row.occurred_at,
            "quality": data.quality,
            "association": data.association,
            "evidence_ids": row.evidence_ids,
            "instrument_id": row.data.get("instrument_id"),
        }
    elif data.source_kind == "measurement":
        source_identity = row.data.get("identity", row.data)
        if row.equipment_id != data.equipment_id:
            fail(422, "quality_source_equipment", "不能用另一台设备的校准资格覆盖实际测量设备")
        for key in ("product_id", "step_id"):
            if source_identity.get(key) and source_identity[key] != getattr(data, key):
                fail(422, "quality_source_context", "产品或工序声明与原始测量关联不一致")
        identity = source_identity.get("object_id") or source_identity.get("production_batch_id")
        if identity != data.object_id or row.value_type not in {"measured", "manual"}:
            fail(422, "quality_source_identity", "设备设定/派生值不能冒充实际产品检测；须有明确对象或批次")
        from sqlalchemy import cast, Text

        if svc.db.scalar(
            svc.scoped(m.Measurement)
            .where(
                m.Measurement.id != row.id,
                cast(m.Measurement.data["supersedes"], Text).contains('"' + row.id + '"', autoescape=True),
            )
            .limit(1)
        ):
            fail(409, "quality_measurement_superseded", "此测量已由新映射追加更正，须引用当前事实")
        source_grain = row.data.get("granularity", source_identity.get("granularity"))
        if source_grain and source_grain != data.granularity:
            fail(422, "quality_source_granularity", "源测量对象粒度与本次声明不一致")
        numeric = s.exact(row.data.get("original_value", row.value))
        original_unit = row.data.get("original_unit", row.unit)
        if original_unit == "kPa" and row.unit == "Pa":
            numeric *= Decimal(1000)
        elif original_unit == "mbar" and row.unit == "Pa":
            numeric *= Decimal(100)
        elif original_unit == "K" and row.unit == "degC":
            numeric -= Decimal("273.15")
        elif original_unit != row.unit:
            fail(422, "quality_source_conversion", "原始单位与规范测量间缺少确定性转换依据")
        result = {
            "kind": "measurement",
            "value": str(numeric),
            "unit": row.unit,
            "quality": row.quality,
            "occurred_at": row.occurred_at,
            "association": row.data.get("association", "unresolved") if source_grain else "unresolved",
            "granularity": source_grain,
            "original_value": str(row.data.get("original_value", row.value)),
            "original_unit": original_unit,
            "metric": row.metric,
            "equipment_id": row.equipment_id,
        }
    else:
        if row.object_id != data.object_id or row.granularity != data.granularity:
            fail(422, "quality_source_identity", "视觉结果对象与粒度不匹配")
        if svc.db.scalar(
            svc.scoped(m.Inspection)
            .where(m.Inspection.result["correction_of"].as_string() == row.id)
            .limit(1)
        ):
            fail(409, "quality_source_corrected", "此检测已被追加更正，须选择当前有效事实")
        result = {
            "kind": "inspection",
            "value": row.status,
            "unit": data.unit,
            "source_verdict": row.status,
            "occurred_at": row.occurred_at,
            "quality": "Good" if row.status in {"OK", "NG"} and row.result.get("applicable", True) else "Bad",
            "association": row.association,
            "model_confidence": row.result.get("detection_confidence"),
            "sample_count": row.result.get("inspected_count", 1),
            "result": row.result,
        }
    result["source_version"] = actual_version
    if data.source_kind != "inspection" and data.value is not None:
        try:
            same = s.exact(data.value) == s.exact(result["value"])
        except ValueError:
            same = str(data.value) == str(result["value"])
        if not same:
            fail(422, "quality_source_value", "类型化核对不能静默改写原始测量值")
    return result


def assess(svc, data):
    with localcontext() as context:
        context.prec = 128
        return _assess(svc, data)


def _assess(svc, data):
    line = line_scope(svc, data.line_id, "inspect")
    if line.data.get("process") != data.process:
        fail(422, "quality_process", "检测工艺与实际产线不一致")
    evidence(svc, data.evidence_ids, data.line_id)
    if data.cohort_id:
        cohort = svc.get(m.ProductionCohort, data.cohort_id)
        if cohort.scope_line_id != data.line_id or cohort.product_id != data.product_id:
            fail(422, "quality_sample_cohort", "检测样本须明确关联同产线、同产品的生产总体")
    context = data.model_dump(mode="json")
    source = source_observation(svc, data)
    reason = []
    outcome = "review"
    value = None
    standard = None
    method = None
    plan = None
    input_value = source.get("value")
    snapshot = {
        "contract": "quality-assessment-v1",
        "input": context,
        "source": json_value(source),
        "source_hash": digest(json_value(source)),
        "platform_verdict_separate": True,
        "release": "not_evaluated",
    }
    try:
        standard = effective(
            svc,
            m.DecisionDefinition,
            data.standard_id,
            context=context,
            line_id=data.line_id,
            kind="quality_standard",
        )
        spec = s.StandardSpec.model_validate(standard.payload)
        if data.method_id != spec.method_id:
            fail(422, "quality_method_mismatch", "实际方法不匹配批准标准")
        method = effective(
            svc, m.DecisionDefinition, data.method_id, context=context, line_id=data.line_id, kind="method"
        )
        method_spec = s.MethodSpec.model_validate(method.payload)
        snapshot.update(standard=serialize(standard), method=serialize(method))
        if data.source_kind in {"measurement", "lab"} and source.get("metric") != method_spec.item:
            fail(422, "quality_physical_meaning", "来源指标不是此批准检测项目，设定/过程估计不能替代产品实测")
        if data.source_kind == "inspection" and method_spec.result_type != "visual":
            fail(422, "quality_visual_boundary", "外观视觉结果不能证明未测的附着力、膜厚或固化性能")

        if method_spec.item != spec.item or method_spec.granularity != data.granularity:
            fail(422, "quality_method_applicability", "检验项目或对象粒度不匹配批准方法")
        if data.source_kind == "lab" and source["method"] not in {
            method.code,
            method.name,
            method_spec.method_version,
        }:
            fail(422, "quality_method_source", "实验室原记录方法未映射到本次批准方法版本")
        if (current_time() - data.occurred_at).total_seconds() > method_spec.max_age_seconds:
            fail(422, "quality_measurement_stale", "检测结果超过批准方法的有效时间窗")
        if method_spec.calibration_required or method_spec.qualification_required:
            qualification = effective(
                svc,
                m.DecisionDefinition,
                data.qualification_id,
                line_id=data.line_id,
                context=context,
                kind="qualification",
            )
            q = s.QualificationSpec.model_validate(qualification.payload)
            if q.method_id != method.id or q.equipment_id != data.equipment_id:
                fail(422, "quality_qualification", "资格证据的设备或方法不匹配")
            if method_spec.calibration_required and not q.calibration_included:
                fail(422, "quality_calibration", "批准方法要求有效校准证据")
            if not aware(qualification.valid_from) <= data.occurred_at < aware(qualification.valid_until):
                fail(422, "quality_calibration_expired", "实际检测时点不在校准/资格有效期内")
            snapshot["qualification"] = serialize(qualification)
        if data.plan_id:
            plan = effective(
                svc,
                m.DecisionDefinition,
                data.plan_id,
                context=context,
                line_id=data.line_id,
                kind="quality_plan",
            )
            if standard.id not in plan.payload["standard_ids"]:
                fail(422, "quality_plan_item", "本标准不属于当前检验计划")
            snapshot["plan"] = serialize(plan)
        if input_value is None or data.quality == "missing":
            outcome = "not_tested"
            reason.append("该项目未取得实际结果，不补零或通过")
        elif source.get("quality") not in {"Good", "manual_verified"}:
            outcome = "invalid"
            reason.append("来源质量无效或不确定")
        elif source.get("association") == "unresolved":
            outcome = "review"
            reason.append("对象关联尚未明确")
        elif method_spec.result_type == "numeric":
            try:
                value = s.exact(input_value)
                if len(value.as_tuple().digits) > 80:
                    raise ValueError("检测数值有效位超过受控比较边界")
            except ValueError:
                outcome = "invalid"
                reason.append("原始字符串不能解析为有限数值")
            else:
                factor = (
                    Decimal(1) if data.unit == spec.unit else method_spec.approved_unit_factors.get(data.unit)
                )
                if factor is None:
                    outcome = "invalid"
                    reason.append("单位不匹配且无批准换算依据")
                else:
                    value *= factor
                    uncertainty = data.uncertainty * factor
                    snapshot["conversion"] = {
                        "original": str(input_value),
                        "original_unit": data.unit,
                        "factor": str(factor),
                        "standard_unit": spec.unit,
                    }
                    if spec.rounding != "none":
                        value = value.quantize(
                            Decimal(1).scaleb(-spec.rounding_places),
                            rounding=ROUND_HALF_UP if spec.rounding == "half_up" else ROUND_HALF_EVEN,
                        )
                    snapshot["conversion"]["exact_compared_value"] = str(value)
                    if abs(value) >= Decimal("10000000000000000000") or value != value.quantize(
                        Decimal("0.000000001")
                    ):
                        value = None
                        fail(
                            422,
                            "quality_numeric_precision",
                            "规范数值超过存储精度；须批准明确精度规则，不能静默截断后判通过",
                        )
                    low, high = spec.minimum, spec.maximum

                    def inside(v):
                        return (low is None or (v >= low if spec.lower_inclusive else v > low)) and (
                            high is None or (v <= high if spec.upper_inclusive else v < high)
                        )

                    left, right = value - uncertainty, value + uncertainty
                    intersects_valid = (
                        low is None or right > low or spec.lower_inclusive and right == low
                    ) and (high is None or left < high or spec.upper_inclusive and left == high)
                    if (
                        spec.uncertainty_policy == "review_overlap"
                        and uncertainty
                        and intersects_valid
                        and not (inside(left) and inside(right))
                    ):
                        outcome = "review"
                        reason.append("测量不确定区间跨越批准边界")
                    elif spec.uncertainty_policy == "conservative":
                        outcome = (
                            "pass" if inside(value - uncertainty) and inside(value + uncertainty) else "fail"
                        )
                    else:
                        outcome = "pass" if inside(value) else "fail"
        else:
            outcome = "pass" if str(input_value) in spec.allowed_values else "fail"
            if data.source_kind == "inspection" and source.get("source_verdict") not in {"OK", "NG"}:
                outcome = "invalid"
                reason.append("视觉子系统未给出有效检测结论")
        if plan and data.source_kind == "inspection":
            threshold = plan.payload.get("visual_review_below")
            confidence = source.get("model_confidence")
            confidence = s.exact(confidence) if confidence is not None else None
            if threshold is not None and (confidence is None or confidence < s.exact(threshold)):
                outcome = "review"
                reason.append("视觉置信度未达到批准复核策略，转人工")
    except HTTPException as exc:
        code = exc.detail.get("code", "invalid") if isinstance(exc.detail, dict) else "invalid"
        message = (
            exc.detail.get("message", "输入定义无效") if isinstance(exc.detail, dict) else "输入定义无效"
        )
        outcome = (
            "review"
            if code in {"not_found", "industrial_definition_inactive", "industrial_applicability"}
            else "invalid"
        )
        reason.append(message)
        snapshot["validation_error"] = code
    if data.supersedes_id:
        old = svc.get(m.QualityAssessment, data.supersedes_id, True)
        if (
            old.scope_line_id != data.line_id
            or old.object_id != data.object_id
            or old.definition_id != (standard.id if standard else None)
        ):
            fail(422, "quality_correction_scope", "更正须保持对象、标准与产线一致")
        if svc.db.scalar(svc.scoped(m.QualityAssessment).where(m.QualityAssessment.supersedes_id == old.id)):
            fail(409, "quality_correction_branch", "原判定已有更正，须从当前版本继续追加")
        snapshot["correction"] = {"previous": old.id, "reason": data.correction_reason}
    if not reason:
        reason = ["按已批准方法与标准确定性比对；不等同整批放行或实际出库"]
    row = svc.add(
        m.QualityAssessment,
        definition_id=standard.id if standard else None,
        method_id=method.id if method else None,
        plan_id=plan.id if plan else None,
        object_id=data.object_id,
        granularity=data.granularity,
        product_id=data.product_id,
        process=data.process,
        step_id=data.step_id,
        outcome=outcome,
        source_kind=data.source_kind,
        source_id=data.source_id,
        source_version=source.get("source_version") or data.source_version or snapshot["source_hash"],
        original_value=data.original_value or str(input_value)
        if input_value is not None
        else data.original_value,
        value=value,
        unit=data.unit,
        occurred_at=data.occurred_at,
        author_id=svc.actor.id,
        snapshot=json_value(snapshot),
        reasons=reason,
        supersedes_id=data.supersedes_id,
        scope_line_id=data.line_id,
    )
    svc.audit("quality.assessed", row.id, {"outcome": outcome, "standard_id": data.standard_id})
    return serialize(row)


def current_assessment(svc, id):
    row = svc.get(m.QualityAssessment, id)
    if svc.db.scalar(svc.scoped(m.QualityAssessment).where(m.QualityAssessment.supersedes_id == row.id)):
        fail(409, "quality_assessment_superseded", "判定已被更正，不能复用旧结论")
    snapshot = row.snapshot
    if row.source_kind == "plan":
        for sample in snapshot.get("assessments", []):
            current = current_assessment(svc, sample["id"])
            if current.version != sample["version"]:
                fail(409, "quality_plan_sample_changed", "计划中的样本判定版本已变化")
    for key in ["standard", "method", "plan", "qualification"]:
        if snapshot.get(key):
            original = snapshot[key]
            now = effective(svc, m.DecisionDefinition, original["id"], line_id=row.scope_line_id)
            if now.version != original["version"]:
                fail(409, "quality_assessment_changed", "所用方法、标准、资格或计划版本已变化，须重新评估")
    if snapshot.get("input", {}).get("source_kind") == "inspection":
        original = snapshot["input"]["source_id"]
        if svc.db.scalar(
            svc.scoped(m.Inspection)
            .where(m.Inspection.result["correction_of"].as_string() == original)
            .limit(1)
        ):
            fail(409, "quality_assessment_source_changed", "原始检测已更正，须重新评估")
    if snapshot.get("input"):
        original_input = s.QualityObservation.model_validate(snapshot["input"])
        if (
            snapshot.get("method")
            and (current_time() - original_input.occurred_at).total_seconds()
            > snapshot["method"]["payload"]["max_age_seconds"]
        ):
            fail(409, "quality_assessment_expired", "原实测结果超过批准可复用时间窗")
        if original_input.source_kind != "manual":
            current = source_observation(svc, original_input)
            if digest(json_value(current)) != snapshot["source_hash"]:
                fail(409, "quality_assessment_source_changed", "原始检测内容或映射事实已变化")
    return row


def assess_plan(svc, data):
    from pydantic import ValidationError

    line = line_scope(svc, data.line_id, "inspect")
    plan = effective(svc, m.DecisionDefinition, data.plan_id, line_id=data.line_id, kind="quality_plan")
    try:
        spec = s.QualityPlanSpec.model_validate(plan.payload)
    except ValidationError:
        fail(409, "quality_plan_rule", "历史检验计划不满足当前完整性规则，请修订并独立批准后重新评估")
    rows = [current_assessment(svc, id) for id in data.assessment_ids]
    if len(set(data.assessment_ids)) != len(rows):
        fail(422, "quality_plan_duplicates", "不得重复引用判定计数")
    if any(r.scope_line_id != data.line_id or r.definition_id not in spec.standard_ids for r in rows):
        fail(422, "quality_plan_scope", "判定不属于本计划标准或产线")
    cohort = svc.get(m.ProductionCohort, data.cohort_id) if data.cohort_id else None
    if cohort and (
        cohort.scope_line_id != data.line_id
        or cohort.external_id != data.object_id
        or cohort.granularity != spec.granularity
    ):
        fail(422, "quality_plan_cohort", "生产群体与计划目标不一致")
    if len({(r.object_id, r.definition_id) for r in rows}) != len(rows):
        fail(422, "quality_plan_multiple_versions", "同一对象/标准只能计当前唯一判定")
    if cohort and any(r.product_id != cohort.product_id for r in rows):
        fail(422, "quality_plan_product", "样本产品须与生产群体一致")
    if cohort and any(r.snapshot.get("input", {}).get("cohort_id") != cohort.id for r in rows):
        fail(
            422,
            "quality_plan_sample_identity",
            "样本必须在原检测记录明确关联本生产总体，不能只因同产品就推定属于本批",
        )
    if any(not fits(svc, plan, r.snapshot.get("input", {}), data.line_id) for r in rows):
        fail(422, "quality_plan_applicability", "样本不属于计划批准适用范围")
    by_item = {id: {r.object_id: r for r in rows if r.definition_id == id} for id in spec.standard_ids}
    objects = {r.object_id for r in rows}
    missing_by_object = {
        object_id: [id for id in spec.standard_ids if object_id not in by_item[id]]
        for object_id in sorted(objects)
    }
    complete = len(objects) >= spec.required_sample_count and not any(missing_by_object.values())
    if spec.sampling == "all_objects":
        complete = complete and bool(
            cohort
            and cohort.closed
            and cohort.object_ids
            and all(r.granularity == cohort.ledger.get("object_granularity") for r in rows)
            and all(set(by_item[id]) == set(cohort.object_ids) for id in spec.standard_ids)
        )
    else:
        complete = complete and bool(cohort and cohort.closed)
    outcomes = [r.outcome for r in rows]
    confidence_review = []
    if spec.visual_review_below is not None:
        for item in rows:
            if item.source_kind == "inspection":
                confidence = item.snapshot.get("source", {}).get("model_confidence")
                if confidence is None or s.exact(confidence) < spec.visual_review_below:
                    confidence_review.append(item.id)
    failures = {r.object_id for r in rows if r.outcome == "fail"}
    if len(failures) > spec.maximum_failed_samples:
        outcome = "fail"
    elif not complete:
        outcome = "not_tested"
    elif "invalid" in outcomes:
        outcome = "invalid"
    elif confidence_review or any(o in {"not_tested", "review"} for o in outcomes):
        outcome = "review"
    else:
        outcome = "pass"
    result = {
        "plan": serialize(plan),
        "assessments": [serialize(r) for r in rows],
        "cohort": serialize(cohort) if cohort else None,
        "coverage_complete": complete,
        "complete_object_count": sum(not missing for missing in missing_by_object.values()),
        "missing_standards_by_object": {id: missing for id, missing in missing_by_object.items() if missing},
        "confidence_review_ids": confidence_review,
        "sampling": spec.sampling,
        "claim": "抽样接受结论" if spec.sampling == "sampled_lot" else "实际唯一对象全检计划结论",
        "single_piece_all_tested": bool(
            complete
            and spec.sampling == "all_objects"
            and cohort.ledger.get("object_granularity") == "bottle"
        ),
        "release": "requires_separate_human_release",
    }
    row = svc.add(
        m.QualityAssessment,
        definition_id=plan.id,
        method_id=None,
        plan_id=plan.id,
        object_id=data.object_id,
        granularity=spec.granularity,
        product_id=cohort.product_id if cohort else "",
        process=line.data["process"],
        step_id="",
        outcome=outcome,
        source_kind="plan",
        source_id=data.cohort_id or "",
        source_version=digest(result),
        original_value="",
        value=None,
        unit="",
        occurred_at=current_time(),
        author_id=svc.actor.id,
        snapshot=json_value(result),
        reasons=["逐对象核对所有必需项目；不同样本的部分通过不能拼成完整样本；不自动批准放行"],
        scope_line_id=data.line_id,
    )
    svc.audit("quality.plan_assessed", row.id, {"outcome": outcome, "complete": complete})
    return serialize(row)


def cohort(svc, data):
    line_scope(svc, data.line_id, "inspect")
    evidence(svc, data.evidence_ids, data.line_id)
    if (
        len(data.object_ids) != len(set(data.object_ids))
        or data.object_ids
        and Decimal(len(data.object_ids)) != data.expected_quantity
    ):
        fail(422, "cohort_identity", "唯一对象列表须完整对应群体数量，不能重复或伪装单件追溯")
    for value in [
        data.inspected_quantity,
        data.final_good_quantity,
        data.released_quantity,
        data.shipped_quantity,
        data.scrapped_quantity,
    ]:
        if value is not None and value > data.expected_quantity:
            fail(422, "cohort_quantity", "数量不能超过生产群体")
    if data.expected_quantity != data.expected_quantity.to_integral_value():
        fail(422, "cohort_quantity", "产品数量须为整数，不能用材料数量冒充件数")
    ledger = data.model_dump(
        mode="json",
        exclude={
            "line_id",
            "external_id",
            "granularity",
            "product_id",
            "expected_quantity",
            "closed",
            "object_ids",
            "evidence_ids",
        },
    )
    conflicts = []
    for name in (
        "inspected_quantity",
        "final_good_quantity",
        "released_quantity",
        "shipped_quantity",
        "scrapped_quantity",
    ):
        value = getattr(data, name)
        if value is not None and value != value.to_integral_value():
            fail(422, "cohort_count_integer", "产品计数必须是整数，不能默认为材料数量")
    for lower, upper in (
        ("released_quantity", "final_good_quantity"),
        ("shipped_quantity", "released_quantity"),
    ):
        if (
            getattr(data, lower) is not None
            and getattr(data, upper) is not None
            and getattr(data, lower) > getattr(data, upper)
        ):
            conflicts.append(lower + " exceeds " + upper)
    if (
        data.final_good_quantity is not None
        and data.scrapped_quantity is not None
        and data.final_good_quantity + data.scrapped_quantity > data.expected_quantity
    ):
        conflicts.append("final_good_plus_scrap_exceeds_population")
    ledger["reconciliation"] = {
        "status": "review" if conflicts else "declared_counts_require_source_confirmation",
        "conflicts": conflicts,
    }
    row = svc.add(
        m.ProductionCohort,
        **data.model_dump(exclude=set(ledger) | {"line_id"}),
        ledger=ledger,
        scope_line_id=data.line_id,
    )
    svc.audit("production.cohort_recorded", row.id)
    return serialize(row)
