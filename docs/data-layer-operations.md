# Data Layer Operations

This document covers the three stores in the ResonanceAI data layer, the roles
that guard them, and how to run migrations, backups and retention.

## Stores and their jobs

| Store | Technology | Holds | Notes |
| --- | --- | --- | --- |
| Relational | PostgreSQL | Institutions, courses, assignments, policy versions, pseudonymised profiles, submissions, process summaries, viva, evaluations, audit records | System of record |
| Cache and stream | Redis | Sessions, rate-limit counters, policy cache, conversation buffers, short-lived raw process-log buffer, step-event stream | Never a system of record; loss degrades, never destroys |
| Aggregated analytics | PostgreSQL (`analytics` schema) | Anonymised institution-level measures only | No pseudonymous ids reach it |

## Schemas and sensitivity

| Schema | Contents | Reaching roles |
| --- | --- | --- |
| `core` | Academic structure, policy configuration | `resonance_app` |
| `identity` | Accounts, consent, pseudonym mapping | `resonance_identity_owner` only |
| `assessment` | Anonymised process logs, submissions, viva, evaluations | `resonance_app` |
| `audit` | Append-only, hash-chained events | `resonance_app` (insert/select), `resonance_audit_writer` (insert/select) |
| `analytics` | Institution-level aggregates | `resonance_app` |
| all | Backup and DR reads | `resonance_backup` (BYPASSRLS) |

`resonance_app` has no privileges on `identity`, so the application path cannot
re-identify a pseudonym. Row-level security is enabled and forced on every table,
so a query that forgets a tenant filter returns nothing rather than everything.

## Roles

| Role | Purpose | Privileges |
| --- | --- | --- |
| `resonance_migrator` | Apply schema migrations | DDL on the database, runs as the object owner |
| `resonance_app` | Serve requests | DML on `core`, `assessment`, `analytics`; insert/select on `audit` |
| `resonance_audit_writer` | Dedicated audit append path | insert/select on `audit` |
| `resonance_identity_owner` | Institution-controlled identity mapping | DML on `identity` |
| `resonance_backup` | Logical backups and restores | `SELECT` on every schema, `BYPASSRLS` |

Row-level security is `FORCE`d, which also blocks the table owner. `pg_dump`
therefore needs the `BYPASSRLS` backup role; no service uses it.

## Migrations

Schema is defined as code under `app/db/migrations` and applied in lexical
order. Applied files are recorded in `public.schema_migrations`, and the runner
holds an advisory lock so a rolling deployment cannot apply a file twice.

```bash
POSTGRES_DSN="postgresql://resonance_migrator:...@host:5432/resonanceai" \
    python scripts/bootstrap_data_layer.py
```

Migration rules:

- Add a new numbered file; never edit an applied one.
- Keep changes backward compatible so a rolling release does not break the
  previous version still serving traffic (DL-12).
- Add every new table to the row-level security block and to the backup grants.

## Backup, restore and disaster recovery

Recovery targets: RPO of 1 hour or less, RTO of 4 hours or less (NFR-REL-02).

```bash
# Take a compressed logical dump.
PGSSLMODE=disable python scripts/backup_restore.py backup \
    --dsn "postgresql://resonance_backup:...@host:5432/resonanceai" \
    --out /backups/resonanceai-$(date -u +%Y%m%dT%H%M%SZ).dump

# Restore it into a scratch database and check integrity, then drop the scratch.
PGSSLMODE=disable python scripts/backup_restore.py verify \
    --admin-dsn "postgresql://resonance_migrator:...@host:5432/postgres" \
    --dump /backups/resonanceai-20260101T000000Z.dump
```

`verify` checks that every schema restored, that the audit guard triggers
survived, and then drops the scratch database. A restore is not considered good
until this passes.

Operational notes:

- Take a daily logical backup and enable continuous WAL archiving for point-in-time
  recovery within the one-hour RPO.
- Run a verified restore on a schedule, not only when an incident happens.
- Redis is not backed up as a system of record. Rebuild it: sessions are
  re-established at sign-in, caches refill, and any unprocessed step events are
  reconciled from the durable assessment records (DL-4).

## Retention

Retention windows are configurable per institution (BRL-10) and default to the
validated settings. `RetentionJob` purges process data and assessment records
once their window passes. Audit records are never purged by that path; they carry
their own long retention.

| Data | Default window | Setting |
| --- | --- | --- |
| Process data | 365 days | `PROCESS_DATA_RETENTION_DAYS` |
| Assessment records | 1,825 days | `ASSESSMENT_RETENTION_DAYS` |
| Audit records | 2,555 days | `AUDIT_RETENTION_DAYS` |

## Privacy controls in the layer

- Raw process events live only in Redis, under an enforced TTL, and are never
  written to PostgreSQL verbatim. `ProcessLogPersistenceService` anonymises each
  event before the insert and clears the buffer afterwards.
- Assessment rows reference a student only by pseudonymous id (`stu_<32 hex>`),
  produced by an institution-scoped HMAC.
- Aggregates below `ANALYTICS_MIN_COHORT_SIZE` are refused on write and filtered
  on read, so a small group cannot be singled out (DL-9).
- Students can export their own process data and consent record; the export is
  scoped to one pseudonym and never resolves it to a real identity.

## Audit integrity

The audit trail is append-only at two levels: the application role has no
`UPDATE` or `DELETE` grant, and a trigger rejects `UPDATE`, `DELETE` and
`TRUNCATE` regardless of privilege. Each event commits to the hash of the event
before it, so editing or removing a row is detectable even if the trigger is
disabled. `AuditStore.verify()` replays the chain and reports the first event
that breaks it. Run it as part of routine health checks and after any restore.

## Observability

Watch query latency, replication lag, cache hit rate, stream lag, buffer sizes,
TTL expiry counts, and audit write failures (NFR-MNT-02). Alert immediately on
any audit write failure, because a decision that could not be audited must not
be served.

## Data residency

Production data stays in an India region unless an institution has approved
otherwise (NFR-PRV-05). `DATA_REGION` defaults to `ap-south-1` and the settings
reject a non-India region unless `ALLOW_NON_INDIA_REGION` is explicitly set.
