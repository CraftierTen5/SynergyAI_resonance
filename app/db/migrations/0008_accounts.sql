-- 0008: Institution-scoped application accounts.
CREATE TABLE IF NOT EXISTS core.users (
    user_id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    institution_id  text NOT NULL REFERENCES core.institutions(institution_id) ON DELETE CASCADE,
    email           text NOT NULL,
    full_name       text NOT NULL,
    department      text NOT NULL DEFAULT '',
    password_hash   text NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (institution_id, user_id),
    UNIQUE (institution_id, email)
);

ALTER TABLE core.users ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.users FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON core.users;
CREATE POLICY tenant_isolation ON core.users
    USING (institution_id = current_setting('app.institution_id', true))
    WITH CHECK (institution_id = current_setting('app.institution_id', true));
