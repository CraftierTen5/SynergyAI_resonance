import pytest
from fastapi.testclient import TestClient

from app.main import app


def test_frontend_routes_and_static_serving():
    client = TestClient(app)

    # 1. Root JSON contract for existing health/readiness checks
    res_json = client.get("/")
    assert res_json.status_code == 200
    assert res_json.json() == {"service": "ResonanceAI Backend", "status": "ok"}

    # 2. Browser request with text/html returns the HTML application
    res_html = client.get("/", headers={"accept": "text/html,application/xhtml+xml"})
    assert res_html.status_code == 200
    assert "ResonanceAI" in res_html.text
    assert "Mobile App Look" in res_html.text

    # 3. Direct /app route
    res_app = client.get("/app")
    assert res_app.status_code == 200
    assert "ResonanceAI" in res_app.text

    # 4. Static assets
    res_css = client.get("/static/styles.css")
    assert res_css.status_code == 200
    assert "--sage-primary" in res_css.text

    res_js = client.get("/static/app.js")
    assert res_js.status_code == 200
    assert "ResonanceApp" in res_js.text

    res_api = client.get("/static/api.js")
    assert res_api.status_code == 200
    assert "ResonanceAPI" in res_api.text


def test_api_assignments_and_disclosure_receipt():
    client = TestClient(app)

    # Assignments list
    res = client.get("/api/assignments")
    assert res.status_code == 200
    assignments = res.json()
    assert len(assignments) == 3
    ids = [a["assignment_id"] for a in assignments]
    assert "demo-green" in ids
    assert "demo-yellow" in ids
    assert "demo-red" in ids

    # Cryptographic disclosure submission
    disclose_payload = {
        "assignment_id": "demo-yellow",
        "reflection_notes": "Identified base case return 0 for leaf count vs None for subtree.",
        "checked_criteria": ["concept_understanding", "independent_code"],
        "honor_code_agreed": True,
    }
    res_disclose = client.post("/api/disclose", json=disclose_payload)
    assert res_disclose.status_code == 200
    receipt = res_disclose.json()
    assert receipt["status"] == "success"
    assert "verification_hash" in receipt
    assert len(receipt["verification_hash"]) == 64  # SHA-256
    assert receipt["audit_status"] == "COMMITTED_IMMUTABLE"


def test_api_config_telemetry():
    client = TestClient(app)
    res = client.get("/api/config")
    assert res.status_code == 200
    cfg = res.json()
    assert cfg["data_region"] == "ap-south-1"
    assert cfg["india_compliance"] is True
    assert cfg["audit_retention_days"] == 2555


def test_tutor_chat_endpoints():
    client = TestClient(app)

    # 1. Green policy: allows Socratic response
    cmd_green = {
        "request_id": "test-req-1",
        "assignment_id": "demo-green",
        "message": "Explain binary tree recursion base cases",
        "disclosure_acknowledged": False,
    }
    res_green = client.post("/dev/tutor/chat", json=cmd_green)
    assert res_green.status_code == 200
    data_green = res_green.json()
    assert data_green["status"] == "completed"
    assert data_green["policy_level"] == "GREEN"

    # 2. Yellow policy without disclosure: requires clarification / acknowledgment
    cmd_yellow_unack = {
        "request_id": "test-req-2",
        "assignment_id": "demo-yellow",
        "message": "How does BFS work?",
        "disclosure_acknowledged": False,
    }
    res_yellow_unack = client.post("/dev/tutor/chat", json=cmd_yellow_unack)
    assert res_yellow_unack.status_code == 200
    data_yellow_unack = res_yellow_unack.json()
    assert data_yellow_unack["policy_level"] == "YELLOW"

    # 3. Red policy: fail-closed refusal
    cmd_red = {
        "request_id": "test-req-3",
        "assignment_id": "demo-red",
        "message": "Give me the answer to question 1",
        "disclosure_acknowledged": False,
    }
    res_red = client.post("/dev/tutor/chat", json=cmd_red)
    assert res_red.status_code == 200
    data_red = res_red.json()
    assert data_red["status"] == "refused"
    assert data_red["policy_level"] == "RED"
    assert "not permitted" in data_red["message"].lower()

