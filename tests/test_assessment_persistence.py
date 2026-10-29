from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest

from app.db.access import TenantScopeError
from app.db.postgres import MIGRATIONS_DIR, migration_files
from app.services.assessment.persistence import (
    AssessmentPersistenceFactory,
    AssessmentPersistenceService,
)
from app.db.access import TenantScopedStore
from app.schemas.assessment import (
    DocumentInput, ExtractionInput, FeedbackInput, ModelVersionInput,
)
from app.services.data.export import StudentDataExporter


class CaptureStore:
    def __init__(self, institution_id: str = "school-a") -> None:
        self.institution_id = institution_id
        self.calls = []

    async def execute(self, query: str, *args):
        self.calls.append((query, args))
        return "INSERT 0 1"

    async def fetchrow(self, query: str, *args):
        self.calls.append((query, args))
        if "RETURNING conversation_id" in query:
            return {"conversation_id": args[1]}
        if "RETURNING model_version_id" in query:
            return {"model_version_id": UUID("00000000-0000-0000-0000-000000000001")}
        if "RETURNING message_id" in query:
            return {"message_id": "00000000-0000-0000-0000-000000000001"}
        return {"agent_run_id": "00000000-0000-0000-0000-000000000001"}

    async def fetchval(self, query: str, *args):
        self.calls.append((query, args))
        return UUID("00000000-0000-0000-0000-000000000001")


async def seed_institution(store, institution_id: str) -> None:
    async with store.pool.acquire() as connection:
        async with connection.transaction():
            await connection.execute("SELECT set_config('app.institution_id', $1, true)", institution_id)
            await connection.execute(
                "INSERT INTO core.institutions (institution_id, name) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                institution_id, "Test Institute",
            )

def test_migration_discovery_includes_layer_6a_after_existing_history():
    migrations = migration_files()
    assert migrations[-1].name == "0007_layer_6a_persistence.sql"
    assert [path.name for path in migrations] == sorted(path.name for path in migrations)
    sql = migrations[-1].read_text(encoding="utf-8")
    for name in (
        "core.model_versions", "core.documents", "core.document_extractions",
        "assessment.conversations", "assessment.messages", "assessment.agent_runs",
        "assessment.feedback",
    ):
        assert f"CREATE TABLE {name}" in sql
    assert "FORCE ROW LEVEL SECURITY" in sql
    assert "CREATE POLICY tenant_isolation" in sql


def test_migration_does_not_change_prior_migrations():
    assert Path(MIGRATIONS_DIR, "0001_schemas_and_roles.sql").exists()
    assert Path(MIGRATIONS_DIR, "0006_analytics_schema.sql").exists()


@pytest.mark.asyncio
async def test_persistence_validates_roles_and_content_before_sql():
    store = CaptureStore()
    service = AssessmentPersistenceService(store)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        await service.append_message("chat-1", role="system", content="secret prompt", idempotency_key="1")
    with pytest.raises(ValueError):
        await service.ensure_conversation("chat-1", policy_version="v1", policy_tier="RED")
    assert store.calls == []


@pytest.mark.asyncio
async def test_persistence_uses_tenant_bound_parameterized_queries():
    store = CaptureStore("school-a")
    service = AssessmentPersistenceService(store)  # type: ignore[arg-type]
    await service.ensure_conversation("chat-1", policy_version="v1", policy_tier="green")
    await service.append_message("chat-1", role="user", content="Question", idempotency_key="req:user")
    assert store.calls[0][1] == ("school-a", "chat-1", None, "v1", "GREEN", None)
    assert store.calls[1][1] == ("school-a", "chat-1", "req:user", "user", "Question")


def test_tenant_scope_rejects_missing_institution():
    with pytest.raises(TenantScopeError):
        from app.db.access import validate_institution_id
        validate_institution_id(None)


def test_persistence_factory_builds_a_fixed_tenant_scope():
    factory = AssessmentPersistenceFactory(CaptureStore())  # type: ignore[arg-type]
    service = factory.for_institution("school-b")
    assert service.store.institution_id == "school-b"


def test_document_and_feedback_inputs_reject_unsafe_values():
    with pytest.raises(ValueError):
        DocumentInput(
            display_name="assignment.pdf", mime_type="application/pdf", size_bytes=12,
            storage_key="https://public.example/file.pdf",
        )
    with pytest.raises(ValueError):
        FeedbackInput(conversation_id="c1", feedback_type="rating", rating=6, message_id="00000000-0000-0000-0000-000000000001")


def test_feedback_requires_target_and_consistent_rating():
    with pytest.raises(ValueError):
        FeedbackInput(conversation_id="c1", feedback_type="helpful")
    with pytest.raises(ValueError):
        FeedbackInput(
            conversation_id="c1", feedback_type="helpful", rating=4,
            message_id="00000000-0000-0000-0000-000000000001",
        )


@pytest.mark.asyncio
async def test_layer_6a_records_use_tenant_scoped_persistence_methods():
    store = CaptureStore("school-a")
    service = AssessmentPersistenceService(store)  # type: ignore[arg-type]
    model_id = await service.register_model_version(
        ModelVersionInput(model_name="groq", version="llama-test", metadata={"family": "llama"})
    )
    document_id = await service.create_document(
        DocumentInput(
            display_name="work.pdf", mime_type="application/pdf", size_bytes=123,
            storage_key="school-a/documents/item-1",
        )
    )
    await service.record_extraction(
        ExtractionInput(document_id=document_id, status="completed", extracted_text="Extracted")
    )
    await service.record_feedback(
        FeedbackInput(
            conversation_id="chat-1", message_id="00000000-0000-0000-0000-000000000001",
            feedback_type="helpful",
        )
    )
    assert model_id == document_id
    assert len(store.calls) == 4
    assert all(args[0] == "school-a" for _, args in store.calls)
    assert all("$1" in query for query, _ in store.calls)


@pytest.mark.asyncio
async def test_postgres_conversation_persistence_and_rls(clean_db, pg_app):
    import asyncpg

    await seed_institution(clean_db, "school-a")
    await seed_institution(clean_db, "school-b")
    service_a = AssessmentPersistenceService(TenantScopedStore(pg_app, "school-a"))
    pseudonym = "stu_" + "a" * 32
    await service_a.ensure_conversation(
        "chat-a", policy_version="v1", policy_tier="green",
        actor_pseudonymous_id=pseudonym,
    )
    await service_a.append_message("chat-a", role="user", content="Question", idempotency_key="r1:user")
    rows = await TenantScopedStore(pg_app, "school-a").fetch(
        "SELECT role, content FROM assessment.messages WHERE institution_id = $1 AND conversation_id = $2",
        "school-a", "chat-a",
    )
    assert [(row["role"], row["content"]) for row in rows] == [("user", "Question")]
    export = await StudentDataExporter(TenantScopedStore(pg_app, "school-a")).export(pseudonym)
    assert len(export.conversations) == 1
    assert [row["content"] for row in export.messages] == ["Question"]
    assert await TenantScopedStore(pg_app, "school-b").fetch(
        "SELECT * FROM assessment.conversations WHERE institution_id = $1", "school-b"
    ) == []

    async with clean_db.pool.acquire() as connection:
        async with connection.transaction():
            await connection.execute("SELECT set_config('app.institution_id', 'school-b', true)")
            await connection.execute(
                "INSERT INTO assessment.conversations (institution_id, conversation_id, policy_version, policy_tier) VALUES ('school-b', 'chat-b', 'v1', 'GREEN')"
            )
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await TenantScopedStore(pg_app, "school-a").execute(
            "INSERT INTO assessment.messages (institution_id, conversation_id, role, content) VALUES ($1, $2, 'user', 'bad')",
            "school-a", "chat-b",
        )
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await TenantScopedStore(pg_app, "school-a").fetch("SELECT * FROM identity.pseudonym_map")
