"""Tenant scoped durable persistence for AI conversations and run metadata."""

from __future__ import annotations

import json
from typing import Literal
from uuid import UUID

from app.db.access import TenantScopedStore
from app.db.postgres import PostgresStore
from app.schemas.assessment import (
    DocumentInput,
    ExtractionInput,
    FeedbackInput,
    ModelVersionInput,
)


class AssessmentPersistenceService:
    """Persist approved chat content and safe run metadata in PostgreSQL."""

    def __init__(self, store: TenantScopedStore) -> None:
        self.store = store

    async def ensure_conversation(
        self, conversation_id: str, *, policy_version: str, policy_tier: str,
        actor_pseudonymous_id: str | None = None, assignment_id: UUID | None = None,
    ) -> None:
        tier = policy_tier.upper()
        if tier not in {"YELLOW", "GREEN"}:
            raise ValueError("only policy-approved YELLOW or GREEN conversations can be persisted")
        row = await self.store.fetchrow(
            """INSERT INTO assessment.conversations
               (institution_id, conversation_id, assignment_id, policy_version, policy_tier,
                actor_pseudonymous_id)
               VALUES ($1, $2, $3, $4, $5, $6)
               ON CONFLICT (institution_id, conversation_id) DO UPDATE
               SET updated_at = now(),
                   assignment_id = COALESCE(assessment.conversations.assignment_id,
                                            EXCLUDED.assignment_id),
                   actor_pseudonymous_id = COALESCE(assessment.conversations.actor_pseudonymous_id,
                                                    EXCLUDED.actor_pseudonymous_id)
               WHERE assessment.conversations.status = 'active'
                 AND (assessment.conversations.assignment_id IS NULL
                      OR EXCLUDED.assignment_id IS NULL
                      OR assessment.conversations.assignment_id = EXCLUDED.assignment_id)
                 AND (assessment.conversations.actor_pseudonymous_id IS NULL
                      OR EXCLUDED.actor_pseudonymous_id IS NULL
                      OR assessment.conversations.actor_pseudonymous_id = EXCLUDED.actor_pseudonymous_id)
               RETURNING conversation_id""",
            self.store.institution_id, conversation_id, assignment_id, policy_version, tier,
            actor_pseudonymous_id,
        )
        if row is None:
            raise ValueError("conversation is inactive or belongs to another pseudonymous actor")

    async def register_model_version(self, record: ModelVersionInput) -> UUID:
        row = await self.store.fetchrow(
            """INSERT INTO core.model_versions
               (institution_id, model_name, version, registry_ref, artifact_sha256, status, metadata)
               VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)
               ON CONFLICT (institution_id, model_name, version) DO UPDATE
               SET registry_ref = EXCLUDED.registry_ref,
                   artifact_sha256 = EXCLUDED.artifact_sha256,
                   status = EXCLUDED.status, metadata = EXCLUDED.metadata
               RETURNING model_version_id""",
            self.store.institution_id, record.model_name, record.version, record.registry_ref,
            record.artifact_sha256, record.status, json.dumps(record.metadata),
        )
        return row["model_version_id"]

    async def find_model_version_id(self, model_name: str, version: str) -> UUID | None:
        return await self.store.fetchval(
            """SELECT model_version_id FROM core.model_versions
               WHERE institution_id = $1 AND model_name = $2 AND version = $3""",
            self.store.institution_id, model_name, version,
        )

    async def create_document(self, record: DocumentInput) -> UUID:
        return await self.store.fetchval(
            """INSERT INTO core.documents
               (institution_id, assignment_id, uploader_pseudonymous_id, display_name,
                mime_type, size_bytes, storage_key, content_sha256, status, expires_at)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
               RETURNING document_id""",
            self.store.institution_id, record.assignment_id, record.uploader_pseudonymous_id,
            record.display_name, record.mime_type, record.size_bytes, record.storage_key,
            record.content_sha256, record.status, record.expires_at,
        )

    async def update_document_status(
        self, document_id: UUID, status: Literal["pending", "processing", "ready", "failed", "expired"]
    ) -> bool:
        result = await self.store.execute(
            "UPDATE core.documents SET status = $3, updated_at = now() WHERE institution_id = $1 AND document_id = $2",
            self.store.institution_id, document_id, status,
        )
        return result.endswith(" 1")

    async def record_extraction(self, record: ExtractionInput) -> UUID:
        return await self.store.fetchval(
            """INSERT INTO core.document_extractions
               (institution_id, document_id, status, extractor_version, extracted_text,
                metadata, expires_at)
               VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7)
               RETURNING extraction_id""",
            self.store.institution_id, record.document_id, record.status, record.extractor_version,
            record.extracted_text, json.dumps(record.metadata), record.expires_at,
        )

    async def update_extraction_status(
        self, extraction_id: UUID,
        status: Literal["pending", "processing", "completed", "failed", "expired"],
    ) -> bool:
        result = await self.store.execute(
            "UPDATE core.document_extractions SET status = $3, updated_at = now() WHERE institution_id = $1 AND extraction_id = $2",
            self.store.institution_id, extraction_id, status,
        )
        return result.endswith(" 1")

    async def record_feedback(self, record: FeedbackInput) -> UUID:
        return await self.store.fetchval(
            """INSERT INTO assessment.feedback
               (institution_id, conversation_id, message_id, agent_run_id,
                actor_pseudonymous_id, feedback_type, rating, comment)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
               RETURNING feedback_id""",
            self.store.institution_id, record.conversation_id, record.message_id,
            record.agent_run_id, record.actor_pseudonymous_id, record.feedback_type,
            record.rating, record.comment,
        )

    async def append_message(
        self, conversation_id: str, *, role: str, content: str, idempotency_key: str
    ) -> None:
        if role not in {"user", "assistant"}:
            raise ValueError("only user and assistant messages may be persisted")
        if not content or len(content) > 32_000:
            raise ValueError("message content must contain 1 to 32000 characters")
        row = await self.store.fetchrow(
            """INSERT INTO assessment.messages
               (institution_id, conversation_id, idempotency_key, role, content)
               VALUES ($1, $2, $3, $4, $5)
               ON CONFLICT (institution_id, conversation_id, idempotency_key)
               WHERE idempotency_key IS NOT NULL DO UPDATE
               SET content = assessment.messages.content
               WHERE assessment.messages.role = EXCLUDED.role
                 AND assessment.messages.content = EXCLUDED.content
               RETURNING message_id""",
            self.store.institution_id, conversation_id, idempotency_key, role, content,
        )
        if row is None:
            raise ValueError("idempotency key was previously used for different message content")

    async def start_run(
        self, conversation_id: str, *, request_id: str, policy_version: str,
        policy_tier: str, provider: str, model_name: str,
        model_version_id: UUID | None = None,
    ) -> UUID:
        row = await self.store.fetchrow(
            """INSERT INTO assessment.agent_runs
               (institution_id, conversation_id, request_id, policy_version, policy_tier,
                provider, model_name, model_version_id)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
               ON CONFLICT (institution_id, request_id) DO NOTHING
               RETURNING agent_run_id""",
            self.store.institution_id, conversation_id, request_id, policy_version,
            policy_tier.upper(), provider, model_name, model_version_id,
        )
        if row is None:
            raise ValueError("request_id has already been used for an agent run")
        return row["agent_run_id"]

    async def finish_run(
        self, run_id: UUID, *, input_tokens: int | None = None,
        output_tokens: int | None = None, result_sha256: str | None = None,
        error_category: str | None = None, provider: str | None = None,
        model_name: str | None = None, model_version_id: UUID | None = None,
        update_model_version: bool = False,
    ) -> None:
        status = "failed" if error_category else "completed"
        await self.store.execute(
            """UPDATE assessment.agent_runs SET status = $3, completed_at = now(),
               input_tokens = $4, output_tokens = $5, result_sha256 = $6, error_category = $7,
               provider = COALESCE($8, provider), model_name = COALESCE($9, model_name),
               model_version_id = CASE WHEN $11 THEN $10 ELSE model_version_id END
               WHERE institution_id = $1 AND agent_run_id = $2 AND status = 'running'""",
            self.store.institution_id, run_id, status, input_tokens, output_tokens,
            result_sha256, error_category, provider, model_name, model_version_id,
            update_model_version,
        )


class AssessmentPersistenceFactory:
    """Create request-scoped persistence services from the shared pool."""

    def __init__(self, store: PostgresStore) -> None:
        self.store = store

    def for_institution(self, institution_id: str) -> AssessmentPersistenceService:
        return AssessmentPersistenceService(TenantScopedStore(self.store, institution_id))
