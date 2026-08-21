# Deployment

Step-by-step guide to deploying incremental capture from an SAP BDC catalog-linked share. Read [how-it-works.md](how-it-works.md) before deploying if you have not already.

**Two tracks, two sequences.** Steps 1 and 2 apply to both tracks. After that, follow the sequence for the track your share carries. If your share carries both, complete the common steps first, then run both sequences in either order.

```
sql/00_discover.sql                  find the share, the watermark, and prove it prunes
sql/01_capture_control.sql           control schema: watermark state, refresh audit, baseline
sql/02_dynamic_table_engine.sql      the capture engine - four verified query shapes [custom track]
sql/03_monitoring.sql                reload detection, exceptions, alert                [custom track]
sql/04_watermark_exception_path.sql  the watermark exception path                       [custom track]
sql/05_standard_product_track.sql    validate, poll, freshness                         [standard track]
```

---

## Prerequisites (both tracks)

### What you need before starting

| Requirement | Notes |
| --- | --- |
| Catalog-linked database | Created by SAP BDC Connect. Identify with `SHOW DATABASES` and keep rows where `kind = 'CATALOG-LINKED DATABASE'`. |
| Gold (L2) database | An ordinary Snowflake database you own. Capture objects and the control schema live here. |
| Warehouse | Used by dynamic tables, the alert check, and the watermark task if you deploy the exception path. One warehouse can serve all three. |
| Schema and object names | Schema names contain a colon (e.g. `"dp_finance_v2_srv:v1"`) and object names are mixed case. Every reference must be double-quoted. `DESCRIBE TABLE` is the only route to the column list - `INFORMATION_SCHEMA.COLUMNS` returns no rows for a catalog-linked database. |

### Required privileges

The role that creates the capture objects needs:

- `USAGE` on the catalog-linked database and its schemas
- `SELECT` on the catalog-linked tables
- `CREATE SCHEMA`, `CREATE TABLE`, `CREATE VIEW`, `CREATE DYNAMIC TABLE`, `CREATE TASK`, `CREATE ALERT`, `CREATE PROCEDURE` on the Gold database
- `USAGE` on the warehouse

Grant read access to the ops role that will query monitoring views after deployment:

```sql
GRANT USAGE  ON SCHEMA <L2_DB>.CAPTURE_CONTROL TO ROLE <OPS_ROLE>;
GRANT SELECT ON ALL VIEWS  IN SCHEMA <L2_DB>.CAPTURE_CONTROL TO ROLE <OPS_ROLE>;
GRANT SELECT ON ALL TABLES IN SCHEMA <L2_DB>.CAPTURE_CONTROL TO ROLE <OPS_ROLE>;
```

---

## Step 1 - Run the readiness assessment [both tracks]

Run the readiness probe against your tenant before deploying anything. It classifies each object by track (standard or custom), validates standard metadata where present, confirms whether the mechanisms will work on your specific share, and discovers the watermark column name needed for subsequent steps.

```bash
cd probes
pip install -r requirements.txt
python3 cdc_readiness.py --connection <your_connection> --report readiness.md
```

Add `--with-scratch-tests` to verify dynamic table incremental refresh directly rather than inferring it from the connector write pattern. That flag creates a stream and a dynamic table in a scratch database and drops them afterwards; everything else is read-only.

### Reading the verdicts

| Verdict | Meaning | Action |
| --- | --- | --- |
| `READY` | All checks passed. | Proceed. |
| `READY WITH CAVEATS` | The core mechanism works but at least one signal is degraded - for example, the watermark prunes poorly or a delete code was not observed. | Proceed, noting the flagged check. |
| `NOT READY` | A critical check failed - connector appears to reload, no watermark column found, or share absent. | Do not deploy. Understand the cause before continuing. |
| `NOT ASSESSABLE` | A probe could not run, usually due to insufficient privilege or no catalog-linked database found. | Resolve the access issue and re-run. |

A `NOT READY` result on `signal.merge_or_reload` is the most serious failure for both tracks. The incremental pattern depends on the connector merging, not reloading. If the connector reloads, dynamic tables produce correct results but at full-rebuild cost on every refresh, and the watermark path reads every row every cycle. Neither is incremental.

---

## Step 2 - Deploy the control schema [both tracks]

Run `sql/01_capture_control.sql` in full. Substitute `<L2_DB>` with your Gold database name throughout. This creates three objects in `<L2_DB>.CAPTURE_CONTROL`:

| Object | Type | Purpose |
| --- | --- | --- |
| `WATERMARK` | Table | Watermark state for the exception path. Dynamic tables do not use it. `TRACK` is `NOT NULL` with a CHECK constraint - inserts must specify `'CUSTOM'` or `'STANDARD'`; other values fail with error 23514. `LAST_WATERMARK` holds a parsed timestamp for the custom track; `LAST_WATERMARK_TEXT` holds the raw text value for the standard track, where comparisons must stay in text to preserve the zero-scan poll cost. `OPERATION_COLUMN` is populated for standard-track objects only. |
| `VW_REFRESH_AUDIT` | View | Reads `DYNAMIC_TABLE_REFRESH_HISTORY()`. Used by monitoring and for manual verification. |
| `CAPTURE_BASELINE` | Table | A snapshot of row counts and file counts captured once at deployment. Required to detect a future reload or Parquet compaction. |

**`STATISTICS` type quirk.** `VW_REFRESH_AUDIT` addresses the `STATISTICS` column using the colon path operator:

```sql
STATISTICS:numInsertedRows::NUMBER  AS ROWS_INSERTED,
STATISTICS:numDeletedRows::NUMBER   AS ROWS_DELETED,
```

`STATISTICS` is type `OBJECT`, not `VARCHAR`. Wrapping it in `TRY_PARSE_JSON` fails with `Invalid argument types for function 'TRY_PARSE_JSON': (OBJECT)`. The colon path operator is the correct approach.

**Deploy this schema before deploying dynamic tables.** Without the baseline there is no reference point from which to detect a future reload. Once dynamic tables are running, the baseline cannot be meaningfully captured retroactively.

---

## Standard-track deployment

Deploy this sequence for any object whose track classification from Step 1 is `STANDARD`. Steps 1 and 2 must be complete first.

For the rationale behind each step, see [standard-products-cdc.md](standard-products-cdc.md).

### Step 3 (Standard) - Deploy the standard-track procedures

Run `sql/05_standard_product_track.sql`. Substitute `<L2_DB>` and `<WH>`. This creates in `<L2_DB>.CAPTURE_CONTROL`:

| Object | Type | Purpose |
| --- | --- | --- |
| `STANDARD_VALIDATION` | Table | Validation results per object, written by `SP_VALIDATE_STANDARD`. |
| `SP_VALIDATE_STANDARD` | Procedure | Validates connector metadata on every registered STANDARD object against thresholds derived from the clean-vs-corrupted contrast. Returns a summary and writes detail to `STANDARD_VALIDATION`. |
| `SP_POLL_STANDARD` | Procedure | Checks whether the source watermark advanced since the last poll, refreshes the dependent dynamic table if it did, and updates `WATERMARK`. Poll cost is 0.000 MB from Iceberg manifest statistics - comparisons stay in text. |
| `VW_STANDARD_FRESHNESS` | View | Reports `MINUTES_BEHIND` per registered object using `SYSDATE()` (UTC), not `CURRENT_TIMESTAMP()` (session-timezone-aware). |
| `VW_STANDARD_OPERATION_MIX` | View | Latest validation result per object from `STANDARD_VALIDATION`. |

### Step 4 (Standard) - Create dynamic tables

Create one dynamic table per standard-track object, reading from the live catalog-linked database:

```sql
CREATE OR REPLACE DYNAMIC TABLE <L2_DB>.<SCHEMA>.DT_<NAME>
  TARGET_LAG    = '15 minutes'
  WAREHOUSE     = <WH>
  REFRESH_MODE  = INCREMENTAL
AS
SELECT *
FROM   "<LINKED_DB>"."<LINKED_SCHEMA>"."<OBJECT_NAME>";
```

`REFRESH_MODE = INCREMENTAL` must be stated explicitly. Confirm acceptance after creation:

```sql
SHOW DYNAMIC TABLES IN SCHEMA <L2_DB>.<SCHEMA>;
-- Expected: refresh_mode = INCREMENTAL, refresh_mode_reason IS NULL
```

Do not put non-deterministic expressions inside the defining query. Put time-relative logic in a view over the dynamic table instead.

### Step 5 (Standard) - Register in WATERMARK

```sql
INSERT INTO <L2_DB>.CAPTURE_CONTROL.WATERMARK
  (OBJECT_NAME, SOURCE_FQN, TRACK, WATERMARK_COLUMN, OPERATION_COLUMN,
   MERGE_KEY_COLUMNS, LAST_WATERMARK_TEXT)
SELECT '<NAME>',
       '"<LINKED_DB>"."<LINKED_SCHEMA>"."<OBJECT_NAME>"',
       'STANDARD',
       '__TIMESTAMP',
       '__OPERATION_TYPE',
       '<KEY_COLUMN_1>,<KEY_COLUMN_2>',
       NULL
WHERE  NOT EXISTS (SELECT 1 FROM <L2_DB>.CAPTURE_CONTROL.WATERMARK
                    WHERE OBJECT_NAME = '<NAME>');
```

`TRACK` must be `'STANDARD'`. `WATERMARK_COLUMN` must be `'__TIMESTAMP'`. `LAST_WATERMARK_TEXT` is the comparison column for this track; it is never parsed during comparison. `MERGE_KEY_COLUMNS` is required - confirm the correct business key before inserting.

### Step 6 (Standard) - Validate before scheduling

```sql
CALL <L2_DB>.CAPTURE_CONTROL.SP_VALIDATE_STANDARD();
```

Read the result:

```sql
SELECT OBJECT_NAME, VERDICT, FAULTS, OPERATION_CARD, OPERATION_MAX_LEN,
       TIMESTAMPS_PARSED, DEGENERATE_ROWS
FROM   <L2_DB>.CAPTURE_CONTROL.VW_STANDARD_OPERATION_MIX
ORDER  BY VERDICT DESC, OBJECT_NAME;
```

**Do not proceed past this step with any UNUSABLE object.** An UNUSABLE result most commonly means `SOURCE_FQN` points at a local copy rather than the live catalog-linked database. Look for the `load_type_<hash>` column name - the hash suffix is absent on copies.

### Step 7 (Standard) - Schedule polling tasks

Create one task per registered object. Leave suspended until Step 6 returns USABLE for that object:

```sql
CREATE OR REPLACE TASK <L2_DB>.CAPTURE_CONTROL.TSK_POLL_<NAME>
  WAREHOUSE = <WH>
  SCHEDULE  = '15 minutes'
AS
CALL <L2_DB>.CAPTURE_CONTROL.SP_POLL_STANDARD(
       '<NAME>',
       '<L2_DB>.<SCHEMA>.DT_<NAME>');

-- Resume only after Step 6 confirms USABLE:
-- ALTER TASK <L2_DB>.CAPTURE_CONTROL.TSK_POLL_<NAME> RESUME;
```

The poll is free (0.000 MB from Iceberg manifest statistics). The cost of running the task on a 15-minute cadence is the warehouse resume cost for the brief procedure execution, not a data scan.

---

## Custom-track deployment

Deploy this sequence for any object whose track classification from Step 1 is `CUSTOM`. Steps 1 and 2 must be complete first.

For the rationale behind each step, see [custom-products-cdc.md](custom-products-cdc.md).

### Step 3 (Custom) - Configure dynamic tables

Run `sql/02_dynamic_table_engine.sql`. Substitute `<L2_DB>`, `<L2_WH>`, `<L1_DB>`, and `<L1_SCHEMA>` with the names discovered in Step 1. Double-quote all schema and object names from the catalog-linked database.

**State `REFRESH_MODE = INCREMENTAL` explicitly.** `AUTO` allows Snowflake to silently fall back to `FULL` with no error. Stating `INCREMENTAL` converts that into a deployment-time error.

```sql
SHOW DYNAMIC TABLES IN SCHEMA <L2_DB>.<SCHEMA>;
-- Expected: refresh_mode = INCREMENTAL, refresh_mode_reason IS NULL
```

**Set `TARGET_LAG` per object class:**

| Object class | Suggested `TARGET_LAG` |
| --- | --- |
| Transactional, high change rate | 15 minutes |
| Transactional, low change rate | 1 hour |
| Master data, actively updated | 1 hour |
| Master data, effectively static | 24 hours or `DOWNSTREAM` |

**Carry the watermark column through.** Include the replication timestamp column in each dynamic table's select list and alias it as `SOURCE_WRITE_TS`:

```sql
ZZLASTCHGDATE AS SOURCE_WRITE_TS
```

The monitoring view reads `SOURCE_WRITE_TS` to detect connector staleness. The column name varies from the reference-tenant name `ZZLASTCHGDATE`; use the name found in your readiness assessment.

**Confirm merge keys.** Catalog-linked tables carry no primary key constraint. The correct merge key for some SAP objects is not obvious from the column names. Purchase order history requires a seven-part key including `AccountAssignmentNumber` and `PurchasingHistoryCategory`. On a five-part key the table appears to contain duplicates, and de-duplicating on that key silently discards real goods movements. Confirm the grain before writing any MERGE.

### Step 4 (Custom) - Deploy monitoring and capture the baseline

Run `sql/03_monitoring.sql`. This creates four objects:

| Object | Purpose |
| --- | --- |
| `VW_RELOAD_DETECTION` | One row per monitored object. Reports `PCT_RESTAMPED` (proportion of rows whose timestamp falls in the last 24 hours) and a `CAPTURE_MODE` verdict. |
| `VW_CAPTURE_EXCEPTIONS` | Union of all alert conditions in one view. Query this to see what is currently open. |
| `AL_CAPTURE_HEALTH` | Snowflake Alert that checks `VW_CAPTURE_EXCEPTIONS` hourly and sends an email if any `HIGH` severity row is present. |

Add a `UNION ALL` branch to `VW_RELOAD_DETECTION` for each object you want to monitor.

**Capture the baseline immediately** after deploying the views, before the first connector write arrives:

```sql
INSERT INTO <L2_DB>.CAPTURE_CONTROL.CAPTURE_BASELINE
  (OBJECT_NAME, TOTAL_ROWS, FILE_COUNT, DISTINCT_STAMPS, LAST_WRITE, PCT_RESTAMPED_24H)
SELECT OBJECT_NAME, TOTAL_ROWS, FILE_COUNT, NULL, LAST_WRITE, PCT_RESTAMPED
FROM   <L2_DB>.CAPTURE_CONTROL.VW_RELOAD_DETECTION;
```

Without this INSERT the file-count compaction check in `VW_CAPTURE_EXCEPTIONS` has no reference to compare against.

### Step 5 (Custom) - Verify healthy steady state

After the first few refresh cycles, query the audit view:

```sql
SELECT NAME, REFRESH_ACTION, DURATION_MS, ROWS_INSERTED, ROWS_DELETED
FROM   <L2_DB>.CAPTURE_CONTROL.VW_REFRESH_AUDIT
ORDER  BY REFRESH_START_TIME DESC
LIMIT  20;
```

**Healthy looks like:**

- `REFRESH_ACTION = NO_DATA` in a few hundred milliseconds: nothing changed since the last connector write.
- `REFRESH_ACTION = INCREMENTAL` with a `ROWS_INSERTED` count that is a small fraction of the total table size: the connector wrote new data and the dynamic table picked it up.

**A problem looks like:**

- `REFRESH_ACTION = FULL` in steady state (not on the first cycle, which is always `INCREMENTAL`). Check whether a non-deterministic expression entered the query, and check `refresh_mode_reason` from `SHOW DYNAMIC TABLES`.
- `CAPTURE_MODE = 'FULL RELOAD DETECTED'` in `VW_RELOAD_DETECTION`. The connector switched from merging to reloading.
- `CAPTURE_MODE = 'CONNECTOR STALLED'` in `VW_RELOAD_DETECTION`. More than 24 hours since the last connector write. The dynamic table continues to report `NO_DATA` and appears healthy, making a stalled connector invisible without this check.

Check the exceptions view:

```sql
SELECT * FROM <L2_DB>.CAPTURE_CONTROL.VW_CAPTURE_EXCEPTIONS ORDER BY SEVERITY, SUBJECT;
```

An empty result means the capture pattern is healthy.

### Step 6 (Custom) - The exception path

Use `sql/04_watermark_exception_path.sql` only where a dynamic table cannot serve:

1. The transformation contains a non-deterministic expression, which forces a full refresh every cycle.
2. The target is outside Snowflake.

The exception path cannot detect physical deletes. Schedule `VW_DELETE_RECONCILIATION` periodically and rebuild the target if a persistent positive divergence appears (target has more rows than source).

**Registering an object:**

```sql
INSERT INTO <L2_DB>.CAPTURE_CONTROL.WATERMARK
  (OBJECT_NAME, SOURCE_FQN, TRACK, WATERMARK_COLUMN, MERGE_KEY_COLUMNS,
   OPERATION_COLUMN, LAST_WATERMARK)
SELECT '<NAME>',
       '"<LINKED_DB>"."<LINKED_SCHEMA>"."<OBJECT_NAME>"',
       'CUSTOM',
       'ZZLASTCHGDATE',
       '<KEY_COLUMN_1>,<KEY_COLUMN_2>',
       NULL,
       NULL
WHERE  NOT EXISTS (SELECT 1 FROM <L2_DB>.CAPTURE_CONTROL.WATERMARK
                    WHERE OBJECT_NAME = '<NAME>');
```

`TRACK` must be `'CUSTOM'`. The CHECK constraint rejects any other value with error 23514. `WATERMARK_COLUMN` must match the actual column name found by the probe on your tenant.

**Two design decisions in the procedure must be preserved when adapting to other objects:**

- The high-watermark is captured before reading data, not derived from the rows returned. A row written while the run is in progress is picked up on the next cycle rather than skipped.
- The range is closed at both ends (`ZZLASTCHGDATE > :v_from AND ZZLASTCHGDATE <= :v_to`). This makes a failed run safe to repeat without double-counting.

On error, `LAST_WATERMARK` is left untouched. The next run repeats the same closed range.

---

## Troubleshooting (both tracks)

| Symptom | Likely cause | Where to look |
| --- | --- | --- |
| `REFRESH_ACTION = FULL` in steady state | Non-deterministic expression in DT query, or `REFRESH_MODE` left as `AUTO` | `VW_REFRESH_AUDIT.UNEXPECTED_FULL_REFRESH`; `SHOW DYNAMIC TABLES` for `refresh_mode_reason` |
| `CAPTURE_MODE = 'FULL RELOAD DETECTED'` | BDC connector switched from merge to reload | `VW_RELOAD_DETECTION.PCT_RESTAMPED`; contact SAP BDC pipeline owner |
| `CAPTURE_MODE = 'CONNECTOR STALLED'` | No new connector write in over 24 hours | `VW_RELOAD_DETECTION.LAST_WRITE`; check SAP BDC pipeline health |
| `FILE_COUNT_DROP` in `VW_CAPTURE_EXCEPTIONS` | Parquet compaction; per-file watermark ranges widened | Re-run the pruning probe in `sql/00_discover.sql`; re-measure scan ratio |
| Dynamic table suspended | Task or warehouse issue | `VW_REFRESH_AUDIT.STATE`; `SHOW DYNAMIC TABLES` |
| `INFORMATION_SCHEMA.COLUMNS` returns no rows | Known behaviour of catalog-linked databases | Use `DESCRIBE TABLE`, one statement per object |
| Schema or object reference errors | Colon in schema name, mixed-case object names | Double-quote all identifiers |
| Target has more rows than source (watermark path) | Physical deletes upstream not visible on watermark path | `VW_DELETE_RECONCILIATION`; rebuild the target table |
| `TRY_PARSE_JSON` error on `STATISTICS` | `STATISTICS` is type `OBJECT`, not `VARCHAR` | Use colon path operator: `STATISTICS:numInsertedRows::NUMBER` |
| Merge produces unexpected duplicates | Wrong grain supplied to `MERGE_KEY_COLUMNS` | Verify the full key against SAP documentation |
| `SP_VALIDATE_STANDARD` returns `UNUSABLE` | Metadata corruption; `SOURCE_FQN` likely points at a local copy | `STANDARD_VALIDATION.FAULTS`; look for the `load_type_<hash>` column name (absent on copies) |
| `__TIMESTAMP` parses as NULL everywhere | Comma decimal separator; `TRY_TO_TIMESTAMP_NTZ` silently returns NULL | Use `TRY_TO_TIMESTAMP_NTZ(REPLACE(col, ',', '.'))` for display; comparisons stay as raw text |
| `SP_POLL_STANDARD` returns `NOT REGISTERED` | Object not in `WATERMARK` with `TRACK = 'STANDARD'` | Run the INSERT in Step 5 (Standard) |
| `VW_STANDARD_FRESHNESS` shows negative lag | `CURRENT_TIMESTAMP()` is session-timezone-aware | `VW_STANDARD_FRESHNESS` uses `SYSDATE()` (UTC as NTZ); any custom lag query must do the same |
| `DESCRIBE TABLE` fails with `is not initialized` | Share advertises a table never materialised | Tolerate the per-object error; the probe handles this; the object cannot be captured until SAP initialises it |
| INSERT into `WATERMARK` fails with error 23514 | `TRACK` value not in (`'CUSTOM'`, `'STANDARD'`) | Supply exactly `'CUSTOM'` or `'STANDARD'` in the `TRACK` column |
