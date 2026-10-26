import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from quality import learning_schemas as learning
from quality import enterprise_schemas as industrial
from quality.business_protocol import BusinessRequest, SignedBusinessReceipt
from quality.industrial_tools import InvokeInput, InvokeResult
from quality.app import create_app
from quality.config import Settings
from quality.models import Base
from quality.schemas import InspectionInput, PlanInput, ReceiptInput, RetestInput, ModelOutput, DraftPlanInput, ReopenInput, ReleaseInput
from quality.adapters import AdapterConfig
from quality.agent import RuleInput
from quality.anomalies import AnomalyPolicy
from quality.distribution import RolloutInput, DecisionInput
from quality.evidence_tasks import TaskDecision
from quality.recipes import RecipeInput
from quality.sources import SourceRegistry, PointMapping, MappingDraft, MappingReview
from quality.querying import QueryScope
from quality.workspace import AssistantInput, ToolResult

target = Path("schemas")
target.mkdir(exist_ok=True)
app = create_app(Settings(_env_file=None))
schema = app.openapi()
schema.setdefault("components", {}).setdefault("securitySchemes", {}).update(
    {
        "SessionCookie": {"type": "apiKey", "in": "cookie", "name": "quality_session"},
        "SourceBearer": {"type": "http", "scheme": "bearer"},
    }
)
for path, methods in schema["paths"].items():
    for method, operation in methods.items():
        if path == "/api/center/evidence":
            operation["security"] = []
            operation["x-authentication"] = "Ed25519 signed request body; pretrusted center key; purpose/tenant/site/node/line/file/expiry binding"
        elif path not in ("/api/health", "/api/live", "/api/ready", "/api/auth/login"):
            operation["security"] = (
                [{"SourceBearer": []}]
                if "/ingest/visual/" in path or "/sync/" in path or '/industrial/business-callbacks/' in path
                else [{"SessionCookie": []}]
            )
            if operation["security"] == [{"SessionCookie": []}] and method not in {"get", "head", "options"}:
                operation['x-csrf'] = 'X-CSRF-Token must match quality_csrf cookie and the authenticated session digest'
(target / "openapi.json").write_text(json.dumps(schema, ensure_ascii=False, indent=2))
for name, cls in [
    ("visual-result", InspectionInput),
    ("rectification-plan", PlanInput),
    ("execution-receipt", ReceiptInput),
    ("retest-result", RetestInput),
    ("model-output", ModelOutput),
    ("adapter-config", AdapterConfig),
    ("deployment-config", Settings),
    ("evidence-rule", RuleInput),
    ("anomaly-policy", AnomalyPolicy),
    ("distribution-rollout", RolloutInput),
    ("distribution-decision", DecisionInput),
    ("evidence-task-decision", TaskDecision),
    ("recipe-version", RecipeInput),
    ('source-registry', SourceRegistry), ('point-mapping', PointMapping),
    ('mapping-draft', MappingDraft), ('mapping-review', MappingReview),
    ('query-scope', QueryScope), ('assistant-input', AssistantInput), ('assistant-result', ToolResult),
    *[("learning-"+name, cls) for name, cls in [
        ("experience", learning.ExperienceInput), ("condition", learning.ConditionInput),
        ("condition-batch", learning.ConditionBatch), ("material", learning.MaterialInput),
        ("material-check", learning.MaterialCheck), ("decision", learning.LearningDecision),
        ("revision", learning.RevisionInput), ("dataset", learning.DatasetInput),
        ("rule", learning.RuleProposal), ("replay", learning.ReplayInput),
        ("task-action", learning.TaskAction), ("handoff", learning.HandoffInput), ("draft-save", learning.DraftSave)]],
    ('plan-draft', DraftPlanInput), ('case-reopen', ReopenInput), ('quality-release', ReleaseInput),
    ('business-request', BusinessRequest), ('business-receipt', SignedBusinessReceipt),
    ('industrial-tool-input', InvokeInput), ('industrial-tool-result', InvokeResult),
    *[("industrial-"+name, getattr(industrial, name)) for name in [
        'DefinitionInput','GovernanceAction','QualityObservation','PlanAssessmentInput','CohortInput',
        'ProcessDecisionInput','ActualPassageInput','ActualPassageBatch','MaterialItemInput','FormulationInput',
        'CalculationInput','MixingInput','PolicyInput','DigitalTaskInput','DigitalBatchInput','ApprovalInput',
        'TaskAction','ConnectorInput','ConnectorRevision','BusinessCommandInput','CommandAction','BusinessRecoveryInput','BomInput',
        'OrderInput','OrderRevisionInput','InventoryInput','ReservationInput','SupplyPlanInput','UsageInput',
        'DocumentInput','CostInput']],
]:
    (target / (name + ".schema.json")).write_text(
        json.dumps(cls.model_json_schema(), ensure_ascii=False, indent=2)
    )
events = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "Quality event envelope",
    "type": "object",
    "required": ["id", "sequence", "kind", "payload", "occurred_at"],
    "properties": {
        "id": {"type": "string"},
        "sequence": {"type": "integer", "minimum": 1},
        "kind": {
            "enum": ["case.changed", "inspection.recorded", "workflow.reviewed", "case.analysis.requested"]
        },
        "payload": {"type": "object"},
        "occurred_at": {"type": "string", "format": "date-time"},
    },
    "additionalProperties": False,
    "description": "经已注册站点身份/HMAC绑定；来源、目标、发送时间在签名批次body中；服务端记录接收时间与确认序号",
}
(target / "event-envelope.schema.json").write_text(json.dumps(events, ensure_ascii=False, indent=2))
dictionary = {
    name: {
        "columns": [
            {
                "name": c.name,
                "type": str(c.type),
                "nullable": c.nullable,
                "primary_key": c.primary_key,
                "foreign_keys": [str(f.target_fullname) for f in c.foreign_keys],
            }
            for c in table.columns
        ],
        "indexes": [
            {"name": i.name, "columns": [c.name for c in i.columns], "unique": i.unique}
            for i in sorted(table.indexes, key=lambda index: index.name)
        ],
    }
    for name, table in Base.metadata.tables.items()
}
(target / "data-dictionary.json").write_text(json.dumps(dictionary, ensure_ascii=False, indent=2))
print("已导出OpenAPI、事件、视觉、模型、适配器及数据字典契约")
