-- 0003: Identity schema - accounts, roles, consent and the pseudonym mapping.
--
-- High sensitivity. Only resonance_identity_owner (a distinct, restricted role)
-- may read or write here; resonance_app has no privileges at all. The mapping
-- from a real identity to a pseudonymous id lives here and nowhere else, so the
-- assessment schema cannot be re-identified from the application path
-- (NFR-PRV-01, BRL-09).

CREATE TABLE IF NOT EXISTS identity.accounts (
    account_id      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    institution_id  text NOT NULL,
    external_subject text NOT NULL,
    role            text NOT NULL CHECK (role IN ('student', 'faculty', 'admin', 'auditor')),
    status          text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'suspended', 'deleted')),
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (institution_id, account_id),
    UNIQUE (institution_id, external_subject)
);
CREATE INDEX IF NOT EXISTS accounts_institution_role_idx
    ON identity.accounts (institution_id, role);

CREATE TABLE IF NOT EXISTS identity.consent_records (
    consent_id      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    institution_id  text NOT NULL,
    pseudonymous_id text NOT NULL,
    scope           text NOT NULL,
    granted         boolean NOT NULL,
    recorded_at     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (institution_id, consent_id)
);
CREATE INDEX IF NOT EXISTS consent_records_lookup_idx
    ON identity.consent_records (institution_id, pseudonymous_id, scope);

-- The mapping is the only place a real identity meets a pseudonym. It is held
-- under institution control and is not exposed to the assessment path.
CREATE TABLE IF NOT EXISTS identity.pseudonym_map (
    institution_id  text NOT NULL,
    pseudonymous_id text NOT NULL,
    account_id      uuid NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (institution_id, pseudonymous_id),
    UNIQUE (institution_id, account_id),
    FOREIGN KEY (institution_id, account_id)
        REFERENCES identity.accounts (institution_id, account_id) ON DELETE CASCADE
);

ALTER TABLE identity.accounts ENABLE ROW LEVEL SECURITY;
ALTER TABLE identity.accounts FORCE ROW LEVEL SECURITY;
ALTER TABLE identity.consent_records ENABLE ROW LEVEL SECURITY;
ALTER TABLE identity.consent_records FORCE ROW LEVEL SECURITY;
ALTER TABLE identity.pseudonym_map ENABLE ROW LEVEL SECURITY;
ALTER TABLE identity.pseudonym_map FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_isolation ON identity.accounts;
CREATE POLICY tenant_isolation ON identity.accounts
    USING (institution_id = current_setting('app.institution_id', true))
    WITH CHECK (institution_id = current_setting('app.institution_id', true));

DROP POLICY IF EXISTS tenant_isolation ON identity.consent_records;
CREATE POLICY tenant_isolation ON identity.consent_records
    USING (institution_id = current_setting('app.institution_id', true))
    WITH CHECK (institution_id = current_setting('app.institution_id', true));

DROP POLICY IF EXISTS tenant_isolation ON identity.pseudonym_map;
CREATE POLICY tenant_isolation ON identity.pseudonym_map
    USING (institution_id = current_setting('app.institution_id', true))
    WITH CHECK (institution_id = current_setting('app.institution_id', true));

GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA identity TO resonance_identity_owner;
