from fastapi.testclient import TestClient

from app import main
from app.main import app
from app.policy import evaluate_policy


client = TestClient(app)


def setup_function():
    main._dev_assignments.clear()
    main._dev_logs.clear()


def test_root_health_and_ready():
    assert client.get("/").status_code == 200
    assert client.get("/health").json() == {"status": "healthy"}
    assert client.get("/ready").json()["database"] == "not_configured"


def test_policy_levels_and_normalization():
    assert evaluate_policy(" red ")["assistance_mode"] == "refuse"
    assert evaluate_policy("YELLOW")["assistance_mode"] == "socratic"
    assert evaluate_policy("green")["assistance_mode"] == "full"
    assert client.get("/policies").status_code == 200
    assert client.post("/policies/check", json={"policy_level": "  yellow "}).json()["policy_level"] == "YELLOW"
    assert client.post("/policies/check", json={"policy_level": "blue"}).status_code == 422


def test_assignment_crud_is_explicitly_volatile_in_development():
    response = client.post("/assignments", json={"title": "Essay", "policy_level": "YELLOW"})
    assert response.status_code == 201
    assignment = response.json()
    assert assignment["storage"] == "volatile_development"
    assert client.get("/assignments/" + assignment["id"]).status_code == 200
    assert client.patch("/assignments/" + assignment["id"] + "/policy", json={"policy_level": "GREEN"}).json()["policy_level"] == "GREEN"
    assert client.post("/assignments", json={"title": "bad", "policy_level": "BLUE"}).status_code == 422


def test_red_tutor_never_calls_provider(monkeypatch):
    item = client.post("/assignments", json={"title": "Exam", "policy_level": "RED"}).json()
    def forbidden():
        raise AssertionError("provider must not be configured or called")
    monkeypatch.setattr(main, "configured_provider", forbidden)
    result = client.post("/tutor/chat", json={"assignment_id": item["id"], "message": "solve it", "language": "hindi"})
    assert result.status_code == 200
    assert result.json()["assistance_mode"] == "refuse"


def test_yellow_uses_socratic_and_requires_disclosure(monkeypatch):
    item = client.post("/assignments", json={"title": "Essay", "policy_level": "YELLOW"}).json()
    class FakeProvider:
        def generate(self, message, mode, language, context=""):
            assert mode == "socratic"
            assert language == "hinglish"
            return "What have you tried?"
    monkeypatch.setattr(main, "configured_provider", lambda: FakeProvider())
    body = {"assignment_id": item["id"], "message": "help", "language": "hinglish"}
    assert client.post("/tutor/chat", json=body).status_code == 400
    body["disclosure_acknowledged"] = True
    result = client.post("/tutor/chat", json=body)
    assert result.status_code == 200
    assert result.json()["assistance_mode"] == "socratic"
    assert result.json()["process_log"] == "recorded_in_memory"


def test_green_full_assistance_and_missing_provider(monkeypatch):
    item = client.post("/assignments", json={"title": "Project", "policy_level": "GREEN"}).json()
    monkeypatch.setattr(main, "configured_provider", lambda: None)
    response = client.post("/tutor/chat", json={"assignment_id": item["id"], "message": "Explain"})
    assert response.status_code == 503
    class FakeProvider:
        def generate(self, message, mode, language, context=""):
            assert mode == "full"
            return "Explanation"
    monkeypatch.setattr(main, "configured_provider", lambda: FakeProvider())
    assert client.post("/tutor/chat", json={"assignment_id": item["id"], "message": "Explain"}).json()["assistance_mode"] == "full"


def test_provider_failure_is_controlled(monkeypatch):
    from app.tutor import ProviderFailure
    item = client.post("/assignments", json={"title": "Project", "policy_level": "GREEN"}).json()
    class BrokenProvider:
        def generate(self, *args, **kwargs):
            raise ProviderFailure("LLM provider request failed")
    monkeypatch.setattr(main, "configured_provider", lambda: BrokenProvider())
    assert client.post("/tutor/chat", json={"assignment_id": item["id"], "message": "Explain"}).status_code == 502


def test_sample_csv_validation():
    from scripts.validate_sample_csv import validate
    assert validate() == 3
