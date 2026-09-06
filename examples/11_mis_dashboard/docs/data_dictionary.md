# MIS dashboard data dictionary

This document describes the 18 tables created by `seed.py` for the
`JUST_DATA` demo schema. The model is a sales-network star schema with a
small type-2 organization history and an accumulating daily performance mart.

## Conventions

- `PK` is the primary key declared in `seed.py`.
- `UK` is a unique business key declared in `seed.py`.
- `FK` names the referenced table and column(s).
- A fact table's grain is stated explicitly; measures must not be summed
  across a dimension that is already part of that grain.
- Netezza stores PK/FK metadata but does not enforce referential integrity.
  The seed generators therefore create parent rows first and the data load
  order must remain consistent with the relationships below.

## Relationship summary

| Child table | FK column(s) | Parent table | Parent key |
|---|---|---|---|
| `MIS_DIM_BRANCH` | `region_id` | `MIS_DIM_REGION` | `region_id` |
| `MIS_DIM_ADVISOR` | `branch_id` | `MIS_DIM_BRANCH` | `branch_id` |
| `MIS_DIM_CAMPAIGN` | `product_id` | `MIS_DIM_PRODUCT` | `product_id` |
| `MIS_DIM_CAMPAIGN` | `start_date`, `end_date` | `MIS_DIM_DATE` | `calendar_date` |
| `MIS_DIM_ORG_ASSIGNMENT` | `advisor_id` | `MIS_DIM_ADVISOR` | `advisor_id` |
| `MIS_DIM_ORG_ASSIGNMENT` | `branch_id` | `MIS_DIM_BRANCH` | `branch_id` |
| `MIS_DIM_ORG_ASSIGNMENT` | `region_id` | `MIS_DIM_REGION` | `region_id` |
| `MIS_FACT_SALES` | `branch_id` | `MIS_DIM_BRANCH` | `branch_id` |
| `MIS_FACT_SALES` | `advisor_id` | `MIS_DIM_ADVISOR` | `advisor_id` |
| `MIS_FACT_SALES` | `product_id` | `MIS_DIM_PRODUCT` | `product_id` |
| `MIS_FACT_SALES` | `channel_id` | `MIS_DIM_CHANNEL` | `channel_id` |
| `MIS_FACT_SALES` | `sale_date` | `MIS_DIM_DATE` | `calendar_date` |
| `MIS_FACT_BALANCES` | `branch_id` | `MIS_DIM_BRANCH` | `branch_id` |
| `MIS_FACT_BALANCES` | `product_id` | `MIS_DIM_PRODUCT` | `product_id` |
| `MIS_FACT_BALANCES` | `balance_month` | `MIS_DIM_DATE` | `calendar_date` |
| `MIS_FACT_CUSTOMER_MOVEMENT` | `branch_id` | `MIS_DIM_BRANCH` | `branch_id` |
| `MIS_FACT_CUSTOMER_MOVEMENT` | `movement_month` | `MIS_DIM_DATE` | `calendar_date` |
| `MIS_FACT_CAMPAIGN_RESULTS` | `campaign_id` | `MIS_DIM_CAMPAIGN` | `campaign_id` |
| `MIS_FACT_CAMPAIGN_RESULTS` | `branch_id` | `MIS_DIM_BRANCH` | `branch_id` |
| `MIS_FACT_BRANCH_PLAN` | `branch_id` | `MIS_DIM_BRANCH` | `branch_id` |
| `MIS_FACT_BRANCH_PLAN` | `plan_month` | `MIS_DIM_DATE` | `calendar_date` |
| `MIS_FACT_ADVISOR_PERF` | `advisor_id` | `MIS_DIM_ADVISOR` | `advisor_id` |
| `MIS_FACT_ADVISOR_PERF` | `perf_month` | `MIS_DIM_DATE` | `calendar_date` |
| `MIS_DIM_USER` | `advisor_id` | `MIS_DIM_ADVISOR` | `advisor_id` |
| `MIS_DIM_USER` | `branch_id` | `MIS_DIM_BRANCH` | `branch_id` |
| `MIS_DIM_USER` | `region_id` | `MIS_DIM_REGION` | `region_id` |
| `MIS_FACT_PERFORMANCE_SNAPSHOT` | `snapshot_date`, `month_start` | `MIS_DIM_DATE` | `calendar_date` |
| `MIS_FACT_PERFORMANCE_SNAPSHOT` | `advisor_id` | `MIS_DIM_ADVISOR` | `advisor_id` |
| `MIS_FACT_PERFORMANCE_SNAPSHOT` | `historical_branch_id` | `MIS_DIM_BRANCH` | `branch_id` |
| `MIS_FACT_PERFORMANCE_SNAPSHOT` | `historical_region_id` | `MIS_DIM_REGION` | `region_id` |
| `MIS_FACT_PERFORMANCE_SNAPSHOT` | `advisor_id`, `month_start` | `MIS_FACT_ADVISOR_PERF` | `advisor_id`, `perf_month` |
| `MIS_AUDIT_SNAPSHOT_LOAD` | `snapshot_date`, `source_max_date` | `MIS_DIM_DATE` | `calendar_date` |

## Tables

### `MIS_DIM_REGION`

One row per sales region.

- **PK:** `region_id`
- **UK:** `region_code`

| Column | Type | Description |
|---|---|---|
| `region_id` | `INTEGER` | Surrogate region identifier. |
| `region_code` | `NVARCHAR(10)` | Stable business code, for example `RNOR`. |
| `region_name` | `NVARCHAR(60)` | Display name of the region. |
| `area_name` | `NVARCHAR(60)` | Area or management label. |
| `seat_city` | `NVARCHAR(40)` | Main city for the region. |

### `MIS_DIM_BRANCH`

One row per branch. A branch belongs to exactly one region in the current
dimension.

- **PK:** `branch_id`
- **UK:** `branch_code`
- **FK:** `region_id → MIS_DIM_REGION.region_id`

| Column | Type | Description |
|---|---|---|
| `branch_id` | `INTEGER` | Surrogate branch identifier. |
| `branch_code` | `NVARCHAR(10)` | Stable branch code, for example `BEL01`. |
| `branch_name` | `NVARCHAR(80)` | Display name. |
| `region_id` | `INTEGER` | Current owning region. |
| `city` | `NVARCHAR(40)` | Branch city. |
| `district` | `NVARCHAR(60)` | City district or locality. |
| `open_date` | `DATE` | Branch opening date. |
| `status` | `NVARCHAR(10)` | Current branch status. |

### `MIS_DIM_ADVISOR`

One row per advisor in the current organization view.

- **PK:** `advisor_id`
- **UK:** `advisor_code`
- **FK:** `branch_id → MIS_DIM_BRANCH.branch_id`

| Column | Type | Description |
|---|---|---|
| `advisor_id` | `INTEGER` | Surrogate advisor identifier. |
| `advisor_code` | `NVARCHAR(10)` | Stable advisor code, for example `P0037`. |
| `first_name` | `NVARCHAR(40)` | First name. |
| `last_name` | `NVARCHAR(60)` | Last name. |
| `branch_id` | `INTEGER` | Current branch assignment. |
| `role` | `NVARCHAR(40)` | Advisor role or grade. |
| `hire_date` | `DATE` | Hire date. |
| `status` | `NVARCHAR(10)` | Active or inactive status. |

### `MIS_DIM_PRODUCT`

One row per product offered by the network.

- **PK:** `product_id`
- **UK:** `product_code`

| Column | Type | Description |
|---|---|---|
| `product_id` | `INTEGER` | Surrogate product identifier. |
| `product_code` | `NVARCHAR(20)` | Stable product code. |
| `product_name` | `NVARCHAR(80)` | Product display name. |
| `product_group` | `NVARCHAR(40)` | Reporting group, such as Loans or Investments. |
| `product_subgroup` | `NVARCHAR(60)` | More detailed product grouping. |
| `is_balance_product` | `SMALLINT` | 1 when the product contributes to balance reporting. |
| `min_amount` | `NUMERIC(14,2)` | Lower bound used by the deterministic generator. |
| `max_amount` | `NUMERIC(14,2)` | Upper bound used by the deterministic generator. |
| `commission_rate` | `NUMERIC(6,4)` | Percentage/rate used for variable commission products. |

### `MIS_DIM_CHANNEL`

One row per sales channel.

- **PK:** `channel_id`
- **UK:** `channel_name`

| Column | Type | Description |
|---|---|---|
| `channel_id` | `INTEGER` | Surrogate channel identifier. |
| `channel_name` | `NVARCHAR(40)` | Channel display name. |

### `MIS_DIM_CAMPAIGN`

One row per marketing campaign.

- **PK:** `campaign_id`
- **UK:** `campaign_code`
- **FKs:** `product_id → MIS_DIM_PRODUCT.product_id` (nullable for a campaign without one product), `start_date/end_date → MIS_DIM_DATE.calendar_date`

| Column | Type | Description |
|---|---|---|
| `campaign_id` | `INTEGER` | Surrogate campaign identifier. |
| `campaign_code` | `NVARCHAR(20)` | Stable campaign code. |
| `campaign_name` | `NVARCHAR(120)` | Campaign display name. |
| `campaign_type` | `NVARCHAR(40)` | Campaign type. |
| `target_segment` | `NVARCHAR(60)` | Intended customer segment. |
| `product_id` | `INTEGER` | Product promoted by the campaign. |
| `start_date` | `DATE` | Campaign start date. |
| `end_date` | `DATE` | Campaign end/publication date. |
| `budget` | `NUMERIC(14,2)` | Generated campaign budget. |

### `MIS_DIM_DATE`

One row per calendar date from `2024-01-01` through the canonical cut-off
`2026-08-15`. It is the reporting calendar used for point-in-time snapshots
and business-day pacing.

- **PK:** `calendar_date`

| Column | Type | Description |
|---|---|---|
| `calendar_date` | `DATE` | Calendar date. |
| `month_start` | `DATE` | First day of the date's month. |
| `month_end` | `DATE` | Last day of the date's month. |
| `day_of_month` | `SMALLINT` | Calendar day number. |
| `is_business_day` | `SMALLINT` | 1 for Monday-Friday, otherwise 0. |
| `business_day_of_month` | `SMALLINT` | Number of business days elapsed in the month. |
| `business_days_in_month` | `SMALLINT` | Total business days in the month. |

### `MIS_DIM_ORG_ASSIGNMENT`

Type-2 history of advisor-to-branch-and-region assignments. The valid interval
is inclusive at both ends; `9999-12-31` represents an open-ended assignment.

- **PK:** `org_assignment_sk`
- **UK:** (`advisor_id`, `valid_from`)
- **FKs:** `advisor_id → MIS_DIM_ADVISOR`, `branch_id → MIS_DIM_BRANCH`, `region_id → MIS_DIM_REGION`

| Column | Type | Description |
|---|---|---|
| `org_assignment_sk` | `INTEGER` | Surrogate assignment identifier. |
| `advisor_id` | `INTEGER` | Advisor whose history is recorded. |
| `branch_id` | `INTEGER` | Branch valid for the interval. |
| `region_id` | `INTEGER` | Region valid for the interval. |
| `valid_from` | `DATE` | Inclusive start of the assignment. |
| `valid_to` | `DATE` | Inclusive end of the assignment. |
| `is_current` | `SMALLINT` | 1 for the current assignment. |
| `change_reason` | `NVARCHAR(60)` | Reason for the assignment version. |

### `MIS_FACT_SALES`

One row per sales event. `branch_id` is the historical branch stamped for the
event date, so transfers do not rewrite historical sales.

- **PK:** `sale_id`
- **FKs:** `sale_date → MIS_DIM_DATE`, `branch_id → MIS_DIM_BRANCH`, `advisor_id → MIS_DIM_ADVISOR`, `product_id → MIS_DIM_PRODUCT`, `channel_id → MIS_DIM_CHANNEL`
- **Note:** `customer_id` is an anonymised identifier and has no customer dimension in this demo.

| Column | Type | Description |
|---|---|---|
| `sale_id` | `INTEGER` | Unique sales event identifier. |
| `sale_date` | `DATE` | Event date. |
| `branch_id` | `INTEGER` | Historical branch for the event. |
| `advisor_id` | `INTEGER` | Advisor recording the event. |
| `product_id` | `INTEGER` | Sold product. |
| `channel_id` | `INTEGER` | Sales channel. |
| `customer_id` | `INTEGER` | Anonymised customer identifier. |
| `amount` | `NUMERIC(14,2)` | Sales amount. |
| `commission` | `NUMERIC(12,2)` | Commission amount. |
| `sale_status` | `NVARCHAR(10)` | `BOOKED` or `CANCELLED`. |

### `MIS_FACT_BALANCES`

One row per month-end, branch and balance-bearing product.

- **PK:** (`balance_month`, `branch_id`, `product_id`)
- **FKs:** `balance_month → MIS_DIM_DATE`, `branch_id → MIS_DIM_BRANCH`, `product_id → MIS_DIM_PRODUCT`

| Column | Type | Description |
|---|---|---|
| `balance_month` | `DATE` | Month represented by the balance snapshot. |
| `branch_id` | `INTEGER` | Branch. |
| `product_id` | `INTEGER` | Balance-bearing product. |
| `account_count` | `INTEGER` | Number of accounts. |
| `balance_amount` | `NUMERIC(16,2)` | Total balance amount. |

### `MIS_FACT_CUSTOMER_MOVEMENT`

One row per branch and month for acquisition, churn and product penetration.

- **PK:** (`movement_month`, `branch_id`)
- **FKs:** `movement_month → MIS_DIM_DATE.calendar_date`, `branch_id → MIS_DIM_BRANCH.branch_id`

| Column | Type | Description |
|---|---|---|
| `movement_month` | `DATE` | Month represented by the movement record. |
| `branch_id` | `INTEGER` | Branch. |
| `customers_start` | `INTEGER` | Customers at the start of the month. |
| `customers_new` | `INTEGER` | New customers. |
| `customers_lost` | `INTEGER` | Lost customers. |
| `customers_end` | `INTEGER` | Customers at the end of the month. |
| `product_relations` | `INTEGER` | Total product relationships. |
| `customers_2plus` | `INTEGER` | Customers with at least two products. |
| `customers_3plus` | `INTEGER` | Customers with at least three products. |
| `customers_4plus` | `INTEGER` | Customers with at least four products. |

### `MIS_FACT_CAMPAIGN_RESULTS`

One row per campaign and branch.

- **PK:** (`campaign_id`, `branch_id`)
- **FKs:** `campaign_id → MIS_DIM_CAMPAIGN`, `branch_id → MIS_DIM_BRANCH`

| Column | Type | Description |
|---|---|---|
| `campaign_id` | `INTEGER` | Campaign. |
| `branch_id` | `INTEGER` | Branch receiving the campaign. |
| `contacts` | `INTEGER` | Contacts/reach. |
| `responses` | `INTEGER` | Responses. |
| `conversions` | `INTEGER` | Conversions. |
| `sales_amount` | `NUMERIC(14,2)` | Sales attributed to the campaign. |
| `cost` | `NUMERIC(12,2)` | Campaign cost for the branch. |

### `MIS_FACT_BRANCH_PLAN`

One row per branch and plan month. It is the sum of the advisor plans for the
same branch and month.

- **PK:** (`branch_id`, `plan_month`)
- **FKs:** `branch_id → MIS_DIM_BRANCH.branch_id`, `plan_month → MIS_DIM_DATE.calendar_date`

| Column | Type | Description |
|---|---|---|
| `branch_id` | `INTEGER` | Branch. |
| `plan_month` | `DATE` | First day of the plan month. |
| `plan_amount` | `NUMERIC(14,2)` | Branch sales plan. |

### `MIS_FACT_ADVISOR_PERF`

One row per advisor and performance month. Plans are generated independently
of current actual sales; scores and ratings describe observed performance.

- **PK:** (`advisor_id`, `perf_month`)
- **FKs:** `advisor_id → MIS_DIM_ADVISOR.advisor_id`, `perf_month → MIS_DIM_DATE.calendar_date`

| Column | Type | Description |
|---|---|---|
| `advisor_id` | `INTEGER` | Advisor. |
| `perf_month` | `DATE` | First day of the performance month. |
| `plan_amount` | `NUMERIC(14,2)` | Advisor sales plan. |
| `sales_score` | `INTEGER` | Sales score from 0 to 100. |
| `conversion_score` | `INTEGER` | Conversion score from 0 to 100. |
| `quality_score` | `INTEGER` | Quality score from 0 to 100. |
| `activity_score` | `INTEGER` | Activity score from 0 to 100. |
| `rating` | `SMALLINT` | Overall rating from 1 to 5. |
| `note` | `NVARCHAR(60)` | Human-readable rating note. |

### `MIS_DIM_USER`

One row per dashboard persona. Passwords are not stored in this table; the
in-process fake LDAP directory stores credential hashes and links an
authenticated username to `user_code`.

- **PK:** `user_id`
- **UK:** `user_code`
- **Nullable FKs:** `advisor_id → MIS_DIM_ADVISOR`, `branch_id → MIS_DIM_BRANCH`, `region_id → MIS_DIM_REGION`

| Column | Type | Description |
|---|---|---|
| `user_id` | `INTEGER` | User identifier. |
| `user_code` | `NVARCHAR(20)` | Login/session code. |
| `display_name` | `NVARCHAR(80)` | Name shown in the UI. |
| `role` | `NVARCHAR(20)` | Canonical roles include `MIS_SQL_DEVELOPER`, `NETWORK_HEAD`, `REGIONAL_DIRECTOR`, `BRANCH_DIRECTOR`, `CUSTOMER_ADVISOR`, `HQ_FULL_ACCESS` and `APP_TESTER`; legacy role aliases remain supported. |
| `advisor_id` | `INTEGER` | Advisor scope, populated for advisor personas. |
| `branch_id` | `INTEGER` | Branch scope, populated for branch director and advisor personas. |
| `region_id` | `INTEGER` | Region scope, populated for regional and branch director personas. |

### `MIS_FACT_PERFORMANCE_SNAPSHOT`

One row per snapshot date, advisor and historical branch represented in that
month. Daily measures are additive; MTD measures are cumulative through
`snapshot_date`.

- **PK:** (`snapshot_date`, `advisor_id`, `historical_branch_id`)
- **FKs:** `snapshot_date → MIS_DIM_DATE.calendar_date`, `month_start → MIS_DIM_DATE.calendar_date`, `advisor_id → MIS_DIM_ADVISOR.advisor_id`, `historical_branch_id → MIS_DIM_BRANCH.branch_id`, `historical_region_id → MIS_DIM_REGION.region_id`, (`advisor_id`, `month_start`) → `MIS_FACT_ADVISOR_PERF(advisor_id, perf_month)`

| Column | Type | Description |
|---|---|---|
| `snapshot_date` | `DATE` | Reporting cut-off date. |
| `month_start` | `DATE` | First day of the snapshot month. |
| `advisor_id` | `INTEGER` | Advisor. |
| `historical_branch_id` | `INTEGER` | Branch valid for the underlying activity. |
| `historical_region_id` | `INTEGER` | Region valid for the underlying activity. |
| `sales_count_day` | `INTEGER` | Booked sales on the snapshot date. |
| `sales_amount_day` | `NUMERIC(16,2)` | Booked amount on the snapshot date. |
| `commission_day` | `NUMERIC(14,2)` | Commission on the snapshot date. |
| `cancellations_day` | `INTEGER` | Cancelled sales on the snapshot date. |
| `cancellation_amount_day` | `NUMERIC(16,2)` | Cancelled amount on the snapshot date. |
| `customers_day` | `INTEGER` | Distinct booked customers on the snapshot date. |
| `sales_count_mtd` | `INTEGER` | Booked sales from month start through the snapshot. |
| `sales_amount_mtd` | `NUMERIC(16,2)` | Booked amount from month start through the snapshot. |
| `commission_mtd` | `NUMERIC(14,2)` | Commission from month start through the snapshot. |
| `cancellations_mtd` | `INTEGER` | Cancellations from month start through the snapshot. |
| `cancellation_amount_mtd` | `NUMERIC(16,2)` | Cancelled amount from month start through the snapshot. |
| `customers_mtd` | `INTEGER` | Distinct booked customers in the MTD period. |
| `plan_amount` | `NUMERIC(16,2)` | Advisor plan joined for the snapshot month. |
| `quality_score` | `INTEGER` | Advisor quality score joined for the snapshot month. |
| `activity_score` | `INTEGER` | Advisor activity score joined for the snapshot month. |

### `MIS_AUDIT_SNAPSHOT_LOAD`

One published quality-gate record per snapshot date. A row is `PASS` only when
the source watermark and mart totals reconcile.

- **PK:** `snapshot_date`
- **UK:** `load_id`
- **FKs:** `snapshot_date → MIS_DIM_DATE.calendar_date`, `source_max_date → MIS_DIM_DATE.calendar_date`

| Column | Type | Description |
|---|---|---|
| `load_id` | `NVARCHAR(30)` | Unique load identifier, for example `SNAP-20260815`. |
| `snapshot_date` | `DATE` | Published reporting cut-off. |
| `loaded_at` | `NVARCHAR(30)` | Load timestamp/watermark label. |
| `source_max_date` | `DATE` | Latest source activity date included. |
| `snapshot_rows` | `INTEGER` | Number of rows for the snapshot. |
| `source_booked_amount` | `NUMERIC(18,2)` | Booked source amount. |
| `mart_booked_amount` | `NUMERIC(18,2)` | Booked amount in the mart. |
| `difference_amount` | `NUMERIC(18,2)` | Mart amount minus source amount. |
| `status` | `NVARCHAR(10)` | Quality-gate status, normally `PASS` or `FAIL`. |

### `MIS_CONTROL_DATASET_LOAD`

Ordinary control table containing the ETL publication history for logical
datasets. The dashboard reads the latest `PUBLISHED` row directly from this
small table; it is not included in the application data cache. A new version
causes the application to reload the ordinary MIS tables once.

- **PK:** (`dataset_name`, `version_no`)
- **UK:** `load_id`
- **No FKs:** this is a dataset-level publication contract rather than a
  business fact table.

| Column | Type | Description |
|---|---|---|
| `dataset_name` | `NVARCHAR(64)` | Logical dataset name, for example `MIS_DASHBOARD`. |
| `version_no` | `BIGINT` | Monotonically increasing ETL publication version. |
| `load_id` | `NVARCHAR(64)` | Unique batch identifier. |
| `source_max_date` | `DATE` | Optional latest source date represented by the batch. |
| `published_at` | `NVARCHAR(30)` | Time at which the complete batch became available. |
| `status` | `NVARCHAR(16)` | Only `PUBLISHED` is eligible for application refresh. |
| `row_count` | `BIGINT` | Optional count of rows loaded by the batch. |
| `checksum` | `NVARCHAR(128)` | Optional ETL validation fingerprint. |
