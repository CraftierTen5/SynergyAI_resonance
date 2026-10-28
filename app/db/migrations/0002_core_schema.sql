-- 0002: Core schema - institutions, courses, assignments, policies and versions.
--
-- Low-to-medium sensitivity. Retained per academic term and then archived.
-- Every table is tenant-scoped and protected by row-level security.

CREATE TABLE IF NOT EXISTS core.institutions (
    institution_id     text PRIMARY KEY,
    name               text NOT NULL,
    data_region        text NOT NULL DEFAULT 'ap-south-1',
    retention_profile  jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT institutions_id_format CHECK (institution_id ~ '^[A-Za-z0-9_-]{1,128}$')
);

CREATE TABLE IF NOT EXISTS core.courses (
    course_id       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    institution_id  text NOT NULL REFERENCES core.institutions(institution_id) ON DELETE CASCADE,
    code            text NOT NULL,
    title           text NOT NULL,
    academic_term   text NOT NULL,
    archived_at     timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (institution_id, course_id),
    UNIQUE (institution_id, code, academic_term)
);
CREATE INDEX IF NOT EXISTS courses_institution_idx ON core.courses (institution_id);

CREATE TABLE IF NOT EXISTS core.assignments (
    assignment_id   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    institution_id  text NOT NULL REFERENCES core.institutions(institution_id) ON DELETE CASCADE,
    course_id       uuid NOT NULL,
    title           text NOT NULL,
    policy_tier     text NOT NULL CHECK (policy_tier IN ('RED', 'YELLOW', 'GREEN')),
    due_at          timestamptz,
    archived_at     timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (institution_id, assignment_id),
    FOREIGN KEY (institution_id, course_id)
        REFERENCES core.courses (institution_id, course_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS assignments_institution_course_idx
    ON core.assignments (institution_id, course_id);

CREATE TABLE IF NOT EXISTS core.policy_versions (
    policy_version_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    institution_id    text NOT NULL REFERENCES core.institutions(institution_id) ON DELETE CASCADE,
    assignment_id     uuid NOT NULL,
    version_label     text NOT NULL,
    policy_tier       text NOT NULL CHECK (policy_tier IN ('RED', 'YELLOW', 'GREEN')),
    instruction_profile text NOT NULL CHECK (instruction_profile IN ('guided', 'direct')),
    system_instruction  text NOT NULL DEFAULT '',
    log_required        boolean NOT NULL DEFAULT false,
    effective_from      timestamptz NOT NULL DEFAULT now(),
    retired_at          timestamptz,
    created_at          timestamptz NOT NULL DEFAULT now(),
    UNIQUE (institution_id, policy_version_id),
    UNIQUE (institution_id, assignment_id, version_label),
    FOREIGN KEY (institution_id, assignment_id)
        REFERENCES core.assignments (institution_id, assignment_id) ON DELETE CASCADE,
    -- Only one live version per assignment at a time.
    CONSTRAINT policy_versions_label_not_blank CHECK (length(btrim(version_label)) > 0)
);
CREATE INDEX IF NOT EXISTS policy_versions_current_idx
    ON core.policy_versions (institution_id, assignment_id)
    WHERE retired_at IS NULL;

CREATE TABLE IF NOT EXISTS core.policy_acknowledgements (
    acknowledgement_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    institution_id     text NOT NULL REFERENCES core.institutions(institution_id) ON DELETE CASCADE,
    policy_version_id  uuid NOT NULL,
    pseudonymous_id    text NOT NULL,
    acknowledged_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (institution_id, acknowledgement_id),
    UNIQUE (institution_id, policy_version_id, pseudonymous_id),
    FOREIGN KEY (institution_id, policy_version_id)
        REFERENCES core.policy_versions (institution_id, policy_version_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS policy_acknowledgements_institution_idx
    ON core.policy_acknowledgements (institution_id, policy_version_id);

-- Row-level security: a session without app.institution_id sees no rows.
DO $$
DECLARE
    tbl text;
BEGIN
    FOREACH tbl IN ARRAY ARRAY[
        'core.institutions', 'core.courses', 'core.assignments',
        'core.policy_versions', 'core.policy_acknowledgements'
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
