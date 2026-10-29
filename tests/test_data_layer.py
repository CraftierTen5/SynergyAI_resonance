"""Data-layer tests: tenant isolation, privacy, auditability, cache and retention.

These tests run against real PostgreSQL and real Redis. They are skipped when
either service is unavailable, so the wider suite still runs anywhere.
"""
from __future__ import annotations
import os

import asyncio
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from app.db.access import TenantScopeError, TenantScopedStore, validate_institution_id
from app.db.audit import GENESIS_HASH, AuditStore
from app.services.assessment.anonymization import ProcessLogAnonymizer
from app.services.data.analytics import AnalyticsStore, CohortTooSmallError, Measure
from app.services.data.policy_cache import CachedPolicy, PolicyCache
from app.services.data.process_log import ProcessLogBuffer, ProcessLogPersistenceService
from app.services.data.export import StudentDataExporter
from app.services.data.rate_limit import RateLimiter
from app.services.data.retention import RetentionJob, RetentionPolicy
from app.services.data.sessions import SessionStore
from app.services.data.stream import StepEventStream

pytestmark = pytest.mark.data_layer

PSEUDONYM_KEY = "test-pseudonym-key-that-is-at-least-32-bytes-long"


async def seed_institution(store, institution_id: str, name: str = "Test Institute") -> None:
    """Insert an institution as the migrator, scoping the RLS policy explicitly."""
    async with store.pool.acquire() as connection:
        async with connection.transaction():
            await connection.execute(
                "SELECT set_config('app.institution_id', $1, true)", institution_id
            )
            await connection.execute(
                "INSERT INTO core.institutions (institution_id, name) VALUES ($1, $2) "
                "ON CONFLICT DO NOTHING",
                institution_id,
                name,
            )


async def seed_course_and_assignment(store, institution_id: str) -> tuple[str, str]:
    async with store.pool.acquire() as connection:
        async with connection.transaction():
            await connection.execute(
                "SELECT set_config('app.institution_id', $1, true)", institution_id
            )
            course_id = await connection.fetchval(
                "INSERT INTO core.courses (institution_id, code, title, academic_term) "
                "VALUES ($1, $2, $3, $4) RETURNING course_id",
                institution_id,
                "CS101",
                "Intro to Computing",
                "2026-S1",
            )
            assignment_id = await connection.fetchval(
                "INSERT INTO core.assignments (institution_id, course_id, title, policy_tier) "
                "VALUES ($1, $2, $3, 'GREEN') RETURNING assignment_id",
                institution_id,
                course_id,
                "Assignment 1",
            )
    return str(course_id), str(assignment_id)


# --------------------------------------------------------------------------- #
# Tenant separation
# --------------------------------------------------------------------------- #


def test_institution_id_is_validated_before_any_query():
    for bad in (None, "", "  ", "a" * 129, "school/1", "school;drop"):
        with pytest.raises(TenantScopeError):
            validate_institution_id(bad)
    assert validate_institution_id("school-1") == "school-1"


async def test_row_level_security_blocks_cross_tenant_reads(clean_db, pg_app):
    await seed_institution(clean_db, "school-a", "A")
    await seed_institution(clean_db, "school-b", "B")
    await seed_course_and_assignment(clean_db, "school-a")

    scope_a = TenantScopedStore(pg_app, "school-a")
    scope_b = TenantScopedStore(pg_app, "school-b")

    assert await scope_a.fetchval("SELECT count(*) FROM core.courses") == 1
    # Same query, different tenant: nothing leaks.
    assert await scope_b.fetchval("SELECT count(*) FROM core.courses") == 0


async def test_query_without_tenant_scope_returns_nothing(pg_app, clean_db):
    await seed_institution(clean_db, "school-a")
    await seed_course_and_assignment(clean_db, "school-a")
    # A raw connection that never sets app.institution_id must fail safe.
    async with pg_app.pool.acquire() as connection:
        assert await connection.fetchval("SELECT count(*) FROM core.courses") == 0


async def test_writes_for_another_tenant_are_rejected(clean_db, pg_app):
    await seed_institution(clean_db, "school-a")
    await seed_institution(clean_db, "school-b")
    course_id, _ = await seed_course_and_assignment(clean_db, "school-a")
    scope_b = TenantScopedStore(pg_app, "school-b")
    with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
        # WITH CHECK fails because institution_id does not match the scope.
        await scope_b.execute(
            "INSERT INTO core.courses (institution_id, code, title, academic_term) "
            "VALUES ('school-a', 'X', 'Y', '2026-S1')"
        )


async def test_application_role_cannot_reach_identity_schema(pg_app, clean_db):
    await seed_institution(clean_db, "school-a")
    scope = TenantScopedStore(pg_app, "school-a")
    with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
        await scope.fetch("SELECT * FROM identity.pseudonym_map")


# --------------------------------------------------------------------------- #
# Privacy before persistence
# --------------------------------------------------------------------------- #


async def test_raw_process_events_are_buffered_with_a_ttl(redis_store, settings):
    buffer = ProcessLogBuffer(redis_store, settings)
    await buffer.append(
        "school-a", "assignment-1", "stu_" + "a" * 32, {"prompt": "help me"}, ttl_seconds=60
    )
    ttl = await buffer.time_to_live("school-a", "assignment-1", "stu_" + "a" * 32)
    assert 0 < ttl <= 60
    events = await buffer.read("school-a", "assignment-1", "stu_" + "a" * 32)
    assert events == [{"prompt": "help me"}]


async def test_buffered_raw_events_expire_and_are_never_persisted_verbatim(
    clean_db, pg_app, redis_store, settings
):
    await seed_institution(clean_db, "school-a")
    _, assignment_id = await seed_course_and_assignment(clean_db, "school-a")
    pseudonym = "stu_" + "b" * 32

    buffer = ProcessLogBuffer(redis_store, settings)
    await buffer.append(
        "school-a",
        assignment_id,
        pseudonym,
        {
            "institution_id": "school-a",
            "student_id": "REAL-STUDENT-42",
            "email": "priya.sharma@example.edu",
            "name": "Priya Sharma",
            "phone": "+91 98765 43210",
            "prompt": "How do I fix my loop?",
            "response_summary": "Asked about iteration bounds.",
            "event_type": "hint_request",
            "device_class": "mobile",
            "occurred_at": datetime.now(UTC).isoformat(),
        },
    )

    anonymizer = ProcessLogAnonymizer(PSEUDONYM_KEY)
    store = TenantScopedStore(pg_app, "school-a")
    service = ProcessLogPersistenceService(buffer, anonymizer, store)
    result = await service.persist("school-a", assignment_id, pseudonym)
    assert result.written == 1


    row = await store.fetchrow("SELECT * FROM assessment.process_logs")
    assert row is not None
    blob = " ".join(str(value) for value in dict(row).values())
    assert "Priya" not in blob
    assert "@" not in blob
    assert "98765" not in blob
    assert "REAL-STUDENT-42" not in blob
    assert "mobile" not in blob
    assert row["pseudonymous_id"] == anonymizer.anonymize(
        {"institution_id": "school-a", "student_id": "REAL-STUDENT-42"}
    )["pseudonymous_id"]

    # The raw buffer is gone: the durable row is the only survivor.
    assert await buffer.read("school-a", assignment_id, pseudonym) == []


async def test_process_log_rejects_email_smuggled_into_summary(clean_db, pg_app):
    from app.schemas.data import ProcessLogRecord

    with pytest.raises(Exception):
        ProcessLogRecord(
            institution_id="school-a",
            assignment_id="a",
            pseudonymous_id="stu_" + "c" * 32,
            step_number=1,
            event_type="step",
            summary="contact me at student@example.edu",
            occurred_at=datetime.now(UTC),
        )


# --------------------------------------------------------------------------- #
# Append-only, tamper-evident audit
# --------------------------------------------------------------------------- #


async def test_audit_chain_verifies_and_links_to_previous(clean_db, pg_app):
    await seed_institution(clean_db, "school-a")
    audit = AuditStore(TenantScopedStore(pg_app, "school-a"))
    first = await audit.append(event_type="access", reason="login")
    second = await audit.append(event_type="policy_decision", policy_level="GREEN", allowed=True)
    assert first.previous_event_hash == GENESIS_HASH
    assert second.previous_event_hash == first.event_hash
    result = await audit.verify()
    assert result.verified is True
    assert result.events_checked == 2


async def test_audit_chain_is_isolated_per_institution(clean_db, pg_app):
    await seed_institution(clean_db, "school-a")
    await seed_institution(clean_db, "school-b")
    audit_a = AuditStore(TenantScopedStore(pg_app, "school-a"))
    audit_b = AuditStore(TenantScopedStore(pg_app, "school-b"))
    await audit_a.append(event_type="access")
    head_b = await audit_b.append(event_type="access")
    # B's first event must chain from genesis, not from A's head.
    assert head_b.previous_event_hash == GENESIS_HASH
    assert (await audit_a.verify()).events_checked == 1
    assert (await audit_b.verify()).events_checked == 1


async def test_application_role_cannot_mutate_audit_events(clean_db, pg_app):
    await seed_institution(clean_db, "school-a")
    audit = AuditStore(TenantScopedStore(pg_app, "school-a"))
    await audit.append(event_type="access", reason="original")

    # The application role has no UPDATE or DELETE privilege at all.
    async with pg_app.pool.acquire() as connection:
        with pytest.raises(asyncpg.exceptions.PostgresError):
            await connection.execute("UPDATE audit.audit_events SET reason = 'edited'")
        with pytest.raises(asyncpg.exceptions.PostgresError):
            await connection.execute("DELETE FROM audit.audit_events")


async def test_audit_trigger_rejects_mutation_even_for_a_privileged_role(clean_db):
    """A role that can write must still not be able to rewrite history."""
    await seed_institution(clean_db, "school-a")
    audit = AuditStore(TenantScopedStore(clean_db, "school-a"))
    await audit.append(event_type="access", reason="original")

    # Each attempt runs in its own transaction: the first failure aborts its
    # transaction, so a second statement there could not be evaluated.
    async with clean_db.pool.acquire() as connection:
        with pytest.raises(asyncpg.exceptions.PostgresError):
            async with connection.transaction():
                await connection.execute(
                    "SELECT set_config('app.institution_id', 'school-a', true)"
                )
                await connection.execute(
                    "UPDATE audit.audit_events SET reason = 'edited'"
                )
        with pytest.raises(asyncpg.exceptions.PostgresError):
            async with connection.transaction():
                await connection.execute(
                    "SELECT set_config('app.institution_id', 'school-a', true)"
                )
                await connection.execute("DELETE FROM audit.audit_events")


async def test_audit_tampering_is_detected_by_chain_verification(clean_db, pg_app):
    await seed_institution(clean_db, "school-a")
    audit = AuditStore(TenantScopedStore(pg_app, "school-a"))
    await audit.append(event_type="access", reason="first")
    await audit.append(event_type="policy_decision", reason="second")
    assert (await audit.verify()).verified is True

    # Simulate an operator who disables the trigger and rewrites a row. The
    # chain must still expose the edit.
    async with clean_db.pool.acquire() as connection:
        async with connection.transaction():
            await connection.execute(
                "SELECT set_config('app.institution_id', 'school-a', true)"
            )
            await connection.execute(
                "ALTER TABLE audit.audit_events DISABLE TRIGGER audit_events_no_update"
            )
            await connection.execute(
                "UPDATE audit.audit_events SET reason = 'rewritten' WHERE event_type = 'access'"
            )
            await connection.execute(
                "ALTER TABLE audit.audit_events ENABLE TRIGGER audit_events_no_update"
            )

    result = await audit.verify()
    assert result.verified is False
    assert result.first_bad_event_id == 1


# --------------------------------------------------------------------------- #
# Cache and stream store
# --------------------------------------------------------------------------- #


async def test_policy_cache_read_through_and_invalidation(redis_store, settings):
    cache = PolicyCache(redis_store, settings)
    loads = {"count": 0}

    async def loader(institution_id, assignment_id):
        loads["count"] += 1
        return CachedPolicy(
            assignment_id=assignment_id,
            policy_version="v1",
            policy_tier="GREEN",
            instruction_profile="direct",
            system_instruction="Follow policy",
            log_required=False,
        )

    first = await cache.get_or_load("school-a", "assignment-1", loader)
    second = await cache.get_or_load("school-a", "assignment-1", loader)
    assert first.policy_version == second.policy_version == "v1"
    assert loads["count"] == 1

    await cache.invalidate("school-a", "assignment-1")
    assert await cache.get("school-a", "assignment-1") is None


async def test_policy_cache_keys_are_tenant_scoped(redis_store, settings):
    cache = PolicyCache(redis_store, settings)
    await cache.set(
        "school-a",
        CachedPolicy("assignment-1", "v1", "GREEN", "direct", "", False),
    )
    assert await cache.get("school-b", "assignment-1") is None
    assert await cache.get("school-a", "assignment-1") is not None


async def test_sessions_expire_and_are_revoked(redis_store, settings):
    sessions = SessionStore(redis_store, settings)
    record = await sessions.create("school-a", "account-1", "student", ttl_seconds=60)
    assert await sessions.get("school-a", record.session_id) is not None
    # A different tenant must not resolve the session even with the same id.
    assert await sessions.get("school-b", record.session_id) is None
    assert await sessions.revoke("school-a", record.session_id) is True
    assert await sessions.get("school-a", record.session_id) is None


async def test_session_role_is_validated(redis_store, settings):
    sessions = SessionStore(redis_store, settings)
    with pytest.raises(Exception):
        await sessions.create("school-a", "account-1", "superuser")


async def test_rate_limiter_counts_within_the_window(redis_store, settings):
    limiter = RateLimiter(redis_store, settings)
    decisions = [
        await limiter.check("school-a", "student-1", limit=3, window_seconds=60)
        for _ in range(4)
    ]
    assert [d.allowed for d in decisions] == [True, True, True, False]
    assert decisions[-1].remaining == 0
    # Counters are per subject and per tenant.
    assert (await limiter.check("school-b", "student-1", limit=3, window_seconds=60)).allowed
    assert (await limiter.check("school-a", "student-2", limit=3, window_seconds=60)).allowed


async def test_step_event_stream_is_tenant_scoped_and_consumable(redis_store, settings):
    stream = StepEventStream(redis_store, settings)
    await stream.ensure_group("school-a")
    await stream.publish("school-a", {"event_type": "hint_request", "step": 1})
    await stream.publish("school-a", {"event_type": "revision", "step": 2})

    entries = await stream.consume("school-a", "worker-1", block_ms=10)
    assert [e.event["event_type"] for e in entries] == ["hint_request", "revision"]
    await stream.acknowledge("school-a", *(e.entry_id for e in entries))

    # A second institution's stream is separate.
    await stream.ensure_group("school-b")
    assert await stream.consume("school-b", "worker-1", block_ms=10) == []
    assert await stream.length("school-a") == 2


async def test_step_event_stream_is_capped(redis_store, settings):
    stream = StepEventStream(redis_store, settings)
    for step in range(10):
        await stream.publish("school-a", {"step": step}, maxlen=5)
    assert await stream.length("school-a") <= 5


async def test_student_export_returns_only_their_own_records(clean_db, pg_app):
    await seed_institution(clean_db, "school-a")
    _, assignment_id = await seed_course_and_assignment(clean_db, "school-a")
    mine = "stu_" + "e" * 32
    theirs = "stu_" + "f" * 32
    async with pg_app.pool.acquire() as connection:
        async with connection.transaction():
            await connection.execute(
                "SELECT set_config('app.institution_id', 'school-a', true)"
            )
            for pseudonym, step in ((mine, 1), (theirs, 1)):
                await connection.execute(
                    """
                    INSERT INTO assessment.process_logs (
                        institution_id, assignment_id, pseudonymous_id, step_number,
                        event_type, summary, occurred_at
                    ) VALUES ('school-a', $1, $2, $3, 'step', 'worked on loops', now())
                    """,
                    assignment_id,
                    pseudonym,
                    step,
                )

    export = await StudentDataExporter(TenantScopedStore(pg_app, "school-a")).export(mine)
    assert export.pseudonymous_id == mine
    assert len(export.process_logs) == 1
    assert "worked on loops" in export.to_json()


async def test_student_export_rejects_a_non_pseudonymous_identifier(clean_db, pg_app):
    await seed_institution(clean_db, "school-a")
    exporter = StudentDataExporter(TenantScopedStore(pg_app, "school-a"))
    with pytest.raises(ValueError):
        await exporter.export("priya.sharma@example.edu")


async def test_rate_limiter_counter_always_has_a_ttl(redis_store, settings):
    limiter = RateLimiter(redis_store, settings)
    await limiter.check("school-a", "student-1", limit=5, window_seconds=60)
    keys = await redis_store.client.keys("*")
    assert keys
    for key in keys:
        assert await redis_store.client.ttl(key) > 0


# --------------------------------------------------------------------------- #
# Analytics store
# --------------------------------------------------------------------------- #


async def test_small_cohorts_are_suppressed(clean_db, pg_app, settings):
    await seed_institution(clean_db, "school-a")
    analytics = AnalyticsStore(TenantScopedStore(pg_app, "school-a"), settings)
    with pytest.raises(CohortTooSmallError):
        await analytics.record(
            Measure("mastery", "language", "hi", "2026-10", cohort_size=2,
                    numerator=1, denominator=2, value=0.5)
        )


async def test_analytics_records_only_aggregates_and_reads_them_back(clean_db, pg_app, settings):
    await seed_institution(clean_db, "school-a")
    analytics = AnalyticsStore(TenantScopedStore(pg_app, "school-a"), settings)
    await analytics.record(
        Measure("mastery", "language", "hi", "2026-10", cohort_size=40,
                numerator=30, denominator=40, value=0.75)
    )
    rows = await analytics.read("mastery", "2026-10")
    assert len(rows) == 1
    assert rows[0]["cohort_size"] == 40
    assert rows[0]["value"] == pytest.approx(0.75)


async def test_analytics_measures_are_tenant_isolated(clean_db, pg_app, settings):
    await seed_institution(clean_db, "school-a")
    await seed_institution(clean_db, "school-b")
    analytics_a = AnalyticsStore(TenantScopedStore(pg_app, "school-a"), settings)
    analytics_b = AnalyticsStore(TenantScopedStore(pg_app, "school-b"), settings)
    await analytics_a.record(
        Measure("equity", "device", "mobile", "2026-10", 50, 20, 50, 0.4)
    )
    assert len(await analytics_a.read("equity", "2026-10")) == 1
    assert await analytics_b.read("equity", "2026-10") == []


async def test_analytics_rejects_impossible_ratios(clean_db, pg_app, settings):
    await seed_institution(clean_db, "school-a")
    analytics = AnalyticsStore(TenantScopedStore(pg_app, "school-a"), settings)
    with pytest.raises(ValueError):
        await analytics.record(
            Measure("mastery", "language", "hi", "2026-10", 40, 50, 40, 0.5)
        )


# --------------------------------------------------------------------------- #
# Retention
# --------------------------------------------------------------------------- #


async def test_retention_purges_expired_rows_and_keeps_recent_ones(clean_db, pg_app):
    await seed_institution(clean_db, "school-a")
    _, assignment_id = await seed_course_and_assignment(clean_db, "school-a")
    pseudonym = "stu_" + "d" * 32

    async with pg_app.pool.acquire() as connection:
        async with connection.transaction():
            await connection.execute(
                "SELECT set_config('app.institution_id', 'school-a', true)"
            )
            for days_ago in (400, 5):
                await connection.execute(
                    """
                    INSERT INTO assessment.process_logs (
                        institution_id, assignment_id, pseudonymous_id, step_number,
                        event_type, occurred_at, recorded_at
                    ) VALUES ('school-a', $1, $2, $3, 'step',
                              now() - make_interval(days => $4),
                              now() - make_interval(days => $4))
                    """,
                    assignment_id,
                    pseudonym,
                    days_ago,
                    days_ago,
                )

    policy = RetentionPolicy(process_data_days=365, assessment_days=1_825, audit_days=2_555)
    report = await RetentionJob(
        TenantScopedStore(pg_app, "school-a"), policy
    ).run()
    assert report.purged["process_logs"] == 1

    remaining = await TenantScopedStore(pg_app, "school-a").fetchval(
        "SELECT count(*) FROM assessment.process_logs"
    )
    assert remaining == 1


async def test_retention_never_touches_the_audit_trail(clean_db, pg_app):
    await seed_institution(clean_db, "school-a")
    audit = AuditStore(TenantScopedStore(pg_app, "school-a"))
    await audit.append(event_type="access")
    report = await RetentionJob(
        TenantScopedStore(pg_app, "school-a"),
        RetentionPolicy(process_data_days=1, assessment_days=1, audit_days=2_555),
    ).run()
    assert "audit" not in " ".join(report.purged)
    assert (await audit.verify()).verified is True


async def test_retention_policy_rejects_invalid_windows():
    with pytest.raises(ValueError):
        RetentionPolicy(process_data_days=0)


# --------------------------------------------------------------------------- #
# Policy decisions reach durable audit evidence
# --------------------------------------------------------------------------- #


async def test_policy_guard_decision_is_persisted_to_the_audit_chain(clean_db, pg_app):
    from app.core.config import Settings
    from app.services.data.audit_sink import DurableAuditSink
    from app.services.policy.guard import PolicyGuardEngine, PolicyLevel

    await seed_institution(clean_db, "school-a")

    settings = Settings.model_validate({
    "POSTGRES_DSN": os.environ.get(
        "TEST_POSTGRES_APP_DSN",
        "postgresql://resonance_app:app_local_dev_password@localhost:5432/resonanceai",
    ),
    "PROCESS_LOG_PSEUDONYM_KEY": PSEUDONYM_KEY,})
    sink = DurableAuditSink("school-a", settings=settings)
    try:
        guard = PolicyGuardEngine(
            {"policy_tier": PolicyLevel.GREEN, "assignment_id": "assignment-1",
             "policy_version": "v3", "course_id": "course-1"},
            {PolicyLevel.GREEN: {"system_instruction": "Direct help.", "log_required": False}},
            audit_hook=sink.hook,
        )
        decision = guard.validate_request("Explain recursion")
        assert decision["allowed"] is True
    finally:
        sink.close()

    audit = AuditStore(TenantScopedStore(pg_app, "school-a"))
    rows = await audit.store.fetch(
        "SELECT event_type, policy_level, allowed, prompt_sha256 FROM audit.audit_events"
    )
    assert len(rows) == 1
    assert rows[0]["event_type"] == "policy_decision"
    assert rows[0]["policy_level"] == "FULL_INTEGRATION"
    assert rows[0]["allowed"] is True
    # The raw prompt is represented by a digest, never stored verbatim.
    assert len(rows[0]["prompt_sha256"]) == 64
    assert (await audit.verify()).verified is True


async def test_policy_guard_denies_when_the_audit_sink_is_unavailable(clean_db, pg_app):
    from app.core.config import Settings
    from app.services.data.audit_sink import DurableAuditSink
    from app.services.policy.guard import PolicyAuditWriteError, PolicyGuardEngine, PolicyLevel

    await seed_institution(clean_db, "school-a")
    # Point the sink at a port nothing is listening on.
    settings = Settings.model_validate({
    "POSTGRES_DSN": "postgresql://resonance_app:app_local_dev_password@localhost:59999/resonanceai",
    "PROCESS_LOG_PSEUDONYM_KEY": PSEUDONYM_KEY,
})
    sink = DurableAuditSink("school-a", settings=settings)
    guard = PolicyGuardEngine(
        {"policy_tier": PolicyLevel.GREEN},
        {PolicyLevel.GREEN: {"system_instruction": "help", "log_required": False}},
        audit_hook=sink.hook,
    )
    # A decision that cannot be audited must not be released.
    with pytest.raises(PolicyAuditWriteError):
        guard.validate_request("Explain recursion")
    sink.close()
