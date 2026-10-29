"""Bridge Layer 3A policy decisions to the durable, append-only audit store.

``PolicyGuardEngine`` evaluates policy synchronously and refuses to return a
decision whose audit event could not be recorded. The audit store is async, so
this adapter owns a dedicated thread with its own event loop and its own pool,
and blocks the caller until the row is committed. Blocking is the point: the
decision is not released until the evidence is durable (NFR-SEC-07, BR-19).
"""

from __future__ import annotations

import asyncio
import logging
import threading
from concurrent.futures import Future
from typing import Any, Callable

from app.core.config import Settings, get_settings
from app.db.access import TenantScopedStore
from app.db.audit import AuditStore
from app.db.postgres import PostgresStore

logger = logging.getLogger(__name__)


class DurableAuditSink:
    """Append ``PolicyAuditEvent`` objects to the audit store from sync code.

    Usage::

        sink = DurableAuditSink(institution_id)
        guard = PolicyGuardEngine(policy, tiers, audit_hook=sink.hook)
        ...
        sink.close()

    The sink is bound to one institution for its lifetime.
    """

    def __init__(
        self,
        institution_id: str,
        *,
        dsn: str | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.institution_id = institution_id
        self.settings = settings or get_settings()
        if dsn is None:
            secret = self.settings.postgres_dsn
            dsn = secret.get_secret_value() if secret is not None else None
        if not dsn:
            raise RuntimeError("POSTGRES_DSN is required for the durable audit sink")
        self._dsn = dsn
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._store: PostgresStore | None = None
        self._ready = threading.Event()
        self._startup_error: BaseException | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="audit-sink", daemon=True)
        self._thread.start()
        self._ready.wait()
        if self._startup_error is not None:
            raise RuntimeError("audit sink failed to start") from self._startup_error

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            store = PostgresStore(self.settings, dsn=self._dsn)
            loop.run_until_complete(store.connect())
            self._store = store
        except BaseException as exc:  # pragma: no cover - startup failure path
            self._startup_error = exc
            self._ready.set()
            return
        self._ready.set()
        loop.run_forever()

    def _submit(self, coro) -> Any:
        self.start()
        assert self._loop is not None
        future: Future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result()

    def hook(self, event: Any) -> None:
        """Synchronous audit hook for ``PolicyGuardEngine``."""
        self.start()
        if self._store is None:
            raise RuntimeError("audit sink is not running")
        scope = TenantScopedStore(self._store, self.institution_id)
        audit = AuditStore(scope)
        self._submit(
            audit.append(
                event_type="policy_decision",
                course_id=getattr(event, "course_id", None),
                assignment_id=getattr(event, "assignment_id", None),
                policy_version=getattr(event, "policy_version", None),
                policy_level=getattr(event, "policy_level", None),
                allowed=getattr(event, "allowed", None),
                reason=getattr(event, "reason", None),
                prompt_sha256=getattr(event, "prompt_sha256", None),
                payload={"log_required": bool(getattr(event, "log_required", False))},
            )
        )

    def close(self) -> None:
        if self._loop is None:
            return
        if self._store is not None:
            asyncio.run_coroutine_threadsafe(self._store.close(), self._loop).result()
        self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=10)
        self._loop = None
        self._thread = None
        self._store = None

    def __enter__(self) -> "DurableAuditSink":
        self.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
