"""Strict, versioned contracts for deterministic industrial and business services."""

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Annotated, Literal, Union
from pydantic import AfterValidator, BeforeValidator, Field, model_validator, field_validator
from .schemas import Strict, VersionInput
from .learning_schemas import Applicability
from .security import current_time


def exact(value):
    if isinstance(value, bool):
        raise ValueError("布尔值不能作为数量或金额")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError("须为十进制数值") from None
    if not result.is_finite():
        raise ValueError("数量和金额必须有限")
    if abs(result.as_tuple().exponent) > 10000 or len(result.as_tuple().digits) > 1000:
        raise ValueError("数值指数或有效位超过受控解析边界")
    return result


def zoned(value):
    if value.tzinfo is None:
        raise ValueError("时点须包含时区")
    return value


Number = Annotated[Decimal, BeforeValidator(exact), Field(max_digits=28, decimal_places=9)]
Positive = Annotated[Number, Field(gt=0)]
Nonnegative = Annotated[Number, Field(ge=0)]
Fraction = Annotated[Number, Field(ge=0, lt=1)]
Timestamp = Annotated[datetime, AfterValidator(zoned)]
Process = Literal["plastic_spray", "glass_vacuum"]
Grain = Literal["bottle", "load", "batch", "sample"]
ActionName = Literal[
    "quality.assess",
    "formulation.calculate",
    "process.select",
    "retest.create",
    "inventory.reserve",
    "inventory.release",
    "purchase.draft",
    "purchase.submit",
]


class IndustrialScope(Applicability):
    product_versions: list[str] = Field(default_factory=list, max_length=100)
    equipment_ids: list[str] = Field(default_factory=list, max_length=100)


class GovernedInput(Strict):
    code: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=2, max_length=200)
    scope: IndustrialScope
    valid_from: Timestamp = Field(default_factory=current_time)
    valid_until: Timestamp
    evidence_ids: list[str] = Field(min_length=1, max_length=100)
    previous_id: str | None = None

    @model_validator(mode="after")
    def validity(self):
        if self.valid_until <= self.valid_from:
            raise ValueError("有效期结束须晚于开始")
        return self


class MethodSpec(Strict):
    kind: Literal["method"] = "method"
    item: str = Field(min_length=1, max_length=100)
    method_version: str = Field(min_length=1, max_length=100)
    result_type: Literal["numeric", "enumerated", "visual"]
    unit: str = Field(max_length=40)
    granularity: Grain
    calibration_required: bool = False
    qualification_required: bool = False
    max_age_seconds: int = Field(default=86400, ge=1, le=2678400)
    approved_unit_factors: dict[str, Positive] = Field(default_factory=dict, max_length=20)


class QualificationSpec(Strict):
    kind: Literal["qualification"] = "qualification"
    equipment_id: str = Field(min_length=1, max_length=100)
    method_id: str
    calibration_included: bool = False
    certificate_reference: str = Field(min_length=3, max_length=500)


class StandardSpec(Strict):
    kind: Literal["quality_standard"] = "quality_standard"
    item: str = Field(min_length=1, max_length=100)
    customer_reference: str = Field(min_length=1, max_length=120)
    method_id: str
    unit: str = Field(max_length=40)
    minimum: Number | None = None
    maximum: Number | None = None
    allowed_values: list[str] = Field(default_factory=list, max_length=100)
    rounding_places: int = Field(default=6, ge=0, le=9)
    rounding: Literal["half_up", "half_even", "none"] = "none"
    uncertainty_policy: Literal["review_overlap", "conservative", "ignore_approved"] = "review_overlap"
    lower_inclusive: bool = True
    upper_inclusive: bool = True

    @model_validator(mode="after")
    def bounds(self):
        if self.minimum is None and self.maximum is None and not self.allowed_values:
            raise ValueError("批准标准须有数值界限或允许枚举")
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("最小限值不能大于最大限值")
        if self.allowed_values and (self.minimum is not None or self.maximum is not None):
            raise ValueError("枚举与数值标准分开治理")
        return self


class QualityPlanSpec(Strict):
    kind: Literal["quality_plan"] = "quality_plan"
    standard_ids: list[str] = Field(min_length=1, max_length=100)
    granularity: Grain
    sampling: Literal["all_objects", "sampled_lot"]
    required_sample_count: int = Field(ge=1, le=1000000)
    maximum_failed_samples: int = Field(default=0, ge=0)
    sampling_rule_reference: str = Field(min_length=3, max_length=500)
    visual_review_below: Number | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def acceptance(self):
        if self.maximum_failed_samples >= self.required_sample_count:
            raise ValueError("允许失败数须小于必需不同样本数，不能批准全失败的通过规则")
        if self.sampling == "all_objects" and self.maximum_failed_samples:
            raise ValueError("全检计划不能按抽样接收数容许不合格对象后判整体通过")
        return self


class ProcessModeSpec(Strict):
    kind: Literal["process_mode"] = "process_mode"
    route_id: str
    from_step_id: str
    next_step_id: str
    required_standard_ids: list[str] = Field(default_factory=list, max_length=100)
    required_material_ids: list[str] = Field(default_factory=list, max_length=100)
    equipment_qualification_ids: list[str] = Field(default_factory=list, max_length=100)
    sop_ids: list[str] = Field(min_length=1, max_length=100)
    parameters: dict[str, Number] = Field(default_factory=dict, max_length=100)
    parameter_units: dict[str, str] = Field(default_factory=dict, max_length=100)
    max_cycles: int = Field(default=1, ge=1, le=10)


DefinitionSpec = Annotated[
    Union[MethodSpec, StandardSpec, QualityPlanSpec, ProcessModeSpec, QualificationSpec],
    Field(discriminator="kind"),
]


class DefinitionInput(GovernedInput):
    spec: DefinitionSpec


class GovernanceAction(VersionInput):
    action: Literal["submit", "publish", "reject", "retire", "rollback"]
    reason: str = Field(min_length=3, max_length=3000)


class QualityObservation(Strict):
    standard_id: str
    plan_id: str | None = None
    cohort_id: str | None = None
    line_id: str
    object_id: str = Field(min_length=1, max_length=120)
    granularity: Grain
    product_id: str = Field(min_length=1, max_length=100)
    product_version: str = Field(default="", max_length=100)
    substrate_id: str = Field(default="", max_length=100)
    process: Process
    step_id: str = Field(min_length=1, max_length=100)
    equipment_id: str = Field(default="", max_length=100)
    method_id: str
    source_kind: Literal["measurement", "lab", "inspection", "manual"]
    source_id: str = Field(default="", max_length=100)
    source_version: str = Field(default="", max_length=100)
    original_value: str = Field(default="", max_length=3000)
    value: Number | Annotated[str, Field(max_length=3000)] | None = None
    unit: str = Field(max_length=40)
    occurred_at: Timestamp
    available_at: Timestamp
    quality: Literal["Good", "manual_verified", "Bad", "Uncertain", "missing"]
    association: Literal["physical_tracking", "load_record", "batch_record", "manual_confirmed", "unresolved"]
    calibration_valid_until: Timestamp | None = None
    qualification_id: str = Field(default="", max_length=120)
    evidence_ids: list[str] = Field(min_length=1, max_length=100)
    uncertainty: Nonnegative = Decimal(0)
    inspection_round: int = Field(default=1, ge=1, le=100)
    sample_count: int = Field(default=1, ge=1, le=1000000)
    model_confidence: Number | None = Field(default=None, ge=0, le=1)
    supersedes_id: str | None = None
    correction_reason: str = Field(default="", max_length=3000)

    @model_validator(mode="after")
    def observation_times(self):
        if (
            self.available_at < self.occurred_at
            or self.available_at > current_time()
            or self.occurred_at > current_time()
        ):
            raise ValueError("实测与可用时点须有效且不能来自未来")
        if self.supersedes_id and len(self.correction_reason.strip()) < 5:
            raise ValueError("更正须明确原记录与原因")
        return self


class PlanAssessmentInput(Strict):
    plan_id: str
    line_id: str
    object_id: str
    assessment_ids: list[str] = Field(default_factory=list, max_length=1000)
    cohort_id: str | None = None


class ProcessDecisionInput(Strict):
    line_id: str
    route_id: str
    mode_id: str
    object_id: str = Field(min_length=1, max_length=120)
    product_id: str = Field(min_length=1, max_length=100)
    product_version: str = ""
    substrate_id: str = ""
    equipment_id: str = ""
    from_step_id: str
    assessment_ids: list[str] = Field(default_factory=list, max_length=100)
    material_ids: list[str] = Field(default_factory=list, max_length=100)
    qualification_ids: list[str] = Field(default_factory=list, max_length=100)
    cycle: int = Field(default=1, ge=1, le=100)


class ActualPassageInput(Strict):
    line_id: str
    object_id: str = Field(min_length=1, max_length=120)
    granularity: Grain
    route_id: str = Field(min_length=1, max_length=64)
    step_id: str = Field(min_length=1, max_length=64)
    source_id: str = Field(min_length=1, max_length=100)
    source_session_id: str = Field(min_length=1, max_length=100)
    source_record_id: str = Field(min_length=1, max_length=120)
    reported_sequence: int = Field(ge=1, le=2147483647)
    entered_at: Timestamp
    exited_at: Timestamp
    cycle: int = Field(default=1, ge=1, le=100)
    disposition_id: str = Field(default="", max_length=100)
    product_id: str = Field(default="", max_length=120)
    production_batch_id: str = Field(default="", max_length=120)
    evidence_ids: list[str] = Field(min_length=1, max_length=100)
    actual_confirmation: bool
    notes: str = Field(min_length=3, max_length=3000)


class ActualPassageBatch(Strict):
    records: list[ActualPassageInput] = Field(min_length=1, max_length=500)


class CohortInput(Strict):
    line_id: str
    external_id: str = Field(min_length=1, max_length=120)
    granularity: Grain
    object_granularity: Grain = "bottle"
    product_id: str
    expected_quantity: Positive
    closed: bool = False
    object_ids: list[str] = Field(default_factory=list, max_length=10000)
    inspected_quantity: Nonnegative | None = None
    final_good_quantity: Nonnegative | None = None
    released_quantity: Nonnegative | None = None
    shipped_quantity: Nonnegative | None = None
    scrapped_quantity: Nonnegative | None = None
    quantity_basis: str = Field(min_length=3, max_length=1000)
    evidence_ids: list[str] = Field(min_length=1, max_length=100)


class MaterialItemInput(Strict):
    line_id: str
    code: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=2, max_length=200)
    unit: Literal["kg", "g", "L", "mL", "piece"]
    density_kg_l: Positive | None = None
    density_basis: str = Field(default="", max_length=500)
    prohibited_material_ids: list[str] = Field(default_factory=list, max_length=100)


class ComponentInput(Strict):
    material_id: str
    role: Literal["main", "hardener", "diluent", "additive"]
    parts: Positive
    approved_substitute_ids: list[str] = Field(default_factory=list, max_length=100)


class FormulationInput(GovernedInput):
    ratio_basis: Literal["mass", "volume"]
    dilution_basis: Literal["parts", "main", "main_hardener", "final_total"] = "parts"
    dilution_fraction: Fraction = Decimal(0)
    resolution_kg: Positive = Decimal("0.001")
    tolerance_fraction: Fraction = Decimal("0.01")
    pot_life_minutes: int = Field(ge=1, le=10080)
    technical_version: str = Field(min_length=1, max_length=100)
    prohibited_pairs: list[tuple[str, str]] = Field(default_factory=list, max_length=100)
    components: list[ComponentInput] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def components_valid(self):
        if len({c.material_id for c in self.components}) != len(self.components):
            raise ValueError("同一材料不能重复列组分")
        if not any(c.role == "main" for c in self.components):
            raise ValueError("配方须有主剂")
        if self.dilution_basis != "parts" and sum(c.role == "diluent" for c in self.components) != 1:
            raise ValueError("百分比稀释口径须有且仅有一种稀释剂")
        return self


class CalculationInput(Strict):
    line_id: str
    formulation_id: str
    formulation_version: int = Field(ge=1)
    product_id: str
    product_version: str = ""
    substrate_id: str = ""
    equipment_id: str = ""
    basis: Literal["total", "main"]
    quantity: Positive
    unit: Literal["kg", "g", "L", "mL"]
    inventory_ids: list[str] = Field(default_factory=list, max_length=200)
    substitutions: dict[str, str] = Field(default_factory=dict, max_length=100)


class ActualWeighing(Strict):
    material_id: str
    inventory_id: str
    quantity: Positive
    unit: Literal["kg", "g", "L", "mL"]
    evidence_ids: list[str] = Field(min_length=1, max_length=100)
    deviation_reason: str = Field(default="", max_length=2000)


class MixingInput(Strict):
    line_id: str
    calculation_id: str
    external_id: str = Field(min_length=1, max_length=120)
    mixed_at: Timestamp
    order_line_id: str | None = None
    weighings: list[ActualWeighing] = Field(min_length=1, max_length=100)
    actual_confirmation: bool


class PolicyScope(IndustrialScope):
    quantity_unit: Literal["operation", "kg", "g", "L", "mL", "piece", "bottle", "load", "batch", "sample"]


class PolicyInput(GovernedInput):
    scope: PolicyScope
    priority: int = Field(default=0, ge=0, le=1000)
    effect: Literal["allow", "deny"] = "allow"
    automation_level: Literal["D1", "D2"]
    actor_ids: list[str] = Field(default_factory=list, max_length=100)
    actor_roles: list[str] = Field(min_length=1, max_length=20)
    actions: list[ActionName] = Field(min_length=1, max_length=8)
    object_ids: list[str] = Field(default_factory=list, max_length=1000)
    max_quantity: Positive
    max_amount: Nonnegative
    currency: str = Field(min_length=3, max_length=10)
    window_seconds: int = Field(default=3600, ge=1, le=86400)
    max_frequency: int = Field(ge=1, le=10000)
    offline_allowed: bool = False
    required_evidence_kinds: list[
        Literal["quality_assessment", "calculation", "source_receipt", "evidence"]
    ] = Field(default_factory=list, max_length=4)


class DigitalTaskInput(Strict):
    line_id: str
    action: ActionName
    object_id: str = Field(min_length=1, max_length=120)
    object_version: str = Field(min_length=1, max_length=100)
    policy_id: str | None = None
    quantity: Nonnegative = Decimal(0)
    amount: Nonnegative = Decimal(0)
    currency: str = Field(default="CNY", min_length=3, max_length=10)
    offline: bool = False
    evidence_ids: list[str] = Field(default_factory=list, max_length=100)
    payload: dict = Field(default_factory=dict)


class DigitalBatchInput(Strict):
    tasks: list[DigitalTaskInput] = Field(min_length=1, max_length=200)
    execute_valid: bool = False


class TaskAction(VersionInput):
    action: Literal["pause", "resume", "cancel", "retry"]
    reason: str = Field(min_length=3, max_length=2000)


class ConnectorInput(Strict):
    line_id: str
    name: str = Field(min_length=2, max_length=200)
    system: Literal["MES", "WMS", "ERP", "FINANCE"]
    endpoint: str = Field(min_length=8, max_length=500)
    credential_reference: str = Field(min_length=1, max_length=100)
    actions: list[ActionName] = Field(min_length=1, max_length=8)
    enabled: bool = False
    supports_idempotency: bool = False
    supports_lookup: bool = False
    authority: str = Field(min_length=3, max_length=120)
    contract_version: Literal["business-actions-v1"] = "business-actions-v1"


class BusinessActionPayload(Strict):
    action: Literal[
        "retest.create", "inventory.reserve", "inventory.release", "purchase.draft", "purchase.submit"
    ]
    target_id: str = Field(min_length=1, max_length=120)
    target_version: str = Field(min_length=1, max_length=100)
    material_id: str = Field(default="", max_length=100)
    quantity: Nonnegative = Decimal(0)
    unit: str = Field(default="", max_length=20)
    reference_id: str = Field(default="", max_length=160)
    reason: str = Field(min_length=3, max_length=2000)
    unit_price: Nonnegative | None = None
    currency: str = Field(default="CNY", min_length=3, max_length=10)
    tax_basis: Literal["tax_inclusive", "tax_exclusive"] = "tax_exclusive"
    tax_rate: Fraction = Decimal(0)

    @model_validator(mode="after")
    def action_contract(self):
        if self.action.startswith("purchase.") and (
            self.unit_price is None or not self.material_id or not self.unit or self.quantity <= 0
        ):
            raise ValueError("采购动作须有明确材料、数量、单位、报价与税币种依据")
        if self.action.startswith("inventory.") and (
            not self.material_id or not self.unit or not self.reference_id or self.quantity <= 0
        ):
            raise ValueError("库存动作须有明确材料、数量、单位和预占引用")
        if self.action == "retest.create" and (self.quantity != 1 or self.unit != "operation"):
            raise ValueError("复测工单创建须为一次明确数字操作")
        return self


class BusinessCommandInput(Strict):
    line_id: str
    connector_id: str
    payload: BusinessActionPayload
    policy_id: str | None = None
    approval_id: str | None = None
    evidence_ids: list[str] = Field(default_factory=list, max_length=50)
    prerequisite_ids: list[str] = Field(default_factory=list, max_length=100)
    expected_result: Literal[
        "work_order_created",
        "inventory_reserved",
        "inventory_released",
        "purchase_draft_created",
        "purchase_submitted",
    ]


class ApprovalInput(VersionInput):
    approved: bool
    reason: str = Field(min_length=3, max_length=2000)
    expires_at: Timestamp


class CommandAction(VersionInput):
    action: Literal["submit", "send", "lookup", "cancel"]
    reason: str = Field(min_length=3, max_length=2000)


class ConnectorRevision(ConnectorInput):
    version: int = Field(ge=1)


class BusinessRecoveryInput(Strict):
    node_epoch: str = Field(min_length=16, max_length=100)
    source_handover_confirmed: bool
    reason: str = Field(min_length=10, max_length=2000)


class BomLineInput(Strict):
    material_id: str | None = None
    child_bom_id: str | None = None
    quantity: Positive
    unit: str = Field(min_length=1, max_length=20)
    loss_fraction: Fraction = Decimal(0)
    conversion_factor: Positive = Decimal(1)
    conversion_basis: str = Field(min_length=3, max_length=500)

    @field_validator("material_id", "child_bom_id", mode="before")
    @classmethod
    def blank_reference(cls, value):
        return value or None

    @model_validator(mode="after")
    def one_target(self):
        if bool(self.material_id) == bool(self.child_bom_id):
            raise ValueError("BOM每行恰为物料或子BOM之一")
        return self


class BomInput(GovernedInput):
    product_id: str
    product_version: str = Field(min_length=1, max_length=100)
    output_quantity: Positive
    output_unit: str = Field(min_length=1, max_length=20)
    route_id: str
    components: list[BomLineInput] = Field(min_length=1, max_length=200)


class OrderLineInput(Strict):
    external_line: str = Field(min_length=1, max_length=100)
    product_id: str
    product_version: str
    quantity: Positive
    unit: str = Field(min_length=1, max_length=20)
    bom_id: str


class OrderInput(Strict):
    line_id: str
    external_id: str = Field(min_length=1, max_length=120)
    customer_reference: str = Field(min_length=1, max_length=120)
    source_system: str = Field(min_length=1, max_length=100)
    source_version: str = Field(min_length=1, max_length=100)
    due_at: Timestamp
    currency: str = Field(min_length=3, max_length=10)
    tax_basis: Literal["tax_inclusive", "tax_exclusive"]
    evidence_ids: list[str] = Field(default_factory=list, max_length=100)
    lines: list[OrderLineInput] = Field(min_length=1, max_length=200)


class OrderRevisionInput(OrderInput):
    version: int = Field(ge=1)


class InventoryInput(Strict):
    line_id: str
    material_id: str
    warehouse: str = Field(min_length=1, max_length=100)
    lot: str = Field(min_length=1, max_length=120)
    quality_status: Literal["released", "quarantined", "pending", "rejected"]
    quantity_basis: Literal["gross", "net_available"]
    on_hand: Nonnegative
    held: Nonnegative = Decimal(0)
    reserved: Nonnegative = Decimal(0)
    in_transit: Nonnegative = Decimal(0)
    reported_available: Nonnegative | None = None
    unit: Literal["kg", "g", "L", "mL", "piece"]
    source_system: str = Field(min_length=1, max_length=100)
    source_version: str = Field(min_length=1, max_length=100)
    observed_at: Timestamp
    expires_at: Timestamp
    fresh_seconds: int = Field(ge=1, le=2678400)
    evidence_ids: list[str] = Field(min_length=1, max_length=100)
    included_command_ids: list[str] = Field(default_factory=list, max_length=1000)
    included_usage_ids: list[str] = Field(default_factory=list, max_length=1000)
    quality_assessment_id: str | None = None

    @model_validator(mode="after")
    def quantities(self):
        if self.quantity_basis == "net_available" and self.reported_available is None:
            raise ValueError("净可用口径须明确reported_available")
        if (
            self.held + self.reserved > self.on_hand
            or self.reported_available is not None
            and self.reported_available > self.on_hand
        ):
            raise ValueError("冻结/占用或可用数量不能超过账存")
        if self.observed_at > current_time():
            raise ValueError("库存快照不能来自未来")
        return self


class SupplyPlanInput(Strict):
    order_id: str
    order_version: int = Field(ge=1)
    inventory_ids: list[str] = Field(min_length=1, max_length=1000)
    capacity_basis: str = Field(default="", max_length=1000)
    capacity_per_day: Positive | None = None
    calendar_available_days: Nonnegative | None = None
    changeover_days: Nonnegative | None = None
    arrival_at: Timestamp | None = None


class ReservationInput(Strict):
    inventory_id: str
    plan_id: str | None = None
    quantity: Positive


class UsageInput(Strict):
    line_id: str
    material_id: str
    inventory_id: str
    order_line_id: str
    source_id: str = Field(min_length=1, max_length=120)
    quantity: Positive
    unit: Literal["kg", "g", "L", "mL", "piece"]
    purpose: Literal["production", "rework", "scrap"]
    occurred_at: Timestamp
    evidence_ids: list[str] = Field(min_length=1, max_length=100)
    deviation_reason: str = Field(default="", max_length=3000)


class DocumentLineInput(Strict):
    external_line: str = Field(min_length=1, max_length=100)
    order_line_id: str
    material_id: str
    lot: str = Field(min_length=1, max_length=120)
    quantity: Nonnegative
    unit: str = Field(min_length=1, max_length=20)
    unit_price: Nonnegative
    reference_line_id: str | None = None


class DocumentInput(Strict):
    line_id: str
    kind: Literal["receipt", "return", "invoice"]
    external_id: str = Field(min_length=1, max_length=120)
    source_system: str = Field(min_length=1, max_length=100)
    order_id: str
    currency: str = Field(min_length=3, max_length=10)
    tax_basis: Literal["tax_inclusive", "tax_exclusive"]
    tax_rate: Fraction
    occurred_at: Timestamp
    evidence_ids: list[str] = Field(min_length=1, max_length=100)
    lines: list[DocumentLineInput] = Field(min_length=1, max_length=200)


class CostInput(Strict):
    order_id: str
    allocation_version: str = Field(min_length=1, max_length=100)
    evidence_ids: list[str] = Field(min_length=1, max_length=100)
    labor_hours: Nonnegative | None = None
    hourly_cost: Nonnegative | None = None
    labor_basis: str = Field(default="", max_length=2000)
    estimated_unit_cost: Nonnegative | None = None
    difference_resolutions: dict[str, str] = Field(default_factory=dict, max_length=1000)
