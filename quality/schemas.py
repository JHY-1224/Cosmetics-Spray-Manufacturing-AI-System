from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Login(Strict):
    username: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=1, max_length=256)


class UserInput(Strict):
    username: str = Field(min_length=2, max_length=120)
    password: str = Field(min_length=14, max_length=256)
    role: Literal["viewer", "inspector", "engineer", "approver", "executor", "quality", "admin", "planner", "storekeeper", "finance"]
    allowed_sites: list[str] = Field(min_length=1)
    allowed_lines: list[str] = Field(default_factory=list)


class MasterInput(Strict):
    kind: Literal[
        "enterprise",
        "factory",
        "workshop",
        "line",
        "station",
        "equipment",
        "product",
        "substrate",
        "recipe",
        "work_order",
        "production_batch",
        "material_batch",
        "hanger",
        "load",
        "equipment_run",
    ]
    code: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=200)
    parent_id: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class Defect(Strict):
    category: str = Field(min_length=1, max_length=80)
    count: int = Field(ge=1, le=1000000)
    detection_confidence: float | None = Field(default=None, ge=0, le=1)
    bbox: list[float] | None = Field(default=None, min_length=4, max_length=4)
    coordinate_system: Literal["pixels_xyxy", "normalized_xyxy"] | None = None
    image_width: int | None = Field(default=None, gt=0)
    image_height: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def coordinates(self):
        if self.bbox:
            if not self.coordinate_system or not self.image_width or not self.image_height:
                raise ValueError("边界框必须提供坐标约定及图像尺寸")
            x1, y1, x2, y2 = self.bbox
            maxx, maxy = (
                (1, 1)
                if self.coordinate_system == "normalized_xyxy"
                else (self.image_width, self.image_height)
            )
            if not 0 <= x1 < x2 <= maxx or not 0 <= y1 < y2 <= maxy:
                raise ValueError("边界框越界或坐标顺序错误")
        return self


class InspectionInput(Strict):
    record_id: str = Field(min_length=1, max_length=120)
    line_id: str
    object_id: str | None = None
    granularity: Literal["bottle", "load", "batch", "sample"]
    association: Literal["physical_tracking", "load_record", "batch_record", "manual_confirmed", "unresolved"]
    occurred_at: datetime
    status: Literal["OK", "NG", "failed", "out_of_scope"]
    defects: list[Defect] = Field(default_factory=list, max_length=1000)
    image_ids: list[str] = Field(default_factory=list, max_length=30)
    model_version: str
    detection_confidence: float | None = Field(default=None, ge=0, le=1)
    config_version: str
    applicable: bool = True
    error: str = ""
    inspected_count: int = Field(default=1, ge=1, le=1000000)
    defective_count: int = Field(default=0, ge=0)
    product_id: str = ""
    production_batch_id: str = ""
    material_batch_ids: list[str] = Field(default_factory=list)
    hanger_id: str = ""
    load_id: str = ""
    equipment_run_id: str = ""
    process: Literal["plastic_spray", "glass_vacuum"] = "plastic_spray"
    station_id: str = ""
    passage_id: str = ""
    correction_of: str | None = None
    correction_reason: str = ""
    source_session_id: str = Field(default="", max_length=100)
    inspection_event_id: str = Field(default="", max_length=120)
    inspection_round: int = Field(default=1, ge=1, le=100)
    inspection_kind: Literal["initial", "reinspection", "unknown"] = "unknown"
    view_id: str = Field(default="", max_length=100)
    furnace_run_id: str = Field(default="", max_length=120)
    rack_position: str = Field(default="", max_length=120)
    collection_scope: Literal['all_results', 'ng_only', 'unknown'] = 'unknown'

    @field_validator("occurred_at")
    @classmethod
    def time_zone(cls, value):
        if value.tzinfo is None:
            raise ValueError("发生时间必须带时区")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def counts(self):
        if self.inspection_kind == "initial" and self.inspection_round != 1:
            raise ValueError("初检必须为第1检验轮次")
        if self.inspection_kind == "reinspection" and self.inspection_round < 2:
            raise ValueError("复检必须明确第2次或后续检验轮次")
        if self.granularity == "bottle" and self.inspection_kind != "unknown" and self.inspected_count != 1:
            raise ValueError("已明确检验轮次的单瓶记录必须对应1个实际对象")
        if self.defective_count > self.inspected_count:
            raise ValueError("不良件数不能超过检测件数")
        if self.status == "NG" and self.defective_count == 0:
            raise ValueError("NG必须明确不良件数，不以缺陷数替代")
        if self.granularity == "bottle" and self.association not in (
            "physical_tracking",
            "manual_confirmed",
            "unresolved",
        ):
            raise ValueError("单瓶粒度需要物理追踪或人工确认依据")
        return self


class CaseInput(Strict):
    title: str = Field(min_length=2, max_length=200)
    line_id: str
    process: Literal["plastic_spray", "glass_vacuum"]
    inspection_ids: list[str] = Field(default_factory=list, max_length=300)
    evidence_ids: list[str] = Field(default_factory=list, max_length=100)
    route_id: str = ""
    step_id: str = Field(default="", max_length=100)
    recipe_id: str = ""
    product_id: str = ""
    product_family: str = Field(default="", max_length=120)
    substrate_id: str = Field(default="", max_length=120)
    shape: str = Field(default="", max_length=120)
    defect: str = Field(min_length=1, max_length=100)
    notes: str = Field(default="", max_length=5000)


class VersionInput(Strict):
    version: int = Field(ge=1)


class RetestPlan(Strict):
    method: str = Field(min_length=2, max_length=300)
    target_defects: list[str] = Field(min_length=1)
    sample_unit: Literal["bottle", "load", "batch", "sample"]
    min_samples: int = Field(ge=1)
    max_defect_rate: float = Field(ge=0, le=1)
    stabilization_minutes: int = Field(ge=0)
    observation_minutes: int = Field(ge=0)
    conditions: list[str] = Field(default_factory=list)
    other_constraints: list[str] = Field(default_factory=list)


class Action(Strict):
    action_id: str = Field(default="", max_length=100)
    owner_role: Literal["", "engineer", "executor", "quality", "inspector", "admin"] = ""
    owner_id: str = Field(default="", max_length=100)
    type: Literal["inspect", "measure", "sop_action"]
    description: str = Field(min_length=2, max_length=1000)
    sop_id: str | None = None
    parameters: dict[str, float] = Field(default_factory=dict)


class PlanInput(VersionInput):
    source_run_id: str = Field(default="", max_length=100)
    actions: list[Action] = Field(min_length=1, max_length=30)
    retest: RetestPlan
    expires_in_hours: int = Field(default=24, ge=1, le=168)


class ApprovalInput(VersionInput):
    plan_id: str
    approved: bool
    reason: str = Field(min_length=2, max_length=2000)


class ReceiptInput(VersionInput):
    plan_id: str
    executed_at: datetime
    object_ids: list[str] = Field(min_length=1)
    actual_actions: str = Field(min_length=3, max_length=5000)
    deviations: str = Field(max_length=3000)
    evidence_ids: list[str] = Field(min_length=1)
    success: bool
    completion: Literal['completed', 'partial', 'failed'] | None = None
    completed_action_indexes: list[int] = Field(default_factory=list, max_length=30)
    completed_action_ids: list[str] = Field(default_factory=list, max_length=30)
    action_results: list["ActionResult"] = Field(default_factory=list, max_length=30)


class ActionResult(Strict):
    action_id: str = Field(min_length=1, max_length=100)
    status: Literal["completed", "partial", "failed", "not_started"]
    notes: str = Field(min_length=3, max_length=2000)
    evidence_ids: list[str] = Field(default_factory=list, max_length=100)


class RetestInput(VersionInput):
    plan_id: str
    started_at: datetime
    finished_at: datetime
    sample_count: int = Field(ge=0)
    defective_count: int = Field(ge=0)
    sample_unit: Literal["bottle", "load", "batch", "sample"]
    evidence_ids: list[str] = Field(min_length=1)
    conditions_met: bool
    other_constraints_met: bool
    notes: str = Field(min_length=2, max_length=3000)


class CloseInput(VersionInput):
    root_cause_confirmation: Literal["unverified", "evidence_supported", "human_confirmed"]
    quality_disposition: Literal["pending", "rework", "strip", "scrap", "await_release"]
    reason: str = Field(min_length=3, max_length=2000)


class EvidenceUpdate(VersionInput):
    inspection_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    reason: str = Field(min_length=3, max_length=2000)
    purpose: Literal['critical', 'supplemental'] = 'critical'


class LabInput(Strict):
    line_id: str
    object_id: str
    metric: Literal["adhesion", "film_thickness", "color", "gloss", "curing", "other"]
    method: str = Field(min_length=2, max_length=200)
    value: str = Field(min_length=1, max_length=100)
    unit: str = Field(min_length=1, max_length=30)
    occurred_at: datetime
    evidence_ids: list[str] = Field(min_length=1)
    granularity: Literal["bottle", "load", "batch", "sample"]
    instrument_id: str = ""


class KnowledgeInput(Strict):
    category: Literal["defect", "root_cause", "sop", "case"]
    title: str = Field(min_length=2, max_length=200)
    content: str = Field(min_length=3, max_length=200000)
    process: Literal["plastic_spray", "glass_vacuum", "all"]
    scope: dict = Field(default_factory=dict)
    source: dict
    rules: dict = Field(default_factory=dict)
    valid_days: int = Field(default=365, ge=1, le=3650)
    previous_id: str | None = None

    @field_validator("scope")
    @classmethod
    def scope_schema(cls, value):
        allowed = {"factory_ids", "line_ids", "product_ids", "substrate_ids", "route_ids", "recipe_ids", "product_families", "shapes", "step_ids"}
        if set(value) - allowed or any(
            not isinstance(v, list)
            or len(v) > 500
            or any(not isinstance(i, str) or not 1 <= len(i) <= 120 for i in v)
            for v in value.values()
        ):
            raise ValueError("适用范围只能包含受控的标识数组")
        return value


class ReviewInput(Strict):
    action: Literal["submit", "publish", "reject", "retire", "rollback"]
    reason: str = Field(min_length=3, max_length=2000)


class RouteInput(Strict):
    name: str = Field(min_length=2, max_length=200)
    process: Literal["plastic_spray", "glass_vacuum"]
    steps: list[dict] = Field(min_length=2, max_length=100)
    disposition: dict = Field(default_factory=dict)
    previous_id: str | None = None


class PassageInput(Strict):
    line_id: str = ""
    object_id: str
    granularity: Literal["bottle", "load", "batch", "sample"]
    route_id: str
    step_id: str
    sequence: int = Field(ge=1)
    entered_at: datetime
    exited_at: datetime
    data: dict = Field(default_factory=dict)


class Candidate(Strict):
    dimension: Literal["equipment", "material", "process", "environment", "management"]
    cause: str = Field(min_length=2, max_length=500)
    support_refs: list[str]
    counter_refs: list[str]
    missing_evidence: list[str]
    ranking_basis: str
    applicability: str
    next_checks: list[str] = Field(min_length=1)
    status: Literal["unverified", "evidence_supported", "excluded", "human_confirmed"]
    relative_score: float = Field(ge=0, le=100)


class ModelOutput(Strict):
    candidates: list[Candidate] = Field(max_length=20)
    actions: list[Action] = Field(max_length=20)
    summary: str = Field(max_length=3000)


class DispositionInput(Strict):
    line_id: str
    object_id: str = Field(min_length=1, max_length=120)
    route_id: str
    case_id: str | None = None
    action: Literal["rework", "strip"]
    reason: str = Field(min_length=3, max_length=2000)
    evidence_ids: list[str] = Field(min_length=1)


class DispositionReview(VersionInput):
    approved: bool
    reason: str = Field(min_length=3, max_length=2000)


class ReleaseInput(VersionInput):
    reason: str = Field(min_length=3, max_length=2000)
    evidence_ids: list[str] = Field(default_factory=list, max_length=100)
    external_reference: str = Field(default="", max_length=500)


class ReopenInput(VersionInput):
    reason: str = Field(min_length=5, max_length=2000)
    evidence_ids: list[str] = Field(min_length=1, max_length=100)


class DraftPlanInput(VersionInput):
    source_run_id: str = Field(default="", max_length=100)
    actions: list[Action] = Field(min_length=1, max_length=30)
    retest: RetestPlan
    expires_in_hours: int = Field(default=24, ge=1, le=168)
