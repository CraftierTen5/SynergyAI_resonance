# AGENTS.md

Repository memory for ResonanceAI. Read this before changing the data layer.

## What this repository is

ResonanceAI is built as layers. The services present today cover Layer 3 (domain
services: policy guard, multilingual AI service, assessment and process engine)
and Layer 4 (the data layer). Services above the data layer are stateless; all
state lives in the three stores described below.

## Layout

```
app/core/config.py            Validated settings (secrets are SecretStr)
app/schemas/                  Pydantic request/response and record models
app/services/ai/              Layer 3B: providers, resilience, usage metering, guidance
app/services/policy/          Layer 3A: fail-closed policy guard, hash-chained audit hooks
app/services/assessment/      Layer 3C: anonymisation boundary, viva generation
app/services/data/            Layer 4 services: cache, stream, retention, analytics, export
app/db/migrations/            Schema as code; apply in lexical order
app/db/postgres.py            Pool + migration runner
app/db/access.py              Tenant-scoped access; rejects calls without institution id
app/db/audit.py               Append-only hash-chained audit store
app/db/redis_store.py         Redis client + tenant key prefixes
docs/data-layer-operations.md Operations runbook (roles, backup, retention)
scripts/                      bootstrap_data_layer.py, backup_restore.py
```

## Commands

```bash
pip install -r requirements.txt
python -m pytest -q                       # full suite; data-layer tests skip without services

# Local services (PostgreSQL 17 and Redis must be running)
POSTGRES_DSN="postgresql://resonance_migrator:migrator_local_dev_password@localhost:5432/resonanceai" \
    python scripts/bootstrap_data_layer.py
```

## Data-layer invariants (do not break these)

1. Every table carries `institution_id`, has row-level security enabled and
   forced, and has a `tenant_isolation` policy. Add new tables to the loop in
   `0002`/`0004`/`0006` and to the backup grants in `0001`.
2. Every data-layer call goes through `TenantScopedStore`, which rejects a call
   without an institution id and binds `app.institution_id` for the transaction.
   Never query the pool directly from service code.
3. `resonance_app` has no privileges on the `identity` schema. The pseudonym
   mapping stays out of the application path.
4. Raw process events live only in Redis under a TTL and are never persisted
   verbatim. Persist through `ProcessLogPersistenceService`, which anonymises
   first.
5. Audit is append-only at the grant level and at the trigger level, and each
   event is hash-chained. Never add an `UPDATE`/`DELETE` path to audit.
6. The `analytics` schema holds counts and ratios only. Suppress cohorts below
   `ANALYTICS_MIN_COHORT_SIZE` on both write and read.
7. Row-level security is `FORCE`d, so even the table owner is blocked. Backups
   need the `BYPASSRLS` `resonance_backup` role.

## Style

- Match the surrounding code: `from __future__ import annotations`, typed
  signatures, dataclasses for value objects, pydantic for boundary models.
- Secrets are `SecretStr` and are never logged.
- Comments explain non-obvious invariants, not what the next line does.

## Testing

`tests/test_data_layer.py` runs against real PostgreSQL and Redis and skips
cleanly when either is unavailable. Do not replace these with mocks: tenant
isolation, RLS and append-only audit can only be verified against the real
engine. Override endpoints with `TEST_POSTGRES_MIGRATOR_DSN`,
`TEST_POSTGRES_APP_DSN` and `TEST_REDIS_DSN`.
