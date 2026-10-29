"""Anonymised institution-level analytics with cohort suppression.

Reporting reads only counts and ratios; it never sees a pseudonymous id. Small
cohorts are suppressed before a measure is stored or returned, because an
aggregate over a handful of students can re-identify them (DL-9, NFR-PRV-01).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.core.config import Settings, get_settings
from app.db.access import TenantScopedStore

logger = logging.getLogger(__name__)

MEASURE_TYPES = ("equity", "compliance", "mastery", "device_parity")

_UPSERT = """
INSERT INTO analytics.measures (
    institution_id, measure_type, dimension, dimension_value, period,
    cohort_size, numerator, denominator, value
) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
ON CONFLICT (institution_id, measure_type, dimension, dimension_value, period)
DO UPDATE SET cohort_size = EXCLUDED.cohort_size,
              numerator = EXCLUDED.numerator,
              denominator = EXCLUDED.denominator,
              value = EXCLUDED.value,
              computed_at = now()
RETURNING measure_id
"""


class CohortTooSmallError(ValueError):
    """Raised when an aggregate would expose an identifiable small group."""


@dataclass(frozen=True)
class Measure:
    measure_type: str
    dimension: str
    dimension_value: str
    period: str
    cohort_size: int
    numerator: int
    denominator: int
    value: float | None


class AnalyticsStore:
    """Write and read institution-level measures, suppressing small cohorts."""

    def __init__(self, store: TenantScopedStore, settings: Settings | None = None) -> None:
        self.store = store
        self.settings = settings or get_settings()

    def _suppress_small_cohort(self, cohort_size: int) -> None:
        if cohort_size < self.settings.analytics_min_cohort_size:
            raise CohortTooSmallError(
                "cohort is below the minimum size and must not be released"
            )

    async def record(self, measure: Measure) -> int:
        if measure.measure_type not in MEASURE_TYPES:
            raise ValueError(f"unsupported measure type: {measure.measure_type}")
        self._suppress_small_cohort(measure.cohort_size)
        if measure.numerator > measure.denominator:
            raise ValueError("numerator must not exceed denominator")
        return int(
            await self.store.fetchval(
                _UPSERT,
                self.store.institution_id,
                measure.measure_type,
                measure.dimension,
                measure.dimension_value,
                measure.period,
                measure.cohort_size,
                measure.numerator,
                measure.denominator,
                measure.value,
            )
        )

    async def record_batch(self, measures: Sequence[Measure]) -> int:
        written = 0
        for measure in measures:
            await self.record(measure)
            written += 1
        return written

    async def read(
        self, measure_type: str, period: str, *, dimension: str | None = None
    ) -> list[Mapping[str, Any]]:
        """Return stored measures, still applying cohort suppression on read."""
        query = """
            SELECT measure_type, dimension, dimension_value, period, cohort_size,
                   numerator, denominator, value
            FROM analytics.measures
            WHERE institution_id = $1 AND measure_type = $2 AND period = $3
              AND cohort_size >= $4
        """
        args: list[Any] = [
            self.store.institution_id,
            measure_type,
            period,
            self.settings.analytics_min_cohort_size,
        ]
        if dimension is not None:
            query += " AND dimension = $5"
            args.append(dimension)
        query += " ORDER BY dimension, dimension_value"
        rows = await self.store.fetch(query, *args)
        return [dict(row) for row in rows]
