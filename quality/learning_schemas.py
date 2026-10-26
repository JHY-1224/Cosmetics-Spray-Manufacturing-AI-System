"""Expert opinions, observations and labels are distinct typed records, not model weights."""

from datetime import datetime
import math
from typing import Literal
from pydantic import Field, model_validator
from .schemas import Strict, VersionInput
from .agent import RuleInput

Process = Literal["plastic_spray", "glass_vacuum"]
Dimension = Literal["equipment", "material", "process", "environment", "management"]


class Applicability(Strict):
    line_id: str = Field(min_length=1, max_length=100)
    process: Process
    product_ids: list[str] = Field(default_factory=list, max_length=100)
    product_families: list[str] = Field(default_factory=list, max_length=100)
    substrate_ids: list[str] = Field(default_factory=list, max_length=100)
    shapes: list[str] = Field(default_factory=list, max_length=100)
    step_ids: list[str] = Field(default_factory=list, max_length=100)
    route_ids: list[str] = Field(default_factory=list, max_length=100)


class HandoffStage(Strict):
    segment: Literal["device_to_server", "server_to_platform", "platform_processing"]
    owner: str = Field(min_length=2, max_length=120)
    receiver: str = Field(min_length=2, max_length=120)
    location: str = Field(min_length=2, max_length=300)
    format: str = Field(min_length=2, max_length=300)
    authorization: str = Field(min_length=2, max_length=300)
    error_handling: str = Field(min_length=3, max_length=1000)
    sample_ids: list[str] = Field(default_factory=list, max_length=30)
    acceptance_evidence_ids: list[str] = Field(default_factory=list, max_length=30)
    state: Literal[
        "unconfirmed", "fixture_validated", "sample_validated", "field_connected", "field_accepted"
    ] = "unconfirmed"

    @model_validator(mode="after")
    def proof(self):
        if self.state in {"fixture_validated", "sample_validated"} and not self.sample_ids:
            raise ValueError("样本验证状态必须引用可回查样本")
        if self.state in {"field_connected", "field_accepted"} and not self.acceptance_evidence_ids:
            raise ValueError("现场连通/验收状态必须引用验收证据")
        return self


class HandoffInput(Strict):
    title: str = Field(min_length=2, max_length=200)
    scope: Applicability
    stages: list[HandoffStage] = Field(min_length=3, max_length=3)
    gaps: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def distinct(self):
        if len({s.segment for s in self.stages}) != 3:
            raise ValueError("必须分别明确三段交接责任")
        return self


class ConditionInput(Strict):
    stable_id: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_.-]{0,99}$")
    name: str = Field(min_length=2, max_length=200)
    scope: Applicability
    dimension: Dimension
    data_type: Literal["number", "enum", "boolean", "text"]
    unit: str = Field(default="", max_length=40)
    allowed_values: list[str] = Field(default_factory=list, max_length=200)
    minimum: float | None = None
    maximum: float | None = None
    value_type: Literal["measured", "setpoint", "manual", "derived", "uncollected"]
    event_semantics: str = Field(min_length=2, max_length=500)
    quality_required: Literal["Good", "manual_verified"] = "Good"
    max_age_seconds: int = Field(default=3600, ge=1, le=604800)
    critical: bool = False
    source_id: str = Field(default="", max_length=100)
    source_field: str = Field(default="", max_length=200)
    meaning: str = Field(min_length=3, max_length=2000)

    @model_validator(mode="after")
    def limits(self):
        if any(v is not None and not math.isfinite(v) for v in [self.minimum, self.maximum]):
            raise ValueError("范围必须有限")
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("范围顺序无效")
        if self.data_type == "number" and not self.unit:
            raise ValueError("数值条件必须明确单位")
        if self.data_type == "enum" and not self.allowed_values:
            raise ValueError("枚举必须列出允许值")
        return self


class ConditionValue(Strict):
    definition_id: str
    definition_version: int = Field(ge=1)
    value: float | bool | str | None = None
    unit: str = Field(default="", max_length=40)
    occurred_at: datetime
    available_at: datetime
    quality: Literal["Good", "Bad", "Uncertain", "missing", "manual_verified"]
    source_kind: Literal["manual", "source_record"] = "manual"
    raw_record_id: str = ""
    evidence_ids: list[str] = Field(default_factory=list, max_length=20)
    reason: str = Field(min_length=3, max_length=1000)

    @model_validator(mode="after")
    def times(self):
        if (self.value is None) != (self.quality == "missing"):
            raise ValueError("缺测只记录missing和空值，实际值不得为空")
        if self.source_kind == "source_record" and not self.raw_record_id:
            raise ValueError("来源记录方式必须引用原始记录ID")
        if self.occurred_at.tzinfo is None or self.available_at.tzinfo is None:
            raise ValueError("时间须带时区")
        if isinstance(self.value, float) and not math.isfinite(self.value):
            raise ValueError("数值必须有限")
        return self


class ConditionBatch(VersionInput):
    values: list[ConditionValue] = Field(min_length=1, max_length=100)


class Opinion(Strict):
    expert_name: str = Field(min_length=2, max_length=120)
    dimension: Dimension
    hypothesis: str = Field(min_length=3, max_length=2000)
    support_ids: list[str] = Field(default_factory=list, max_length=100)
    counter_ids: list[str] = Field(default_factory=list, max_length=100)
    checked_facts: str = Field(default="", max_length=5000)
    check_process: str = Field(default="", max_length=5000)
    applicability: str = Field(min_length=3, max_length=2000)
    unknowns: str = Field(default="", max_length=2000)
    origin: Literal["human", "model_draft"] = "human"


class ExpertLabel(Strict):
    task: Literal["post_inspection_diagnosis", "pre_inspection_prediction"]
    label_type: Literal["inspection", "root_cause", "rectification_effect"]
    value: str = Field(min_length=1, max_length=500)
    basis_ids: list[str] = Field(default_factory=list, max_length=100)
    grade: Literal["hypothesis", "expert_checked", "retest_supported"] = "hypothesis"
    formed_at: datetime
    allow_evaluation: bool = False
    allow_training: bool = False
    reason: str = Field(min_length=3, max_length=2000)

    @model_validator(mode="after")
    def time(self):
        if self.formed_at.tzinfo is None:
            raise ValueError("标签形成时间须带时区")
        if (self.allow_training or self.allow_evaluation) and (
            self.grade == "hypothesis" or not self.basis_ids
        ):
            raise ValueError("未经核对/无依据标签不得准入")
        return self


class ExperienceInput(Strict):
    case_id: str
    case_version: int = Field(ge=1)
    title: str = Field(min_length=2, max_length=200)
    opinions: list[Opinion] = Field(default_factory=list, max_length=30)
    material_ids: list[str] = Field(default_factory=list, max_length=50)
    labels: list[ExpertLabel] = Field(default_factory=list, max_length=100)
    conclusion: Literal[
        "unverified", "reviewed_experience", "retest_supported", "failed", "normal_control"
    ] = "unverified"
    outcome_notes: str = Field(default="", max_length=10000)
    other_factors: list[str] = Field(default_factory=list, max_length=100)
    feedback: str = Field(default="", max_length=10000)
    previous_id: str | None = None


class MaterialInput(Strict):
    case_id: str
    file_id: str
    title: str = Field(min_length=2, max_length=200)


class MaterialCheck(VersionInput):
    reviewed_text: str = Field(max_length=200000)
    reason: str = Field(min_length=3, max_length=2000)
    confirmed_fields: list[str] = Field(default_factory=list, max_length=200)
    time_ranges: list[tuple[float, float]] = Field(default_factory=list, max_length=100)
    uncertainties: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def ranges(self):
        if any(not math.isfinite(a) or not math.isfinite(b) or a < 0 or b < a for a, b in self.time_ranges):
            raise ValueError("时间片范围无效")
        return self


class LearningDecision(VersionInput):
    action: Literal["submit", "publish", "reject", "retire", "rollback"]
    reason: str = Field(min_length=4, max_length=2000)
    confirm_labels: bool = False
    acknowledge_conflicts: bool = False


class RevisionInput(VersionInput):
    reason: str = Field(min_length=4, max_length=2000)


class DatasetItem(Strict):
    case_id: str
    case_version: int = Field(ge=1)
    sample_role: Literal["normal", "abnormal", "failed"]
    expected_match: bool | None = None
    label_basis: str = Field(min_length=3, max_length=2000)
    decision_time: datetime
    label_time: datetime

    @model_validator(mode="after")
    def times(self):
        if self.decision_time.tzinfo is None or self.label_time.tzinfo is None:
            raise ValueError("评估时点须带时区")
        return self


class DatasetInput(Strict):
    title: str = Field(min_length=2, max_length=200)
    scope: Applicability
    split: Literal["functional", "development", "validation", "locked_test"]
    task: Literal["post_inspection_diagnosis", "pre_inspection_prediction"] = "post_inspection_diagnosis"
    items: list[DatasetItem] = Field(min_length=1, max_length=200)
    usage_rights: str = Field(min_length=4, max_length=2000)
    target_definition: str = Field(min_length=3, max_length=1000)


class RuleProposal(Strict):
    title: str = Field(min_length=2, max_length=200)
    scope: Applicability
    rule: RuleInput
    definition_ids: list[str] = Field(min_length=1, max_length=100)
    experience_ids: list[str] = Field(default_factory=list, max_length=100)
    sop_ids: list[str] = Field(default_factory=list, max_length=100)
    rationale: str = Field(min_length=4, max_length=5000)
    previous_id: str | None = None


class ReplayInput(VersionInput):
    dataset_id: str


class TaskAction(Strict):
    action: Literal["retry", "cancel"]
    reason: str = Field(min_length=4, max_length=2000)


class DraftSave(VersionInput):
    payload: ExperienceInput | ConditionInput | RuleProposal | HandoffInput
