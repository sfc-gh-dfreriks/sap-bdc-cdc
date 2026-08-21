# Deployment

Step-by-step guide to deploying incremental capture from an SAP BDC catalog-linked share into a Gold database. Read [how-it-works.md](how-it-works.md) before deploying if you have not already.

The SQL files run in order:

```
sql/00_discover.sql                  find the share, the watermark, and prove it prunes
sql/01_capture_control.sql           control schema: watermark state, refresh audit, baseline
sql/02_dynamic_table_engine.sql      the capture engine - four verified query shapes (custom track)
sql/03_monitoring.sql                reload detection, exceptions, alert
sql/04_watermark_exception_path.sql  the watermark exception path (custom track)
sql/05_standard_product_track.sql    the standard-product track: validation, polling, exception path
```

Steps 1 and 2 apply to both tracks. Steps 3-6 are the custom-track path. Step 7 is the standard-track path. Deploy only the track or tracks your share carries.

---

## Prerequisites

### What you need before starting

| Requirement | Notes |
| --- | --- |
| Catalog-linked database | Created by SAP BDC Connect. Identify it with `SHOW DATABASES` and keep rows where `kind = 'CATALOG-LINKED DATABASE'`. |
| Gold (L2) database | An ordinary Snowflake database you own. The capture objects and control schema live here. |
| Warehouse | Used by dynamic tables, the alert check, and by the watermark task if you deploy the exception path. One warehouse can serve all three. |
| Schema names and object names noted | Schema names contain a colon (e.g. `"dp_finance_v2_srv:v1"`) and object names are mixed case. Every reference must be double-quoted. `DESCRIBE TABLE` is the only route to the column list - `INFORMATION_SCHEMA.COLUMNS` returns no rows for a catalog-linked database. |

### Required privileges

The role that creates the capture objects needs:

- `USAGE` on the catalog-linked database and its schemas
- `SELECT` on the catalog-linked tables
- `CREATE SCHEMA`, `CREATE TABLE`, `CREATE VIEW`, `CREATE DYNAMIC TABLE`, `CREATE TASK`, `CREATE ALERT`, `CREATE PROCEDURE` on the Gold database
- `USAGE` on the warehouse

Grant read access to the ops role that will query the monitoring views after deployment:

```sql
GRANT USAGE  ON SCHEMA <L2_DB>.CAPTURE_CONTROL TO ROLE <OPS_ROLE>;
GRANT SELECT ON ALL VIEWS  IN SCHEMA <L2_DB>.CAPTURE_CONTROL TO ROLE <OPS_ROLE>;
GRANT SELECT ON ALL TABLES IN SCHEMA <L2_DB>.CAPTURE_CONTROL TO ROLE <OPS_ROLE>;
```

These grants are included at the end of `sql/01_capture_control.sql` and `sql/05_standard_product_track.sql`.

---

## Step 1 - Run the readiness assessment

Before deploying anything, run the readiness probe against your tenant. It classifies each object by track (standard or custom), validates standard metadata where present, confirms whether the mechanisms will work on your specific share, and discovers the watermark column name and form needed for subsequent steps.

```bash
cd probes
pip install -r requirements.txt
python3 cdc_readiness.py --connection <your_connection> --report readiness.md
```

Add `--with-scratch-tests` to verify the dynamic table incremental refresh directly, rather than inferring it from the connector write pattern. That flag creates a stream and a dynamic table in a scratch database and drops them afterwards; everything else is read-only.

### Reading the verdicts

| Verdict | Meaning | Action |
| --- | --- | --- |
| `READY` | All checks passed. | Proceed. |
| `READY WITH CAVEATS` | The core mechanism works but at least one signal is degraded - for example, the watermark prunes poorly. Dynamic tables are still recommended; check which probe is flagged and read its output. | Proceed, noting the flagged check. |
| `NOT READY` | A critical check failed. The connector appears to reload, no watermark column was found, or the share is absent. | Do not deploy. Understand the cause before continuing. |
| `NOT ASSESSABLE` | A probe could not run, usually due to insufficient privilege or no catalog-linked database found. | Resolve the access issue and re-run. |

The checks the probe runs correspond to the discovery steps in `sql/00_discover.sql`: share present, share inventory, change signal and track classification, standard metadata validation (standard track), connector write pattern (custom track), pruning ratio, and dynamic table incremental acceptance.

A `NOT READY` result on `signal.merge_or_reload` is the most serious for the custom track. The entire custom-track incremental pattern depends on the connector merging. If the connector reloads, the watermark path reads every row every cycle and dynamic tables become full-rebuild on every refresh - both are correct but expensive, and neither is incremental.

---

## Step 2 - Deploy the control schema

Run `sql/01_capture_control.sql` in full. Substitute `<L2_DB>` with your Gold database name throughout. This creates three objects in `<L2_DB>.CAPTURE_CONTROL`:

| Object | Type | Purpose |
| --- | --- | --- |
| `WATERMARK` | Table | Watermark state for the exception path (steps 6 and 7). Dynamic tables do not use it. The `TRACK` column distinguishes `'CUSTOM'` from `'STANDARD'`; objects on the two tracks use different watermark columns and comparison semantics. `LAST_WATERMARK` holds a parsed timestamp for the custom track; `LAST_WATERMARK_TEXT` holds the raw text value for the standard track, where comparisons must stay in text to preserve the zero-scan poll cost. `OPERATION_COLUMN` is populated for standard-track objects only. |
| `VW_REFRESH_AUDIT` | View | Reads Snowflake's `DYNAMIC_TABLE_REFRESH_HISTORY()`. Used by monitoring and for manual verification. |
| `CAPTURE_BASELINE` | Table | A snapshot of row counts and file counts captured once at deployment. Required to detect a future reload or Parquet compaction. |

**`STATISTICS` type quirk.** `VW_REFRESH_AUDIT` addresses the `STATISTICS` column from `DYNAMIC_TABLE_REFRESH_HISTORY()` using the colon path operator:

```sql
STATISTICS:numInsertedRows::NUMBER  AS ROWS_INSERTED,
STATISTICS:numDeletedRows::NUMBER   AS ROWS_DELETED,
```

`STATISTICS` is returned as type `OBJECT`, not `VARCHAR`. The natural thing to write - wrapping it in `TRY_PARSE_JSON` - fails with:

```
Invalid argument types for function 'TRY_PARSE_JSON': (OBJECT)
```

The colon path operator is the correct approach and is used throughout the monitoring views.

**Deploy this schema before deploying dynamic tables.** Without the baseline there is no reference point from which to detect a future reload. Once the dynamic tables are running, the baseline cannot be meaningfully captured retroactively.

---

## Step 3 - Configure the dynamic tables (custom track)

Run `sql/02_dynamic_table_engine.sql`. Substitute `<L2_DB>`, `<L2_WH>`, `<L1_DB>`, and `<L1_SCHEMA>` with the names discovered in step 1. Use double quotes around schema and object names from the catalog-linked database.

Two choices in the DDL carry all the weight.

### Choice 1 - State `REFRESH_MODE = INCREMENTAL` explicitly

Never leave it as `AUTO`. `AUTO` allows Snowflake to silently fall back to `FULL` when it cannot support incremental for a given query. A full refresh of a Gold layer over hundreds of millions of rows carries a completely different cost profile from an incremental one, and nothing in the object's behaviour announces the difference - no error, no warning, just a larger bill and a longer lag.

Stating `REFRESH_MODE = INCREMENTAL` converts a silent cost regression into a deployment-time error. If Snowflake cannot support incremental for the query you have written, the `CREATE` statement fails and tells you why. That is what you want.

All four query shapes in `sql/02_dynamic_table_engine.sql` have been verified to sustain incremental refresh over a catalog-linked share. There is no reason to use `AUTO`.

After creating each table, confirm the engine used the setting you provided:

```sql
SHOW DYNAMIC TABLES IN SCHEMA <L2_DB>.<SCHEMA>;
-- check: refresh_mode = INCREMENTAL, refresh_mode_reason IS NULL
```

`refresh_mode_reason IS NULL` means Snowflake found no obstacle to incremental refresh. A non-null reason value means it fell back to `FULL` despite your instruction - investigate before proceeding.

### Choice 2 - Set `TARGET_LAG` per object class

A no-change refresh takes a few hundred milliseconds, so a uniform lag is not expensive in absolute terms. But it obscures intent and leads to unnecessary cost on objects that rarely change. The `sql/02_dynamic_table_engine.sql` file uses these classes:

| Object class | Suggested `TARGET_LAG` |
| --- | --- |
| Transactional, high change rate | 15 minutes |
| Transactional, low change rate | 1 hour |
| Master data, actively updated | 1 hour |
| Master data, effectively static | 24 hours or `DOWNSTREAM` |

`DOWNSTREAM` means the table refreshes only when a downstream dynamic table requests it, which is appropriate for dimension tables that are joined to a transactional fact.

### Carry the watermark column through

Include the replication timestamp column in the dynamic table's select list and alias it as `SOURCE_WRITE_TS`:

```sql
ZZLASTCHGDATE AS SOURCE_WRITE_TS
```

The monitoring view reads `SOURCE_WRITE_TS` to detect connector staleness. Including it on every object costs one column and makes every downstream row traceable to the connector batch that delivered it. The watermark column name will vary from the reference-tenant name `ZZLASTCHGDATE`; use the name found in your readiness assessment.

### Merge keys

A catalog-linked table declares no primary key. The correct merge key for some SAP objects is not obvious from the column names. Purchase order history is the most problematic example: the correct key has seven parts including `AccountAssignmentNumber` and `PurchasingHistoryCategory`. On a five-part key, the table appears to contain duplicates, and de-duplicating on that key silently discards real goods movements without raising any error.

Confirm the grain of every object before writing any MERGE. If the correct key is unclear, the data product owner or SAP documentation is the authoritative source.

### What not to put inside a dynamic table

Non-deterministic expressions force a full refresh every cycle:

```sql
-- These expressions force FULL refresh every cycle:
CURRENT_DATE()
CURRENT_TIMESTAMP()
RANDOM()

-- Symptom: REFRESH_ACTION = FULL in steady state in VW_REFRESH_AUDIT
```

Month-to-date calculations are the common case. Put them in a view over the dynamic table instead. The view is always current; the dynamic table stays incrementally refreshable. An example is in `sql/02_dynamic_table_engine.sql`.

---

## Step 4 - Deploy monitoring and capture the baseline

Run `sql/03_monitoring.sql`. This creates four objects:

| Object | Purpose |
| --- | --- |
| `VW_RELOAD_DETECTION` | One row per monitored object. Reports `PCT_RESTAMPED` (proportion of rows whose timestamp falls in the last 24 hours) and a `CAPTURE_MODE` verdict. |
| `VW_CAPTURE_EXCEPTIONS` | Union of all alert conditions in one view. Query this to see what is currently open. |
| `AL_CAPTURE_HEALTH` | Snowflake Alert that checks `VW_CAPTURE_EXCEPTIONS` hourly and sends an email if any `HIGH` severity row is present. |

Add a `UNION ALL` branch to `VW_RELOAD_DETECTION` for each object you want to monitor. Monitoring the largest one or two objects per data product is normally sufficient - the connector's write mode is a property of the pipeline, not of an individual table.

**Capture the baseline immediately** after deploying the views, before the first connector write arrives:

```sql
INSERT INTO <L2_DB>.CAPTURE_CONTROL.CAPTURE_BASELINE
  (OBJECT_NAME, TOTAL_ROWS, FILE_COUNT, DISTINCT_STAMPS, LAST_WRITE, PCT_RESTAMPED_24H)
SELECT OBJECT_NAME, TOTAL_ROWS, FILE_COUNT, NULL, LAST_WRITE, PCT_RESTAMPED
FROM   <L2_DB>.CAPTURE_CONTROL.VW_RELOAD_DETECTION;
```

This INSERT is included in `sql/03_monitoring.sql`. Without it, the file-count compaction check in `VW_CAPTURE_EXCEPTIONS` has no reference to compare against.

---

## Step 5 - Verify healthy steady state

After the first few refresh cycles, query the audit view:

```sql
SELECT NAME, REFRESH_ACTION, DURATION_MS, ROWS_INSERTED, ROWS_DELETED
FROM   <L2_DB>.CAPTURE_CONTROL.VW_REFRESH_AUDIT
ORDER  BY REFRESH_START_TIME DESC
LIMIT  20;
```

**Healthy looks like:**

- `REFRESH_ACTION = NO_DATA` in a few hundred milliseconds: nothing changed since the last connector write; Snowflake established that without reading the source data.
- `REFRESH_ACTION = INCREMENTAL` with a `ROWS_INSERTED` count that is a small fraction of the total table size: the connector wrote new data and the dynamic table picked it up. Note that this state was not directly observed on the custom-track reference tenant in the available window - see the open questions in [how-it-works.md](how-it-works.md#open-questions).

**A problem looks like:**

- `REFRESH_ACTION = FULL` in steady state (not on the first cycle, which is always `INCREMENTAL`). The dynamic table is re-reading the entire source every cycle. Check whether a non-deterministic expression has entered the query, and check `refresh_mode_reason` from `SHOW DYNAMIC TABLES`.
- `CAPTURE_MODE = 'FULL RELOAD DETECTED'` in `VW_RELOAD_DETECTION`. The connector has switched from merging to reloading. Dynamic tables produce correct results but at full-rebuild cost on every refresh.
- `CAPTURE_MODE = 'CONNECTOR STALLED'` in `VW_RELOAD_DETECTION`. More than 24 hours since the last connector write. The dynamic table will continue to report `NO_DATA` and appear healthy, making a stalled connector invisible without this check.

Check the exceptions view to see whether anything is open:

```sql
SELECT * FROM <L2_DB>.CAPTURE_CONTROL.VW_CAPTURE_EXCEPTIONS ORDER BY SEVERITY, SUBJECT;
```

An empty result means the capture pattern is healthy.

---

## Step 6 - The custom-track exception path

Use `sql/04_watermark_exception_path.sql` only where a dynamic table cannot serve:

1. The transformation contains a non-deterministic expression, which forces a full refresh every cycle.
2. The target is outside Snowflake, and a dynamic table cannot push to it.

The exception path **cannot detect physical deletes**. A row that disappears from the source leaves no trace in the data; the watermark window will never encounter it. Schedule the `VW_DELETE_RECONCILIATION` query periodically and rebuild the target if you see a persistent positive divergence (target has more rows than source).

### Registering an object

```sql
INSERT INTO <L2_DB>.CAPTURE_CONTROL.WATERMARK
  (OBJECT_NAME, SOURCE_FQN, TRACK, WATERMARK_COLUMN, MERGE_KEY_COLUMNS,
   OPERATION_COLUMN, LAST_WATERMARK)
SELECT 'SO_ITEM',
       '<L1_DB>.<L1_SCHEMA>.SALES_ORDER_ITEM',
       'CUSTOM',
       'ZZLASTCHGDATE',
       'SALESDOCUMENT,SALESDOCUMENTITEM',
       NULL,
       NULL
WHERE  NOT EXISTS (SELECT 1 FROM <L2_DB>.CAPTURE_CONTROL.WATERMARK
                   WHERE OBJECT_NAME = 'SO_ITEM');
```

`MERGE_KEY_COLUMNS` is a required column with no default. Supplying the wrong grain silently produces duplicate rows or discards real records without any constraint violation. `TRACK` is required and must be `'CUSTOM'` for this path; the column has a CHECK constraint that rejects any other value.

### Procedure design decisions

Two decisions in `SP_CAPTURE_SO_ITEM` (in `sql/04`) are not incidental and should be preserved if you adapt the procedure to other objects:

- **The high-water mark is captured before reading data, not derived from the rows returned.** A row written while the run is in progress is picked up on the next cycle rather than skipped.
- **The range is closed at both ends** (`ZZLASTCHGDATE > :v_from AND ZZLASTCHGDATE <= :v_to`). This makes a failed run safe to repeat. An open upper bound would consume a different set of rows on each retry.

On error, `LAST_WATERMARK` is left untouched. The next run repeats the same closed range rather than skipping it.

---

## Step 7 - Standard-track deployment

Deploy this path for any object whose track classification from step 1 is `STANDARD`. Steps 1 and 2 must already be complete.

### 7a - Deploy the standard-track procedures

Run `sql/05_standard_product_track.sql`. Substitute `<L2_DB>` and `<WH>`. This creates in `<L2_DB>.CAPTURE_CONTROL`:

| Object | Type | Purpose |
| --- | --- | --- |
| `STANDARD_VALIDATION` | Table | Validation results per object, written by `SP_VALIDATE_STANDARD`. Stores operation cardinality, max length, parse success count, and degenerate-row count. |
| `SP_VALIDATE_STANDARD` | Procedure | Validates connector metadata on every registered STANDARD object against the thresholds derived from the clean-vs-corrupted contrast. Returns a summary and writes detail rows to `STANDARD_VALIDATION`. |
| `SP_POLL_STANDARD` | Procedure | Checks whether the source watermark advanced since the last poll, refreshes the dependent dynamic table if it did, and updates `WATERMARK`. Poll is free (0.000 MB, answered from Iceberg manifest statistics) because it compares `__TIMESTAMP` as text. |
| `VW_STANDARD_FRESHNESS` | View | Reports `MINUTES_BEHIND` per registered object, using `SYSDATE()` (UTC) rather than `CURRENT_TIMESTAMP()` (session-timezone-aware). |
| `VW_STANDARD_OPERATION_MIX` | View | Latest validation result per object from `STANDARD_VALIDATION`. |

### 7b - Create dynamic tables over the standard objects

Standard-track dynamic tables follow the same rules as custom-track: `REFRESH_MODE = INCREMENTAL`, explicit `TARGET_LAG`, no non-deterministic expressions. Create one dynamic table per object you want to capture, reading from the live catalog-linked database.

```sql
CREATE OR REPLACE DYNAMIC TABLE <L2_DB>.<SCHEMA>.DT_CUSTOMER
  TARGET_LAG = '15 minutes'
  WAREHOUSE  = <WH>
  REFRESH_MODE = INCREMENTAL
AS
SELECT *
FROM   "<LINKED_DB>"."<LINKED_SCHEMA>"."<OBJECT_NAME>";
```

Confirm incremental acceptance after creation:

```sql
SHOW DYNAMIC TABLES IN SCHEMA <L2_DB>.<SCHEMA>;
-- check: refresh_mode = INCREMENTAL, refresh_mode_reason IS NULL
```

### 7c - Register objects in WATERMARK

```sql
INSERT INTO <L2_DB>.CAPTURE_CONTROL.WATERMARK
  (OBJECT_NAME, SOURCE_FQN, TRACK, WATERMARK_COLUMN, OPERATION_COLUMN,
   MERGE_KEY_COLUMNS, LAST_WATERMARK_TEXT)
SELECT 'CUSTOMER',
       '"<LINKED_DB>"."<LINKED_SCHEMA>"."<OBJECT_NAME>"',
       'STANDARD',
       '__TIMESTAMP',
       '__OPERATION_TYPE',
       '<KEY_COLUMN_1>,<KEY_COLUMN_2>',
       NULL
WHERE  NOT EXISTS (SELECT 1 FROM <L2_DB>.CAPTURE_CONTROL.WATERMARK
                   WHERE OBJECT_NAME = 'CUSTOMER');
```

`WATERMARK_COLUMN` must be `'__TIMESTAMP'` (the fixed-name column, two leading underscores). `OPERATION_COLUMN` must be `'__OPERATION_TYPE'`. `LAST_WATERMARK_TEXT` is the comparison column for this track - it stores the raw text value and is never parsed for comparison, only for display in `VW_STANDARD_FRESHNESS`.

### 7d - Validate before scheduling anything

```sql
CALL <L2_DB>.CAPTURE_CONTROL.SP_VALIDATE_STANDARD();
```

Read the result. All registered objects must return `USABLE` before you schedule any capture. An `UNUSABLE` result means the metadata columns are not clean change codes - most commonly because the `SOURCE_FQN` points at a local copy of the data product rather than the live catalog-linked database.

```sql
SELECT OBJECT_NAME, VERDICT, FAULTS, OPERATION_CARD, OPERATION_MAX_LEN,
       TIMESTAMPS_PARSED, DEGENERATE_ROWS
FROM   <L2_DB>.CAPTURE_CONTROL.VW_STANDARD_OPERATION_MIX
ORDER  BY VERDICT DESC, OBJECT_NAME;
```

Do not proceed past this step with any `UNUSABLE` object.

### 7e - Schedule SP_POLL_STANDARD per object

Create one task per registered object. `SP_POLL_STANDARD` takes two parameters: the `OBJECT_NAME` from `WATERMARK`, and the fully-qualified dynamic table name to refresh if the watermark advanced.

```sql
CREATE OR REPLACE TASK <L2_DB>.CAPTURE_CONTROL.TSK_POLL_CUSTOMER
  WAREHOUSE = <WH>
  SCHEDULE  = '15 minutes'
AS
CALL <L2_DB>.CAPTURE_CONTROL.SP_POLL_STANDARD(
       'CUSTOMER',
       '<L2_DB>.<SCHEMA>.DT_CUSTOMER');
```

Leave tasks **suspended** until you have verified step 7d. Resume with `ALTER TASK ... RESUME` once validation is clean.

The poll itself is free (answered from Iceberg manifest statistics). The cost of running the task scheduler on a 15-minute cadence is the warehouse resume cost for the brief procedure execution, not a data scan.

---

## Troubleshooting

| Symptom | Likely cause | Where to look |
| --- | --- | --- |
| `REFRESH_ACTION = FULL` in steady state | Non-deterministic expression in DT query, or `REFRESH_MODE` left as `AUTO` | `VW_REFRESH_AUDIT.UNEXPECTED_FULL_REFRESH`; `SHOW DYNAMIC TABLES` for `refresh_mode_reason` |
| `CAPTURE_MODE = 'FULL RELOAD DETECTED'` | BDC connector switched from merge to reload | `VW_RELOAD_DETECTION.PCT_RESTAMPED`; contact SAP BDC pipeline owner |
| `CAPTURE_MODE = 'CONNECTOR STALLED'` | No new connector write in over 24 hours | `VW_RELOAD_DETECTION.LAST_WRITE`; check SAP BDC pipeline health |
| `FILE_COUNT_DROP` exception in `VW_CAPTURE_EXCEPTIONS` | Parquet compaction; per-file watermark ranges widened | Re-run the pruning probe in `sql/00_discover.sql`; re-measure scan ratio |
| Dynamic table suspended | Task or warehouse issue | `VW_REFRESH_AUDIT.STATE`; `SHOW DYNAMIC TABLES` |
| `INFORMATION_SCHEMA.COLUMNS` returns no rows | Known behaviour of catalog-linked databases | Use `DESCRIBE TABLE` instead, one statement per object |
| Schema or object reference errors | Colon in schema name, mixed-case object names | Double-quote all identifiers: `"dp_finance_v2_srv:v1"."ObjectName"` |
| Target has more rows than source (watermark path) | Physical deletes upstream not visible on watermark path | `VW_DELETE_RECONCILIATION`; rebuild the target table |
| `TRY_PARSE_JSON` error on `STATISTICS` | `STATISTICS` column is type `OBJECT`, not `VARCHAR` | Use colon path operator: `STATISTICS:numInsertedRows::NUMBER` |
| Merge produces unexpected duplicates | Wrong grain supplied to `MERGE_KEY_COLUMNS` | Verify the full key against SAP documentation; see `sql/01_capture_control.sql` comment on EKBE |
| `SP_VALIDATE_STANDARD` returns `UNUSABLE` | Metadata corruption; `SOURCE_FQN` likely points at a local copy, not the live catalog-linked database | `CAPTURE_CONTROL.STANDARD_VALIDATION.FAULTS`; verify `SOURCE_FQN` resolves to a database with `kind = 'CATALOG-LINKED DATABASE'`; look for the `load_type_<hash>` column name (hash suffix is absent on copies) |
| `__TIMESTAMP` parses as NULL everywhere | Comma decimal separator; `TRY_TO_TIMESTAMP_NTZ` silently returns NULL without the `REPLACE` | Use `TRY_TO_TIMESTAMP_NTZ(REPLACE(col, ',', '.'))` for any display context; comparisons should stay as raw text |
| `SP_POLL_STANDARD` returns `NOT REGISTERED` | Object not in `WATERMARK` with `TRACK = 'STANDARD'` | Run the INSERT in step 7c |
| `VW_STANDARD_FRESHNESS` shows negative lag | `CURRENT_TIMESTAMP()` is session-timezone-aware; mixing with a UTC watermark reports wrong lag | `VW_STANDARD_FRESHNESS` uses `SYSDATE()` (UTC as NTZ); any custom query computing lag must do the same |
| `DESCRIBE TABLE` fails with `is not initialized` | Share advertises a table that was never materialised (`refresh_table`) | Tolerate the per-object error; the probe handles this already; the object cannot be captured until SAP initialises it |
