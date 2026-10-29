-- 0005: Audit schema - append-only, tamper-evident decision and access evidence.
--
-- Inserts only. Updates and deletes are rejected by a trigger, not merely by
-- grants, so even a role with table privileges cannot rewrite history
-- (NFR-SEC-07, BR-19). Events are hash-chained per institution: each row commits
-- to its predecessor, so removing or editing any row breaks verification.

CREATE TABLE IF NOT EXISTS audit.audit_events (
    event_id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    institution_id      text NOT NULL,
    event_type          text NOT NULL,
    actor_pseudonymous_id text,
    course_id           text,
    assignment_id       text,
    policy_version      text,
    policy_level        text,
    allowed             boolean,
    reason              text,
    payload             jsonb NOT NULL DEFAULT '{}'::jsonb,
    prompt_sha256       text,
    previous_event_hash text NOT NULL,
    event_hash          text NOT NULL,
    occurred_at         timestamptz NOT NULL DEFAULT now(),
    UNIQUE (institution_id, event_hash)
);
CREATE INDEX IF NOT EXISTS audit_events_institution_idx
    ON audit.audit_events (institution_id, event_id);
CREATE INDEX IF NOT EXISTS audit_events_type_idx
    ON audit.audit_events (institution_id, event_type, occurred_at);

CREATE OR REPLACE FUNCTION audit.reject_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'audit events are append-only (attempted %)', TG_OP
        USING ERRCODE = '55000';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS audit_events_no_update ON audit.audit_events;
CREATE TRIGGER audit_events_no_update
    BEFORE UPDATE OR DELETE ON audit.audit_events
    FOR EACH ROW EXECUTE FUNCTION audit.reject_mutation();

DROP TRIGGER IF EXISTS audit_events_no_truncate ON audit.audit_events;
CREATE TRIGGER audit_events_no_truncate
    BEFORE TRUNCATE ON audit.audit_events
    EXECUTE FUNCTION audit.reject_mutation();

ALTER TABLE audit.audit_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit.audit_events FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_isolation ON audit.audit_events;
CREATE POLICY tenant_isolation ON audit.audit_events
    USING (institution_id = current_setting('app.institution_id', true))
    WITH CHECK (institution_id = current_setting('app.institution_id', true));

GRANT SELECT, INSERT ON audit.audit_events TO resonance_app;
GRANT SELECT, INSERT ON audit.audit_events TO resonance_audit_writer;
REVOKE UPDATE, DELETE, TRUNCATE ON audit.audit_events FROM PUBLIC;
