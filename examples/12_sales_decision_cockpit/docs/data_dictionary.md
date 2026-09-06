# SDC data dictionary

The SDC schema is weekly and additive. The selected review week is always a
closed Monday-to-Sunday week in the seeded demo.

## Dimensions

| Table | Grain | Important fields |
|---|---|---|
| `SDC_DIM_DATE` | one calendar day | `calendar_date`, `week_start`, `is_business_day` |
| `SDC_DIM_REGION` | one region | `region_id`, `region_code`, `region_name` |
| `SDC_DIM_UNIT` | one sales unit | `unit_id`, `unit_code`, `region_id`, `status` |
| `SDC_DIM_SELLER` | one seller | `seller_id`, `seller_code`, `unit_id`, `status` |
| `SDC_DIM_PRODUCT` | one product group | `product_id`, `product_code`, `average_ticket`, `margin_rate` |
| `SDC_DIM_CHANNEL` | one channel | `channel_id`, `channel_name` |

## Facts

`SDC_FACT_WEEKLY_SCORECARD` has one row per
`week_start + seller_id + product_id + channel_id`. `unit_id` is repeated for
fast filtering and is validated against the seller dimension.

| Field | Definition |
|---|---|
| `sales_count` | booked sales count |
| `sales_amount` | booked sales value |
| `margin_amount` | gross margin value |
| `leads` | leads entering the funnel |
| `qualified_leads` | leads meeting the qualification rule |
| `wins` | converted opportunities / booked sales |
| `pipeline_value` | open nominal pipeline |
| `weighted_pipeline_value` | pipeline value multiplied by stage probability |
| `cancelled_amount` | value cancelled during the week |

`SDC_FACT_WEEKLY_TARGET` uses the identical dimensional grain. Its measures
are `target_count`, `target_amount` and `target_margin`. The identical grain
is what makes the driver bridge reconcilable at every supported dimension.

## Governance tables

`SDC_AUDIT_DATASET_LOAD` stores source and mart actual amounts for each week.
The seeded data has `difference_amount = 0` and `status = PASS`.

`SDC_CONTROL_DATASET_LOAD` stores the published dataset name, version, load
id, source watermark and row count. The application exposes this context with
every report response and in the sidebar status.
