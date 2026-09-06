-- Netezza SQL reference: daily accumulating MIS snapshot
--
-- In a source system this parameter is often named SPNPT_DATE.  The demo
-- uses the clearer snapshot_date column; the canonical example is
-- SPNPT_DATE = DATE '2026-08-15' (activity 1–15 August only).
--
-- The demo seed loads the same grain and additive measures with
-- nzpy_extended.load_data; its Python reference also computes the distinct
-- customer measure used below. In a real batch this CTAS is run after the
-- source watermark is closed (replace the example date with that watermark).

CREATE TABLE MIS_WRK_SALES_DAILY AS
SELECT
    f.sale_date AS activity_date,
    DATE_TRUNC('month', f.sale_date)::DATE AS month_start,
    f.advisor_id,
    h.branch_id AS historical_branch_id,
    h.region_id AS historical_region_id,
    SUM(CASE WHEN f.sale_status = 'BOOKED' THEN 1 ELSE 0 END) AS sales_count_day,
    SUM(CASE WHEN f.sale_status = 'BOOKED' THEN f.amount ELSE 0 END) AS sales_amount_day,
    SUM(CASE WHEN f.sale_status = 'BOOKED' THEN f.commission ELSE 0 END) AS commission_day,
    SUM(CASE WHEN f.sale_status = 'CANCELLED' THEN 1 ELSE 0 END) AS cancellations_day,
    SUM(CASE WHEN f.sale_status = 'CANCELLED' THEN f.amount ELSE 0 END) AS cancellation_amount_day,
    COUNT(DISTINCT CASE WHEN f.sale_status = 'BOOKED' THEN f.customer_id END) AS customers_day
FROM MIS_FACT_SALES f
JOIN MIS_DIM_ORG_ASSIGNMENT h
  ON h.advisor_id = f.advisor_id
 AND f.sale_date BETWEEN h.valid_from AND h.valid_to
GROUP BY 1, 2, 3, 4, 5
DISTRIBUTE ON (advisor_id);

-- COUNT(DISTINCT) is intentionally kept out of the MTD window.  Materialise
-- the first booked activity for each customer/entity, then count those rows
-- at every later calendar date.  This is portable to Netezza and makes the
-- distinct-customer definition explicit instead of summing daily distincts.
CREATE TABLE MIS_WRK_CUSTOMER_FIRST AS
SELECT
    DATE_TRUNC('month', f.sale_date)::DATE AS month_start,
    f.advisor_id,
    h.branch_id AS historical_branch_id,
    h.region_id AS historical_region_id,
    f.customer_id,
    MIN(f.sale_date) AS first_activity_date
FROM MIS_FACT_SALES f
JOIN MIS_DIM_ORG_ASSIGNMENT h
  ON h.advisor_id = f.advisor_id
 AND f.sale_date BETWEEN h.valid_from AND h.valid_to
WHERE f.sale_status = 'BOOKED'
GROUP BY 1, 2, 3, 4, 5;

CREATE TABLE MIS_WRK_CUSTOMER_MTD AS
SELECT
    d.calendar_date AS snapshot_date,
    c.month_start,
    c.advisor_id,
    c.historical_branch_id,
    c.historical_region_id,
    COUNT(*) AS customers_mtd
FROM MIS_DIM_DATE d
JOIN MIS_WRK_CUSTOMER_FIRST c
  ON c.month_start = d.month_start
 AND c.first_activity_date <= d.calendar_date
GROUP BY 1, 2, 3, 4, 5
DISTRIBUTE ON (snapshot_date);

-- The calendar join emits every snapshot day after an entity's first activity
-- in the month. Window frames provide additive MTD measures. Distinct MTD
-- customers are normally prepared as a separate advisor/month/date aggregate
-- because COUNT(DISTINCT) is not portable inside a Netezza window frame.
CREATE TABLE MIS_FACT_PERFORMANCE_SNAPSHOT AS
SELECT
    d.calendar_date AS snapshot_date,
    e.month_start,
    e.advisor_id,
    e.historical_branch_id,
    e.historical_region_id,
    COALESCE(x.sales_count_day, 0) AS sales_count_day,
    COALESCE(x.sales_amount_day, 0) AS sales_amount_day,
    COALESCE(x.commission_day, 0) AS commission_day,
    COALESCE(x.cancellations_day, 0) AS cancellations_day,
    COALESCE(x.cancellation_amount_day, 0) AS cancellation_amount_day,
    COALESCE(x.customers_day, 0) AS customers_day,
    SUM(COALESCE(x.sales_count_day, 0)) OVER (
        PARTITION BY e.month_start, e.advisor_id, e.historical_branch_id
        ORDER BY d.calendar_date ROWS UNBOUNDED PRECEDING) AS sales_count_mtd,
    SUM(COALESCE(x.sales_amount_day, 0)) OVER (
        PARTITION BY e.month_start, e.advisor_id, e.historical_branch_id
        ORDER BY d.calendar_date ROWS UNBOUNDED PRECEDING) AS sales_amount_mtd,
    SUM(COALESCE(x.commission_day, 0)) OVER (
        PARTITION BY e.month_start, e.advisor_id, e.historical_branch_id
        ORDER BY d.calendar_date ROWS UNBOUNDED PRECEDING) AS commission_mtd,
    SUM(COALESCE(x.cancellations_day, 0)) OVER (
        PARTITION BY e.month_start, e.advisor_id, e.historical_branch_id
        ORDER BY d.calendar_date ROWS UNBOUNDED PRECEDING) AS cancellations_mtd,
    SUM(COALESCE(x.cancellation_amount_day, 0)) OVER (
        PARTITION BY e.month_start, e.advisor_id, e.historical_branch_id
        ORDER BY d.calendar_date ROWS UNBOUNDED PRECEDING) AS cancellation_amount_mtd,
    COALESCE(cm.customers_mtd, 0) AS customers_mtd,
    p.plan_amount,
    p.quality_score,
    p.activity_score
FROM MIS_DIM_DATE d
JOIN (
    SELECT DISTINCT month_start, advisor_id, historical_branch_id, historical_region_id
    FROM MIS_WRK_SALES_DAILY
) e ON d.month_start = e.month_start
LEFT JOIN MIS_WRK_SALES_DAILY x
  ON x.activity_date = d.calendar_date
 AND x.advisor_id = e.advisor_id
 AND x.historical_branch_id = e.historical_branch_id
LEFT JOIN MIS_FACT_ADVISOR_PERF p
  ON p.advisor_id = e.advisor_id
 AND p.perf_month = e.month_start
LEFT JOIN MIS_WRK_CUSTOMER_MTD cm
  ON cm.snapshot_date = d.calendar_date
 AND cm.month_start = e.month_start
 AND cm.advisor_id = e.advisor_id
 AND cm.historical_branch_id = e.historical_branch_id
 AND cm.historical_region_id = e.historical_region_id
WHERE d.calendar_date <= DATE '2026-08-15'
DISTRIBUTE ON (snapshot_date);

-- Mandatory batch gate. Persist the evidence before publishing the snapshot:
-- the API only serves rows whose audit status is PASS and whose source
-- watermark equals the requested SPNPT_DATE.  A failed insert/load therefore
-- remains visible to data-quality tooling but cannot be selected by users.
DELETE FROM MIS_AUDIT_SNAPSHOT_LOAD
WHERE snapshot_date = DATE '2026-08-15';

INSERT INTO MIS_AUDIT_SNAPSHOT_LOAD (
    load_id, snapshot_date, loaded_at, source_max_date, snapshot_rows,
    source_booked_amount, mart_booked_amount, difference_amount, status)
SELECT
    'SNAP-20260815',
    DATE '2026-08-15',
    '2026-08-15T06:00:00',
    DATE '2026-08-15',
    m.snapshot_rows,
    r.source_amount,
    m.mart_amount,
    m.mart_amount - r.source_amount,
    CASE WHEN m.mart_amount - r.source_amount = 0 THEN 'PASS' ELSE 'FAIL' END
FROM (
    SELECT COUNT(*) AS snapshot_rows,
           COALESCE(SUM(sales_amount_mtd), 0) AS mart_amount
    FROM MIS_FACT_PERFORMANCE_SNAPSHOT
    WHERE snapshot_date = DATE '2026-08-15'
) m
CROSS JOIN (
    SELECT COALESCE(SUM(amount), 0) AS source_amount
    FROM MIS_FACT_SALES
    WHERE sale_status = 'BOOKED'
      AND sale_date BETWEEN DATE '2026-08-01' AND DATE '2026-08-15'
) r;

SELECT snapshot_date, snapshot_rows, mart_booked_amount,
       source_booked_amount, difference_amount, status
FROM MIS_AUDIT_SNAPSHOT_LOAD
WHERE snapshot_date = DATE '2026-08-15';
