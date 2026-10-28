-- 0006: Aggregated analytics store - anonymised institution-level measures.
--
-- Only counts and ratios reach this schema, so reporting cannot leak personal
-- data by construction (Project Report §10). Aggregates are written from the
-- core and assessment schemas by the aggregation jobs; nothing here references a
-- pseudonymous id. A minimum cohort size is enforced before a measure is
-- released (DL-9).

CREATE TABLE IF NOT EXISTS analytics.measures (
    measure_id      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    institution_id  text NOT NULL,
    measure_type    text NOT NULL CHECK (measure_type IN (
                        'equity', 'compliance', 'mastery', 'device_parity'
                    )),
    dimension       text NOT NULL,
    dimension_value text NOT NULL,
    period          text NOT NULL,
    cohort_size     integer NOT NULL CHECK (cohort_size >= 0),
    numerator       bigint NOT NULL DEFAULT 0 CHECK (numerator >= 0),
    denominator     bigint NOT NULL DEFAULT 0 CHECK (denominator >= 0),
    value           numeric(12, 6),
    computed_at     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (institution_id, measure_type, dimension, dimension_value, period),
    CONSTRAINT measures_numerator_within_denominator CHECK (numerator <= denominator),
    CONSTRAINT measures_value_range CHECK (value IS NULL OR (value >= 0 AND value <= 1))
);
CREATE INDEX IF NOT EXISTS measures_lookup_idx
    ON analytics.measures (institution_id, measure_type, period);

ALTER TABLE analytics.measures ENABLE ROW LEVEL SECURITY;
ALTER TABLE analytics.measures FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_isolation ON analytics.measures;
CREATE POLICY tenant_isolation ON analytics.measures
    USING (institution_id = current_setting('app.institution_id', true))
    WITH CHECK (institution_id = current_setting('app.institution_id', true));
