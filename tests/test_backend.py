from fastapi.testclient import TestClient

from app import main
from app.main import app
from app.policy import evaluate_policy


client = TestClient(app)


def setup_function():
    main._dev_assignments.clear()
    main._dev_logs.clear()
    main.app.state.guidance_service = None
    main.app.state.process_log_reader = None
    main.app.state.viva_generator = None


def test_root_health_and_ready():
    assert client.get("/").status_code == 200
    assert client.get("/health").json() == {"status": "healthy"}
    assert client.get("/ready").json()["database"] == "not_configured"
    assert client.get("/docs").status_code == 200


def test_policy_levels_and_normalization():
    assert evaluate_policy(" red ")["assistance_mode"] == "refuse"
    assert evaluate_policy("YELLOW")["assistance_mode"] == "socratic"
    assert evaluate_policy("green")["assistance_mode"] == "full"
    assert evaluate_policy(" PROCESS_DISCLOSURE ")["policy_level"] == "YELLOW"
    assert evaluate_policy("NO_AI")["log_required"] is True
    assert evaluate_policy("NO_AI")["process_logging_required"] is False
    assert evaluate_policy("NO_AI")["policy_audit_recorded"] is False
    assert evaluate_policy("GREEN")["process_logging_required"] is False
    assert client.get("/policies").status_code == 200
    assert client.post("/policies/check", json={"policy_level": "  yellow "}).json()["policy_level"] == "YELLOW"
    assert client.post("/policies/check", json={"policy_level": "blue"}).status_code == 422
    created = client.post("/assignments", json={"title": "Wire-level policy", "policy_level": "PROCESS_DISCLOSURE"})
    assert created.status_code == 201
    assert created.json()["policy_level"] == "YELLOW"


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
    stored_log = client.get("/process-logs").json()["logs"][0]
    assert "message" not in stored_log and "response" not in stored_log
    assert len(stored_log["message_sha256"]) == 64


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


def test_provider_factory_supports_orchestrate_provider_names(monkeypatch):
    from app import tutor
    monkeypatch.setattr(tutor.settings, "groq_api_key", "test-groq-key")
    monkeypatch.setattr(tutor.settings, "gemini_api_key", "test-gemini-key")
    assert tutor.configured_provider("groq").provider_name == "groq"
    assert tutor.configured_provider("gemini").provider_name == "gemini"
    assert tutor.configured_provider("unknown") is None


def test_sample_csv_validation():
    from scripts.validate_sample_csv import validate
    assert validate() == 3


def test_orchestrate_compatible_stream_uses_server_policy_and_stream_service():
    class FakeChunk:
        def model_dump_json(self):
            return '{"delta":"Guided hint","provider":"groq"}'

    class FakeGuidance:
        request = None

        async def generate_multilingual_guidance(self, request, provider):
            self.request = request
            assert provider == "groq"
            yield FakeChunk()

    assignment = client.post("/assignments", json={
        "title": "Policy essay", "policy_level": "YELLOW", "institution_id": "school-1"
    }).json()
    service = FakeGuidance()
    main.install_orchestration_services(guidance_service=service)
    response = client.post("/tutor/chat/stream", json={
        "assignment_id": assignment["id"], "message": "Help me reason",
        "language": "hinglish", "session_id": "conversation-1", "provider": "groq",
        "disclosure_acknowledged": True,
    })
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "Guided hint" in response.text
    assert service.request.policy_tier == "yellow"
    assert service.request.instruction_profile == "guided"
    assert "Socratic" in service.request.system_instruction


def test_stream_rejects_client_supplied_policy_claims_and_red_never_calls_service():
    assignment = client.post("/assignments", json={
        "title": "Exam", "policy_level": "RED", "institution_id": "school-1"
    }).json()
    response = client.post("/tutor/chat/stream", json={
        "assignment_id": assignment["id"], "message": "solve this", "session_id": "c1",
        "provider": "groq", "policy_tier": "green",
    })
    assert response.status_code == 422

    class NeverCalled:
        async def generate_multilingual_guidance(self, *args):
            raise AssertionError("RED policy must stop before the AI service")

    main.app.state.guidance_service = NeverCalled()
    response = client.post("/tutor/chat/stream", json={
        "assignment_id": assignment["id"], "message": "solve this", "session_id": "c1",
        "provider": "groq",
    })
    assert response.status_code == 403


def test_stream_service_requires_institution_scope_and_explicit_disclosure():
    item = client.post("/assignments", json={"title": "Missing institution", "policy_level": "YELLOW"}).json()
    response = client.post("/tutor/chat/stream", json={
        "assignment_id": item["id"], "message": "help", "session_id": "c1", "provider": "groq",
    })
    assert response.status_code == 400
    body = {"assignment_id": item["id"], "message": "help", "session_id": "c1",
            "provider": "groq", "disclosure_acknowledged": True}
    assert client.post("/tutor/chat/stream", json=body).status_code == 503


def test_sensitive_demo_routes_are_disabled_outside_development(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "environment", "production")
    assert client.post("/assignments", json={"title": "Denied", "policy_level": "GREEN"}).status_code == 503
    assert client.get("/process-logs").status_code == 503


def test_evidence_viva_uses_authorized_server_logs_not_request_body():
    assignment = client.post("/assignments", json={
        "title": "Portfolio", "policy_level": "GREEN", "institution_id": "school-1"
    }).json()
    logs = [{"institution_id": "school-1", "action": "revision", "pseudonymous_id": "stu_abc"}]

    async def read_logs(request, assignment_id):
        assert assignment_id == assignment["id"]
        if request.headers.get("x-role") != "faculty":
            raise PermissionError
        return logs

    class Generator:
        async def generate_viva_questions(self, entries):
            assert entries == logs
            return [{"question": "Why did you make this design choice?", "focus_area": "design_choice"}]

    main.app.state.process_log_reader = read_logs
    main.app.state.viva_generator = Generator()
    denied = client.post("/viva/generate/from-process-logs", json={"assignment_id": assignment["id"]})
    assert denied.status_code == 403
    result = client.post("/viva/generate/from-process-logs", headers={"x-role": "faculty"},
                         json={"assignment_id": assignment["id"], "process_logs": [{"fake": "data"}]})
    assert result.status_code == 422
    result = client.post("/viva/generate/from-process-logs", headers={"x-role": "faculty"},
                         json={"assignment_id": assignment["id"]})
    assert result.status_code == 200
    assert result.json()["review_required"] is True


def test_evidence_viva_blocks_cross_institution_logs():
    assignment = client.post("/assignments", json={
        "title": "Portfolio", "policy_level": "GREEN", "institution_id": "school-1"
    }).json()

    async def read_logs(request, assignment_id):
        return [{"institution_id": "school-2", "action": "revision"}]

    class Generator:
        async def generate_viva_questions(self, entries):
            raise AssertionError("cross-institution logs must not reach the generator")

    main.app.state.process_log_reader = read_logs
    main.app.state.viva_generator = Generator()
    response = client.post("/viva/generate/from-process-logs", json={"assignment_id": assignment["id"]})
    assert response.status_code == 403
