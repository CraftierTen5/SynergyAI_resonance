from contextlib import asynccontextmanager
from uuid import UUID
import pymupdf
import pytest

from fastapi.testclient import TestClient

from app.agent.contracts import AssignmentContext
from app.services.policy.document_classifier import DocumentPolicyDecision


def demo_pdf(text="Synthetic assignment text"):
    document = pymupdf.open()
    document.new_page().insert_text((72, 72), text)
    content = document.tobytes()
    document.close()
    return content


def demo_png():
    document = pymupdf.open()
    page = document.new_page(width=360, height=200)
    page.insert_text((24, 48), "Explain the ER model", fontsize=20)
    content = page.get_pixmap(alpha=False).tobytes("png")
    document.close()
    return content


@pytest.fixture(autouse=True)
def skip_database_startup_for_route_unit_tests(monkeypatch):
    import app.main as main

    monkeypatch.setattr(main.settings, "postgres_dsn", None)


class FakeConnection:
    def __init__(self):
        self.assignment_tier = "YELLOW"
        self.active_tier = "YELLOW"
        self.active_instruction = ""
        self.executions = []

    async def fetchrow(self, query, assignment_id):
        assert assignment_id == UUID("00000000-0000-0000-0000-000000000123")
        return {
            "policy_tier": self.assignment_tier,
            "active_policy_tier": self.active_tier,
            "active_system_instruction": self.active_instruction,
        }

    async def execute(self, query, *args):
        self.executions.append((query, args))
        if query.startswith("UPDATE core.assignments SET policy_tier"):
            self.assignment_tier = args[2]
        elif query.startswith("INSERT INTO core.policy_versions"):
            self.active_tier = args[3]
            self.active_instruction = args[4]


class FakeTenantStore:
    def __init__(self):
        self.institution_id = "school-1"
        self.connection = FakeConnection()

    async def fetchrow(self, query, *args):
        assert "core.users" in query
        return {"user_id": args[0]}

    @asynccontextmanager
    async def scoped_connection(self):
        yield self.connection


class FakeAssignmentService:
    def __init__(self, store):
        pass

    async def get_authorized_assignment(self, assignment_id, user):
        return AssignmentContext(
            assignment_id=assignment_id,
            institution_id=user.institution_id,
            title="Assignment",
            policy_level="YELLOW",
            policy_version="v1",
        )


class FakeClassifier:
    decision = DocumentPolicyDecision(
        policy_tier="RED",
        reason="AI use is prohibited.",
        evidence=("Do not use AI for this assignment.",),
        requires_human_review=False,
        confidence=0.99,
    )

    def __init__(self, provider):
        pass

    async def classify(self, extracted_text, *, documents):
        assert extracted_text == "Synthetic assignment text"
        assert documents[0][0] == "application/pdf"
        assert documents[0][1].startswith(b"%PDF-")
        return self.decision


def test_uploaded_no_ai_pdf_is_persisted_as_red_before_tutor_can_run(monkeypatch):
    import app.main as main

    tenant = FakeTenantStore()
    monkeypatch.setattr(main, "_session", lambda authorization: {
        "user_id": "00000000-0000-0000-0000-000000000001",
        "institution_id": "school-1",
    })
    monkeypatch.setattr(main, "_database", lambda request: object())
    monkeypatch.setattr(main, "_store_for", lambda request, session: tenant)
    monkeypatch.setattr(main, "PostgresAssignmentService", FakeAssignmentService)
    monkeypatch.setattr(main, "AssignmentPolicyDocumentClassifier", FakeClassifier)

    response = TestClient(main.app).post(
        "/api/assignments/00000000-0000-0000-0000-000000000123/policy-document",
        headers={"Authorization": "Bearer unit-test-token"},
        files={"document": ("assignment.pdf", demo_pdf(), "application/pdf")},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "blocked"
    assert payload["policy_level"] == "RED"
    assert payload["policy_review_required"] is False
    assert tenant.connection.assignment_tier == "RED"
    assert tenant.connection.active_tier == "RED"
    assert tenant.connection.active_instruction.startswith("POLICY_DOCUMENT_SHA256=")
    assert sum("INSERT INTO core.policy_versions" in query for query, _ in tenant.connection.executions) == 2


def test_policy_document_upload_requires_authentication():
    import app.main as main

    response = TestClient(main.app).post(
        "/api/assignments/00000000-0000-0000-0000-000000000123/policy-document",
        files={"document": ("assignment.pdf", demo_pdf(), "application/pdf")},
    )
    assert response.status_code == 401


def test_policy_provider_failure_defaults_to_yellow_while_review_is_pending(monkeypatch):
    import app.main as main
    from app.services.ai.errors import ProviderUnavailableError

    class UnavailableClassifier(FakeClassifier):
        async def classify(self, extracted_text, *, documents):
            raise ProviderUnavailableError("provider unavailable")

    tenant = FakeTenantStore()
    monkeypatch.setattr(main, "_session", lambda authorization: {
        "user_id": "00000000-0000-0000-0000-000000000001",
        "institution_id": "school-1",
    })
    monkeypatch.setattr(main, "_database", lambda request: object())
    monkeypatch.setattr(main, "_store_for", lambda request, session: tenant)
    monkeypatch.setattr(main, "PostgresAssignmentService", FakeAssignmentService)
    monkeypatch.setattr(main, "AssignmentPolicyDocumentClassifier", UnavailableClassifier)

    response = TestClient(main.app).post(
        "/api/assignments/00000000-0000-0000-0000-000000000123/policy-document",
        headers={"Authorization": "Bearer unit-test-token"},
        files={"document": ("assignment.pdf", demo_pdf(), "application/pdf")},
    )

    assert response.status_code == 200
    assert response.json()["policy_review_required"] is True
    assert response.json()["policy_level"] == "YELLOW"
    assert "allowed by default" in response.json()["message"]
    assert tenant.connection.assignment_tier == "YELLOW"
    assert tenant.connection.active_tier == "YELLOW"
    assert tenant.connection.active_instruction.startswith("POLICY_REVIEW_REQUIRED;")


def test_explicit_no_ai_evidence_overrides_a_permissive_classifier_result():
    import app.main as main

    assert main._evidence_has_explicit_ai_ban(("Do not use AI tools for this assignment.",))
    assert main._evidence_has_explicit_ai_ban(("AI assistance is not permitted.",))
    assert main._evidence_has_explicit_ai_ban((
        "Use of AI tools to generate your solution is not permitted."
    ,))
    assert main._evidence_has_explicit_ai_ban((
        "Students may not use generative AI for this work."
    ,))
    assert not main._evidence_has_explicit_ai_ban(("You may use AI for brainstorming.",))
    assert not main._evidence_has_explicit_ai_ban(("There is no AI restriction for this assignment.",))
    assert not main._evidence_has_explicit_ai_ban(("AI assistance is not required.",))


def test_searchable_pdf_prohibition_is_red_even_if_classifier_is_unavailable(monkeypatch):
    import app.main as main

    class NeverCalledClassifier:
        def __init__(self, provider):
            raise AssertionError("A local explicit prohibition must not depend on Gemini")

    tenant = FakeTenantStore()
    monkeypatch.setattr(main, "_session", lambda authorization: {
        "user_id": "00000000-0000-0000-0000-000000000001",
        "institution_id": "school-1",
    })
    monkeypatch.setattr(main, "_database", lambda request: object())
    monkeypatch.setattr(main, "_store_for", lambda request, session: tenant)
    monkeypatch.setattr(main, "PostgresAssignmentService", FakeAssignmentService)
    monkeypatch.setattr(main, "AssignmentPolicyDocumentClassifier", NeverCalledClassifier)

    response = TestClient(main.app).post(
        "/api/assignments/00000000-0000-0000-0000-000000000123/policy-document",
        headers={"Authorization": "Bearer unit-test-token"},
        files={"document": (
            "assignment.pdf",
            demo_pdf("Use of AI tools to generate your solution is not permitted."),
            "application/pdf",
        )},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "blocked"
    assert payload["policy_level"] == "RED"
    assert payload["policy_review_required"] is False
    assert "not permitted" in payload["evidence"][0].lower()
    assert tenant.connection.assignment_tier == "RED"
    assert tenant.connection.active_tier == "RED"


def test_explicit_ban_evidence_remains_red_even_when_classifier_conflicts(monkeypatch):
    import app.main as main

    class ConflictedClassifier(FakeClassifier):
        decision = DocumentPolicyDecision(
            policy_tier="GREEN",
            reason="Classifier result conflicts with quoted prohibition.",
            evidence=("Do not use AI tools for this assignment.",),
            requires_human_review=False,
            confidence=0.99,
        )

    tenant = FakeTenantStore()
    monkeypatch.setattr(main, "_session", lambda authorization: {
        "user_id": "00000000-0000-0000-0000-000000000001",
        "institution_id": "school-1",
    })
    monkeypatch.setattr(main, "_database", lambda request: object())
    monkeypatch.setattr(main, "_store_for", lambda request, session: tenant)
    monkeypatch.setattr(main, "PostgresAssignmentService", FakeAssignmentService)
    monkeypatch.setattr(main, "AssignmentPolicyDocumentClassifier", ConflictedClassifier)

    response = TestClient(main.app).post(
        "/api/assignments/00000000-0000-0000-0000-000000000123/policy-document",
        headers={"Authorization": "Bearer unit-test-token"},
        files={"document": ("assignment.pdf", demo_pdf(), "application/pdf")},
    )

    assert response.status_code == 200
    assert response.json()["policy_level"] == "RED"
    assert response.json()["policy_review_required"] is False
    assert tenant.connection.assignment_tier == "RED"


def test_explicitly_permissive_document_resolves_default_yellow_to_green(monkeypatch):
    import app.main as main

    class PermissiveClassifier(FakeClassifier):
        decision = DocumentPolicyDecision(
            policy_tier="GREEN",
            reason="Broad AI use is permitted by the document.",
            evidence=("AI may be used for any part of the assignment.",),
            requires_human_review=False,
            confidence=0.99,
            extracted_text="AI may be used for any part of the assignment.",
        )

    tenant = FakeTenantStore()
    monkeypatch.setattr(main, "_session", lambda authorization: {
        "user_id": "00000000-0000-0000-0000-000000000001",
        "institution_id": "school-1",
    })
    monkeypatch.setattr(main, "_database", lambda request: object())
    monkeypatch.setattr(main, "_store_for", lambda request, session: tenant)
    monkeypatch.setattr(main, "PostgresAssignmentService", FakeAssignmentService)
    monkeypatch.setattr(main, "AssignmentPolicyDocumentClassifier", PermissiveClassifier)

    response = TestClient(main.app).post(
        "/api/assignments/00000000-0000-0000-0000-000000000123/policy-document",
        headers={"Authorization": "Bearer unit-test-token"},
        files={"document": ("assignment.pdf", demo_pdf(), "application/pdf")},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "verified"
    assert response.json()["policy_level"] == "GREEN"
    assert tenant.connection.assignment_tier == "GREEN"


def test_uploaded_png_is_ocr_extracted_and_defaults_to_yellow(monkeypatch):
    import app.main as main

    class UnmentionedPolicyClassifier(FakeClassifier):
        decision = DocumentPolicyDecision(
            policy_tier="YELLOW",
            reason="No AI use rule was found in the image.",
            evidence=(),
            requires_human_review=False,
            confidence=0.9,
            extracted_text="Explain the ER model",
        )

        async def classify(self, extracted_text, *, documents):
            assert extracted_text == ""
            assert documents[0][0] == "image/png"
            assert documents[0][1].startswith(b"\x89PNG\r\n\x1a\n")
            return self.decision

    class FakeGeminiAdapter:
        def __init__(self, settings):
            pass

        async def generate_structured(self, prompt, documents, schema):
            raise AssertionError("Reuse the classifier's OCR text instead of making a second call")

    tenant = FakeTenantStore()
    monkeypatch.setattr(main, "_session", lambda authorization: {
        "user_id": "00000000-0000-0000-0000-000000000001",
        "institution_id": "school-1",
    })
    monkeypatch.setattr(main, "_database", lambda request: object())
    monkeypatch.setattr(main, "_store_for", lambda request, session: tenant)
    monkeypatch.setattr(main, "PostgresAssignmentService", FakeAssignmentService)
    monkeypatch.setattr(main, "AssignmentPolicyDocumentClassifier", UnmentionedPolicyClassifier)
    monkeypatch.setattr(main, "GeminiFlashAdapter", FakeGeminiAdapter)

    response = TestClient(main.app).post(
        "/api/assignments/00000000-0000-0000-0000-000000000123/policy-document",
        headers={"Authorization": "Bearer unit-test-token"},
        files={"document": ("assignment.png", demo_png(), "image/png")},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["policy_level"] == "YELLOW"
    assert payload["policy_review_required"] is False
    assert payload["document_readable"] is True
    assert payload["document_context"] == "Explain the ER model"
