"""Real governance, precise dilution and actual weighing; isolated API fixtures only."""

from datetime import timedelta
from decimal import Decimal
import pytest
from quality.security import current_time
from .conftest import post
from .test_industrial_quality import original


def setup_formula(core, *, ratio="mass", dilution="parts", fraction="0", third=False, density=True):
    _, _, clients, lines = core
    line = lines[0]
    file = original(clients, "admin")
    materials = []
    stocks = []
    for i, role in enumerate(["main", "hardener"] + (["diluent"] if third else [])):
        item = post(
            clients["engineer"],
            "/industrial/materials",
            {
                "line_id": line["id"],
                "code": role,
                "name": "软件验证" + role,
                "unit": "kg",
                "density_kg_l": str(i + 1) if density else None,
                "density_basis": "软件验证材料证书，20摄氏度" if density else "",
            },
        )
        materials.append(item)
        stocks.append(
            post(
                clients["admin"],
                "/industrial/inventory",
                {
                    "line_id": line["id"],
                    "material_id": item["id"],
                    "warehouse": "test-WH",
                    "lot": "test-" + role,
                    "quality_status": "released",
                    "quantity_basis": "gross",
                    "on_hand": "100",
                    "held": "0",
                    "reserved": "0",
                    "unit": "kg",
                    "source_system": "synthetic-source",
                    "source_version": "1",
                    "observed_at": current_time().isoformat(),
                    "expires_at": (current_time() + timedelta(days=7)).isoformat(),
                    "fresh_seconds": 3600,
                    "evidence_ids": [file],
                },
            )
        )
    body = {
        "code": "FORM-1",
        "name": "软件验证混配",
        "scope": {"line_id": line["id"], "process": line["data"]["process"]},
        "valid_until": (current_time() + timedelta(days=7)).isoformat(),
        "evidence_ids": [file],
        "ratio_basis": ratio,
        "dilution_basis": dilution,
        "dilution_fraction": fraction,
        "resolution_kg": "0.005",
        "tolerance_fraction": "0.01",
        "pot_life_minutes": 120,
        "technical_version": "test-only-v1",
        "components": [
            {"material_id": item["id"], "role": role, "parts": parts}
            for item, role, parts in zip(materials, ["main", "hardener", "diluent"], ["4", "1", "1"])
        ],
    }
    return clients, line, file, materials, stocks, body


def approve(clients, body):
    row = post(clients["engineer"], "/industrial/formulations", body)
    row = post(
        clients["engineer"],
        f"/industrial/governance/formulation/{row['id']}",
        {"action": "submit", "version": row["version"], "reason": "提供可复核原始技术依据"},
    )
    return post(
        clients["approver"],
        f"/industrial/governance/formulation/{row['id']}",
        {"action": "publish", "version": row["version"], "reason": "独立核对材料、禁配与稀释口径"},
    )


def calculate(clients, line, formula, stocks, **patch):
    return post(
        clients["engineer"],
        "/industrial/calculations",
        {
            "line_id": line["id"],
            "formulation_id": formula["id"],
            "formulation_version": formula["version"],
            "product_id": "test-product",
            "basis": "main",
            "quantity": "10",
            "unit": "kg",
            "inventory_ids": [s["id"] for s in stocks],
            **patch,
        },
    )


@pytest.mark.parametrize(
    "basis,fraction,expected",
    [("main", "0.1", "1"), ("main_hardener", "0.1", "1.25"), ("final_total", "0.2", "3.125")],
)
def test_dilution_bases_are_distinct_and_actual_usage_is_not_calculation(core, basis, fraction, expected):
    clients, line, file, materials, stocks, body = setup_formula(
        core, dilution=basis, fraction=fraction, third=True
    )
    formula = approve(clients, body)
    calc = calculate(clients, line, formula, stocks)
    assert calc["status"] == "ready"
    result = {c["role"]: c for c in calc["result"]["components"]}
    assert Decimal(result["main"]["planned"]) == 10
    assert Decimal(result["hardener"]["planned"]) == Decimal("2.5")
    assert Decimal(result["diluent"]["planned"]) == Decimal(expected)
    assert clients["admin"].get("/api/records/industrial-actual-usage").json()["total"] == 0
    weighings = [
        {
            "material_id": item["id"],
            "inventory_id": stock["id"],
            "quantity": result[role]["planned"],
            "unit": "kg",
            "evidence_ids": [file],
        }
        for item, stock, role in zip(materials, stocks, ["main", "hardener", "diluent"])
    ]
    fact = {
        "line_id": line["id"],
        "calculation_id": calc["id"],
        "external_id": "mix-actual-1",
        "mixed_at": current_time().isoformat(),
        "weighings": weighings,
        "actual_confirmation": True,
    }
    post(
        clients["executor"], "/industrial/mixing-batches", {**fact, "weighings": weighings[:1]}, expected=422
    )
    mixed = post(clients["executor"], "/industrial/mixing-batches", fact)
    assert mixed["status"] == "recorded"
    assert mixed["snapshot"]["actual_weighings"][0]["quantity"] == weighings[0]["quantity"]
    assert clients["admin"].get("/api/records/industrial-actual-usage").json()["total"] == 3
    post(clients["executor"], "/industrial/mixing-batches", fact, expected=409)


def test_volume_density_rounding_and_unapproved_substitution(core):
    clients, line, _, materials, stocks, body = setup_formula(core, ratio="volume")
    formula = approve(clients, body)
    calc = calculate(clients, line, formula, stocks, basis="total", quantity="1", unit="L")
    result = {c["role"]: c for c in calc["result"]["components"]}
    assert Decimal(result["main"]["planned"]) == Decimal("0.8")
    assert Decimal(result["hardener"]["planned"]) == Decimal("0.4")
    calc = calculate(clients, line, formula, stocks, basis="total", quantity="1", unit="kg")
    for c in calc["result"]["components"]:
        assert Decimal(c["planned"]) % Decimal("0.005") == 0
    post(
        clients["engineer"],
        "/industrial/calculations",
        {
            "line_id": line["id"],
            "formulation_id": formula["id"],
            "formulation_version": formula["version"],
            "product_id": "test-product",
            "basis": "total",
            "quantity": "10",
            "unit": "kg",
            "substitutions": {materials[0]["id"]: materials[1]["id"]},
        },
        expected=422,
    )
    for invalid in [True, "NaN", "Infinity"]:
        post(
            clients["engineer"],
            "/industrial/calculations",
            {
                "line_id": line["id"],
                "formulation_id": formula["id"],
                "formulation_version": formula["version"],
                "product_id": "test-product",
                "basis": "total",
                "quantity": invalid,
                "unit": "kg",
            },
            expected=422,
        )


def test_volume_formula_requires_density_and_deviations_need_reason(core):
    clients, line, file, materials, stocks, body = setup_formula(core, density=False)
    post(clients["engineer"], "/industrial/formulations", {**body, "ratio_basis": "volume"}, expected=422)
    formula = approve(clients, body)
    calc = calculate(clients, line, formula, [], basis="total")
    assert calc["status"] == "blocked_inventory" and not calc["result"]["inventory_feasible"]
    calc = calculate(clients, line, formula, stocks)
    weighings = [
        {
            "material_id": item["id"],
            "inventory_id": stock["id"],
            "quantity": "50",
            "unit": "kg",
            "evidence_ids": [file],
        }
        for item, stock in zip(materials, stocks)
    ]
    fact = {
        "line_id": line["id"],
        "calculation_id": calc["id"],
        "external_id": "deviation-1",
        "mixed_at": current_time().isoformat(),
        "weighings": weighings,
        "actual_confirmation": True,
    }
    post(clients["executor"], "/industrial/mixing-batches", fact, expected=422)
    for w in weighings:
        w["deviation_reason"] = "软件异常事实验证：仪表记录与批准计划不一致"
    r = post(clients["executor"], "/industrial/mixing-batches", fact)
    assert r["status"] == "deviation"
