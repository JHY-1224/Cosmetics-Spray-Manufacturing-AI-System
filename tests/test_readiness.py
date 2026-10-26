import threading
import time
from quality.worker import run


def test_liveness_database_dependencies_and_business_readiness_are_distinct(core):
    settings, _, clients, _ = core
    assert clients["admin"].get("/api/live").status_code == 200
    assert clients["admin"].get("/api/ready").status_code == 200
    unready = clients["admin"].get("/api/readiness/business")
    assert unready.status_code == 503
    assert unready.json()["dependencies"]["worker"]["status"] == "not_verified"
    assert clients["viewer"].get("/api/readiness/dependencies").status_code == 403
    stopping = threading.Event()
    worker = threading.Thread(target=run, args=(settings, stopping))
    worker.start()
    try:
        response = unready
        for _ in range(50):
            response = clients["admin"].get("/api/readiness/business")
            if response.status_code == 200:
                break
            time.sleep(0.1)
        assert response.status_code == 200, response.text
        assert response.json()["model"] == "disabled_deterministic_workflows_available"
        assert response.json()["field_acceptance"] == "not_performed"
    finally:
        stopping.set()
        worker.join(timeout=10)
    assert not worker.is_alive()
    assert clients["admin"].get("/api/readiness/business").status_code == 503
