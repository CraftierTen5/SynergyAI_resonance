-- 0004: Assessment schema - anonymised process logs, submissions, viva and
-- evaluations.
--
-- High sensitivity, but by construction it holds only pseudonymous identifiers.
-- Nothing in this schema stores a name, email, phone, address or government id;
-- those are stripped by the anonymisation boundary before a row is written
-- (NFR-PRV-01). Retention is bounded by the institution's retention profile.

CREATE TABLE IF NOT EXISTS assessment.process_logs (
    log_id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    institution_id  text NOT NULL,
    assignment_id   uuid NOT NULL,
    pseudonymous_id text NOT NULL,
    step_number     integer NOT NULL CHECK (step_number > 0),
    event_type      text NOT NULL,
    language        text,
    prompt_digest   text,
    summary         text,
    metadata        jsonb NOT NULL DEFAULT '{}'::jsonb,
    occurred_at     timestamptz NOT NULL,
    recorded_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS process_logs_lookup_idx
    ON assessment.process_logs (institution_id, assignment_id, pseudonymous_id, step_number);
CREATE INDEX IF NOT EXISTS process_logs_recorded_idx
    ON assessment.process_logs (institution_id, recorded_at);

CREATE TABLE IF NOT EXISTS assessment.submissions (
    submission_id   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    institution_id  text NOT NULL,
    assignment_id   uuid NOT NULL,
    pseudonymous_id text NOT NULL,
    capability_breakdown jsonb NOT NULL DEFAULT '{}'::jsonb,
    submitted_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (institution_id, submission_id)
);
CREATE INDEX IF NOT EXISTS submissions_lookup_idx
    ON assessment.submissions (institution_id, assignment_id, pseudonymous_id);

CREATE TABLE IF NOT EXISTS assessment.viva_sessions (
    viva_id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    institution_id  text NOT NULL,
    submission_id   uuid NOT NULL,
    pseudonymous_id text NOT NULL,
    questions       jsonb NOT NULL DEFAULT '[]'::jsonb,
    responses       jsonb NOT NULL DEFAULT '[]'::jsonb,
    status          text NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'in_progress', 'completed', 'void')),
    created_at      timestamptz NOT NULL DEFAULT now(),
    completed_at    timestamptz,
    UNIQUE (institution_id, viva_id),
    FOREIGN KEY (institution_id, submission_id)
        REFERENCES assessment.submissions (institution_id, submission_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS viva_sessions_lookup_idx
    ON assessment.viva_sessions (institution_id, pseudonymous_id);

CREATE TABLE IF NOT EXISTS assessment.evaluations (
    evaluation_id   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    institution_id  text NOT NULL,
    submission_id   uuid NOT NULL,
    pseudonymous_id text NOT NULL,
    evaluator_role  text NOT NULL CHECK (evaluator_role IN ('faculty', 'moderator')),
    scores          jsonb NOT NULL DEFAULT '{}'::jsonb,
    comments        text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (institution_id, evaluation_id),
    FOREIGN KEY (institution_id, submission_id)
        REFERENCES assessment.submissions (institution_id, submission_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS evaluations_lookup_idx
    ON assessment.evaluations (institution_id, submission_id);

DO $$
DECLARE
    tbl text;
BEGIN
    FOREACH tbl IN ARRAY ARRAY[
        'assessment.process_logs', 'assessment.submissions',
        'assessment.viva_sessions', 'assessment.evaluations'
    ] LOOP
        EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', tbl);
        EXECUTE format('ALTER TABLE %s FORCE ROW LEVEL SECURITY', tbl);
        EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %s', tbl);
        EXECUTE format($fmt$
            CREATE POLICY tenant_isolation ON %s
            USING (institution_id = current_setting('app.institution_id', true))
            WITH CHECK (institution_id = current_setting('app.institution_id', true))
        $fmt$, tbl);
    END LOOP;
END
$$;
