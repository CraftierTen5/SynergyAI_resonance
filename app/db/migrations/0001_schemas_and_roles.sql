-- 0001: Schemas and least-privilege roles.
--
-- The Data Layer separates data by sensitivity into schemas so that a missing
-- tenant filter or a compromised service role cannot reach everything:
--   core        academic structure and policy configuration
--   identity    pseudonym mapping and account links (restricted)
--   assessment  anonymised process data, submissions, viva, evaluations
--   audit       append-only, tamper-evident decision and access evidence
--   analytics   anonymised institution-level aggregates only
--
-- Every table carries institution_id and is protected by row-level security so
-- that a query without a tenant scope returns nothing (fails safe).

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS identity;
CREATE SCHEMA IF NOT EXISTS assessment;
CREATE SCHEMA IF NOT EXISTS audit;
CREATE SCHEMA IF NOT EXISTS analytics;

-- Application role: read/write on core, assessment and analytics; no access to
-- the identity mapping; insert-only on audit.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'resonance_app') THEN
        CREATE ROLE resonance_app NOLOGIN NOINHERIT;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'resonance_audit_writer') THEN
        CREATE ROLE resonance_audit_writer NOLOGIN NOINHERIT;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'resonance_identity_owner') THEN
        CREATE ROLE resonance_identity_owner NOLOGIN NOINHERIT;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'resonance_migrator') THEN
        CREATE ROLE resonance_migrator NOLOGIN NOINHERIT;
    END IF;
END
$$;

-- Backup role. Row-level security is FORCEd on every table, which blocks even
-- the table owner from reading rows without a tenant scope - and that also
-- blocks pg_dump. A dedicated backup role with BYPASSRLS is the one identity
-- that may read across tenants, and it is used only by the backup and DR
-- tooling, never by a service (NFR-REL-02).
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'resonance_backup') THEN
        CREATE ROLE resonance_backup NOLOGIN NOINHERIT BYPASSRLS;
    END IF;
END
$$;

GRANT USAGE ON SCHEMA core, assessment, audit, analytics TO resonance_app;
GRANT USAGE ON SCHEMA audit TO resonance_audit_writer;
GRANT USAGE ON SCHEMA identity TO resonance_identity_owner;
GRANT USAGE ON SCHEMA core, identity, assessment, audit, analytics TO resonance_backup;
GRANT SELECT ON ALL TABLES IN SCHEMA core, identity, assessment, audit, analytics
    TO resonance_backup;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA core, identity, assessment, audit, analytics
    TO resonance_backup;

REVOKE ALL ON SCHEMA identity FROM resonance_app;
REVOKE ALL ON SCHEMA identity FROM PUBLIC;
REVOKE ALL ON SCHEMA audit FROM PUBLIC;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;

-- Default privileges for objects created later by the migrator.
ALTER DEFAULT PRIVILEGES IN SCHEMA core GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO resonance_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA assessment GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO resonance_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA analytics GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO resonance_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA audit GRANT SELECT, INSERT ON TABLES TO resonance_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA audit GRANT SELECT, INSERT ON TABLES TO resonance_audit_writer;
ALTER DEFAULT PRIVILEGES IN SCHEMA identity GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO resonance_identity_owner;
ALTER DEFAULT PRIVILEGES IN SCHEMA core, identity, assessment, audit, analytics
    GRANT SELECT ON TABLES TO resonance_backup;
ALTER DEFAULT PRIVILEGES IN SCHEMA core, identity, assessment, audit, analytics
    GRANT USAGE, SELECT ON SEQUENCES TO resonance_backup;
