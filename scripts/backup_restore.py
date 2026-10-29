#!/usr/bin/env python3
"""Back up and restore the relational store, and verify a restore works.

Backups must be tested, not just taken (NFR-REL-02). ``backup`` writes a
compressed logical dump; ``verify`` restores that dump into a scratch database,
runs integrity checks (including the audit chain), then drops the scratch
database so a bad restore is caught before it matters.

    python scripts/backup_restore.py backup --dsn "$POSTGRES_DSN" --out /backups/resonanceai.dump
    python scripts/backup_restore.py verify --admin-dsn "$ADMIN_DSN" --dump /backups/resonanceai.dump
"""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
import uuid
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SCHEMAS = ("core", "identity", "assessment", "audit", "analytics")


def run(command: list[str], *, env: dict[str, str] | None = None) -> None:
    print(f"$ {' '.join(command)}", flush=True)
    subprocess.run(command, check=True, env=env)


def backup(dsn: str, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            "pg_dump",
            "--format=custom",
            "--compress=6",
            f"--file={out}",
            f"--dbname={dsn}",
        ]
    )
    print(f"backup written to {out} ({out.stat().st_size} bytes)")


async def verify(admin_dsn: str, dump: Path) -> int:
    import asyncpg

    scratch = f"resonance_restore_{uuid.uuid4().hex[:12]}"
    admin = await asyncpg.connect(dsn=admin_dsn)
    try:
        await admin.execute(f'CREATE DATABASE "{scratch}"')
        scratch_dsn = _replace_database(admin_dsn, scratch)
        run(["pg_restore", "--no-owner", f"--dbname={scratch_dsn}", str(dump)])

        connection = await asyncpg.connect(dsn=scratch_dsn)
        try:
            for schema in SCHEMAS:
                count = await connection.fetchval(
                    "SELECT count(*) FROM information_schema.tables WHERE table_schema = $1",
                    schema,
                )
                print(f"  {schema}: {count} table(s)")
                if count == 0:
                    print(f"restore verification FAILED: {schema} is empty")
                    return 1
            # The audit trigger must survive a restore, or history becomes editable.
            triggers = await connection.fetchval(
                "SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'audit' AND NOT t.tgisinternal"
            )
            if triggers < 2:
                print("restore verification FAILED: audit guard triggers are missing")
                return 1
            print("restore verification OK")
            return 0
        finally:
            await connection.close()
    finally:
        await admin.execute(f'DROP DATABASE IF EXISTS "{scratch}" WITH (FORCE)')
        await admin.close()


def _replace_database(dsn: str, database: str) -> str:
    head, _, tail = dsn.rpartition("/")
    query = ""
    if "?" in tail:
        tail, _, query = tail.partition("?")
        query = f"?{query}"
    return f"{head}/{database}{query}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    backup_parser = sub.add_parser("backup", help="write a compressed logical dump")
    backup_parser.add_argument("--dsn", required=True)
    backup_parser.add_argument("--out", required=True, type=Path)

    verify_parser = sub.add_parser("verify", help="restore a dump and check integrity")
    verify_parser.add_argument("--admin-dsn", required=True)
    verify_parser.add_argument("--dump", required=True, type=Path)

    args = parser.parse_args()
    if args.command == "backup":
        backup(args.dsn, args.out)
        return 0
    return asyncio.run(verify(args.admin_dsn, args.dump))


if __name__ == "__main__":
    raise SystemExit(main())
