"""Retention and archival jobs driven by institutional policy.

Retention periods are configurable per institution (BRL-10) and default to the
validated settings. The job archives academic structure per term and purges
process data and assessment records once their window has passed. Audit records
are never purged by the same path as ordinary data: they carry a separate, long
retention and are only trimmed on their own schedule.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from app.core.config import Settings, get_settings
from app.db.access import TenantScopedStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RetentionPolicy:
    """Per-institution retention windows in days."""

    process_data_days: int = 365
    assessment_days: int = 1_825
    audit_days: int = 2_555

    def __post_init__(self) -> None:
        for name, value in (
            ("process_data_days", self.process_data_days),
            ("assessment_days", self.assessment_days),
            ("audit_days", self.audit_days),
        ):
            if value < 1:
                raise ValueError(f"{name} must be at least one day")

    @classmethod
    def from_settings(cls, settings: Settings) -> "RetentionPolicy":
        return cls(
            process_data_days=settings.process_data_retention_days,
            assessment_days=settings.assessment_retention_days,
            audit_days=settings.audit_retention_days,
        )


@dataclass
class RetentionReport:
    institution_id: str
    cutoff: str
    purged: dict[str, int] = field(default_factory=dict)

    @property
    def total_purged(self) -> int:
        return sum(self.purged.values())


class RetentionJob:
    """Purge expired process and assessment data for one institution."""

    def __init__(
        self,
        store: TenantScopedStore,
        policy: RetentionPolicy | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.store = store
        settings = settings or get_settings()
        self.policy = policy or RetentionPolicy.from_settings(settings)

    async def run(self, *, now: datetime | None = None) -> RetentionReport:
        now = now or datetime.now(UTC)
        report = RetentionReport(
            institution_id=self.store.institution_id, cutoff=now.isoformat()
        )
        report.purged["process_logs"] = await self._purge(
            "assessment.process_logs", now - timedelta(days=self.policy.process_data_days)
        )
        report.purged["viva_sessions"] = await self._purge(
            "assessment.viva_sessions", now - timedelta(days=self.policy.assessment_days),
            column="created_at",
        )
        report.purged["evaluations"] = await self._purge(
            "assessment.evaluations", now - timedelta(days=self.policy.assessment_days),
            column="created_at",
        )
        report.purged["submissions"] = await self._purge(
            "assessment.submissions", now - timedelta(days=self.policy.assessment_days),
            column="submitted_at",
        )
        logger.info(
            "retention job purged %s rows for %s",
            report.total_purged,
            self.store.institution_id,
        )
        return report

    async def _purge(
        self, table: str, cutoff: datetime, *, column: str = "recorded_at"
    ) -> int:
        # table and column are fixed literals from this module, never caller input.
        status = await self.store.execute(
            f"DELETE FROM {table} WHERE institution_id = $1 AND {column} < $2",
            self.store.institution_id,
            cutoff,
        )
        return int(status.split()[-1]) if status else 0
