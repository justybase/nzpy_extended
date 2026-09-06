-- Weekly reference mart for the Sales Decision Cockpit.
--
-- The application consumes SDC_FACT_WEEKLY_SCORECARD and
-- SDC_FACT_WEEKLY_TARGET. In a source system the first two tables below
-- would normally be built by ETL from event and target sources.

CREATE TABLE SDC_WRK_WEEKLY_SALES AS
SELECT
    DATE_TRUNC('week', f.activity_date)::DATE AS week_start,
    f.unit_id,
    f.seller_id,
    f.product_id,
    f.channel_id,
    COUNT(*) AS sales_count,
    SUM(f.amount) AS sales_amount,
    SUM(f.margin_amount) AS margin_amount,
    SUM(f.leads) AS leads,
    SUM(f.qualified_leads) AS qualified_leads,
    SUM(f.wins) AS wins,
    SUM(f.pipeline_value) AS pipeline_value,
    SUM(f.pipeline_value * f.stage_probability) AS weighted_pipeline_value,
    SUM(f.cancelled_amount) AS cancelled_amount
FROM SOURCE_SALES_ACTIVITY f
GROUP BY 1, 2, 3, 4, 5
DISTRIBUTE ON RANDOM;

-- Target planning is kept at the same dimensional grain as the scorecard so
-- the driver bridge is additive: sum(actual - target) by any supported
-- dimension is exactly the selected scope's actual - target.
CREATE TABLE SDC_WRK_WEEKLY_TARGET AS
SELECT
    DATE_TRUNC('week', p.target_date)::DATE AS week_start,
    p.unit_id,
    p.seller_id,
    p.product_id,
    p.channel_id,
    SUM(p.target_count) AS target_count,
    SUM(p.target_amount) AS target_amount,
    SUM(p.target_margin) AS target_margin
FROM SOURCE_SALES_TARGET p
GROUP BY 1, 2, 3, 4, 5
DISTRIBUTE ON RANDOM;

-- Publish the two work tables into the fact tables consumed by the API before
-- evaluating the audit gate. In production these statements run in the ETL
-- transaction after the final-table schema has been provisioned.
DELETE FROM SDC_FACT_WEEKLY_SCORECARD;
INSERT INTO SDC_FACT_WEEKLY_SCORECARD (
    week_start, unit_id, seller_id, product_id, channel_id,
    sales_count, sales_amount, margin_amount, leads, qualified_leads, wins,
    pipeline_value, weighted_pipeline_value, cancelled_amount)
SELECT
    week_start, unit_id, seller_id, product_id, channel_id,
    sales_count, sales_amount, margin_amount, leads, qualified_leads, wins,
    pipeline_value, weighted_pipeline_value, cancelled_amount
FROM SDC_WRK_WEEKLY_SALES;

DELETE FROM SDC_FACT_WEEKLY_TARGET;
INSERT INTO SDC_FACT_WEEKLY_TARGET (
    week_start, unit_id, seller_id, product_id, channel_id,
    target_count, target_amount, target_margin)
SELECT
    week_start, unit_id, seller_id, product_id, channel_id,
    target_count, target_amount, target_margin
FROM SDC_WRK_WEEKLY_TARGET;

-- The publication gate is evaluated before the control record is advanced.
-- The API should serve only a generation whose actual amount reconciles with
-- the published mart amount.
INSERT INTO SDC_AUDIT_DATASET_LOAD (
    snapshot_week, load_id, source_max_date, loaded_at,
    source_actual_amount, mart_actual_amount, difference_amount, status)
SELECT
    s.week_start,
    'SDC-' || REPLACE(s.week_start::VARCHAR(10), '-', ''),
    s.week_start + 6,
    CURRENT_TIMESTAMP,
    s.source_actual_amount,
    m.mart_actual_amount,
    m.mart_actual_amount - s.source_actual_amount,
    CASE WHEN m.mart_actual_amount - s.source_actual_amount = 0
         THEN 'PASS' ELSE 'FAIL' END
FROM (
    SELECT DATE_TRUNC('week', activity_date)::DATE AS week_start,
           SUM(amount) AS source_actual_amount
    FROM SOURCE_SALES_ACTIVITY
    GROUP BY 1
) s
JOIN (
    SELECT week_start, SUM(sales_amount) AS mart_actual_amount
    FROM SDC_FACT_WEEKLY_SCORECARD
    GROUP BY 1
) m ON m.week_start = s.week_start;

-- Only after the audit rows pass should the ETL publish the new version.
INSERT INTO SDC_CONTROL_DATASET_LOAD (
    dataset_name, version_no, load_id, source_watermark,
    published_at, status, row_count)
SELECT
    'SALES_DECISION_COCKPIT',
    1,
    'SDC-LATEST',
    MAX(snapshot_week),
    CURRENT_TIMESTAMP,
    CASE WHEN MIN(status) = 'PASS' AND MAX(status) = 'PASS'
         THEN 'PUBLISHED' ELSE 'BLOCKED' END,
    (SELECT COUNT(*) FROM SDC_FACT_WEEKLY_SCORECARD)
FROM SDC_AUDIT_DATASET_LOAD;
