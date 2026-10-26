from .conftest import post
from .test_industrial_formulations import setup_formula, approve


def test_seven_capabilities_real_invocation_idempotency_and_failure_ledger(core):
    clients, line, file, _, stocks, body = setup_formula(core)
    formula = approve(clients, body)
    registry = clients["engineer"].get("/api/industrial/tools").json()
    assert len({t["role"] for t in registry["capabilities"]}) == 7
    assert all(
        t["input_schema"] and t["output_schema"] and t["idempotency"] for t in registry["capabilities"]
    )
    payload = {
        "tool": "formulation.calculate",
        "input": {
            "line_id": line["id"],
            "formulation_id": formula["id"],
            "formulation_version": formula["version"],
            "product_id": "test-product",
            "basis": "total",
            "quantity": "10",
            "unit": "kg",
            "inventory_ids": [s["id"] for s in stocks],
        },
    }
    result = post(clients["engineer"], "/industrial/tools/invoke", payload, key="fixed-tool-invocation-key")
    assert result["status"] == "success" and result["engine"] == "deterministic_tools"
    assert result["result"]["status"] == "ready"
    again = post(clients["engineer"], "/industrial/tools/invoke", payload, key="fixed-tool-invocation-key")
    assert result["invocation_id"] == again["invocation_id"]
    assert clients["admin"].get("/api/records/industrial-calculations").json()["total"] == 1
    invalid = post(
        clients["engineer"],
        "/industrial/tools/invoke",
        {"tool": "formulation.calculate", "input": {"sql": "not-a-tool"}},
    )
    assert invalid["status"] == "invalid" and invalid["error_code"] == "tool_input_schema"
    denied = post(clients["viewer"], "/industrial/tools/invoke", payload)
    assert denied["status"] == "forbidden" and not denied["result"]
    assert clients["viewer"].get("/api/records/industrial-tool-invocations").status_code == 403
    post(clients["admin"], "/industrial/tools/invoke", {"tool": "shell.exec", "input": {}}, expected=422)
    ledger = clients["admin"].get("/api/records/industrial-tool-invocations").json()
    assert ledger["total"] == 3
    assert {r["status"] for r in ledger["items"]} == {"success", "invalid", "forbidden"}
    assert all(r["authorization"]["engine"] == "deterministic_tools" for r in ledger["items"])
