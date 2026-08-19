# How it works

This document explains the mechanism behind incremental capture from an SAP BDC catalog-linked share, the evidence that supports each claim, and why the standard CDC alternatives are unavailable. For deployment steps, see [deployment.md](deployment.md).

---

## The share and its apparent nature

A catalog-linked database created by SAP BDC Connect reports `kind = TABLE` and `is_external = N` on every object inside it. `SHOW TABLES` and `INFORMATION_SCHEMA` agree. Both are incomplete descriptions.

The objects are Iceberg tables whose catalog is managed by SAP, not by Snowflake. Snowflake can read them but does not own their metadata lifecycle. The distinction only becomes visible when you try to attach a change mechanism. Run `SHOW ICEBERG TABLES IN DATABASE <LINKED_DB>` to see the underlying type. The full discovery sequence is in `sql/00_discover.sql`.

Two behaviours make working with this share awkward and are worth knowing before you start:

- `INFORMATION_SCHEMA.COLUMNS` returns no rows for a catalog-linked database. `DESCRIBE TABLE` is the only route to a column list, one statement per object. There is no bulk alternative.
- Schema names contain a colon (for example `"dp_finance_v2_srv:v1"`) and object names are mixed case. Every reference must be double-quoted.

---

## What the platform refuses

The three standard CDC mechanisms Snowflake offers are all unavailable, for one shared reason - the catalog is external:

| You attempt | Error |
| --- | --- |
| `CREATE STREAM ... ON TABLE <share_object>` | *Streams on External Tables or Iceberg tables with an external catalog must have INSERT_ONLY set to true.* |
| `SELECT ... CHANGES(INFORMATION => DEFAULT)` | *CHANGES clause is not supported on external tables or Iceberg tables with an external catalog.* |
| `ALTER TABLE ... SET CHANGE_TRACKING = TRUE` | *change_tracking cannot be set for Iceberg tables unless they use Snowflake catalog.* |

**INSERT_ONLY stream.** A stream with `INSERT_ONLY = TRUE` is permitted. It creates successfully and reports `type = DELTA, mode = INSERT_ONLY`. It returns zero rows at creation, which is correct - the stream is positioned at the current state, not at the beginning of history. The 14-day staleness window applies: an unread stream expires after that period, which is a risk on any object that does not change frequently.

The INSERT_ONLY stream sees inserts and updates. It does not see physical deletes, because delete detection requires comparing two snapshots of the source, and INSERT_ONLY mode does not do that. This is not a limitation of this particular implementation; it is intrinsic to the mode. If you need delete capture, a dynamic table is the only available option.

**Metadata columns.** `METADATA$ACTION`, `METADATA$ISUPDATE`, and `METADATA$ROW_ID` do not resolve on a catalog-linked object. `METADATA$FILENAME` works and exposes the underlying Parquet file paths. The monitoring query in `sql/03_monitoring.sql` uses it to count distinct files per object, which is the signal used to detect Parquet compaction.

**Time travel.** Time travel reaches 24 hours. At 36 hours it is unavailable. `DATA_RETENTION_TIME_IN_DAYS` is 1 (the account default); the share object cannot be altered to extend it. This means you cannot use `AT(OFFSET => ...)` for comparisons beyond 24 hours on the source objects.

---

## The connector's merge behaviour - the precondition

The entire incremental pattern rests on one observable property of the BDC connector: it merges rather than reloads. Historical rows keep their original replication timestamp while new data arrives in new Parquet files.

This is a measured property, not a documented guarantee. On a reference tenant, over five days:

- Initial load: 108,615,638 rows across 402 Parquet files.
- Day 2: 10 rows in 1 new file. Historical rows were unchanged.
- Day 3: 20 rows in 1 new file. Historical rows were unchanged.

The day-2 delta included retroactive updates - rows originally created in May 2025 and February 2026 were rewritten. The connector therefore does not deliver append-only data. A watermark design must MERGE on the business key; an INSERT-only approach will produce duplicate rows on retroactive updates.

**Why this matters.** If the connector reloaded - writing every row on every cycle with a fresh timestamp - then every row would always appear in the watermark window, and the "incremental" read would be a full scan by another name. The merge behaviour is what makes the pruning effective and the pattern viable.

**Verify on your own tenant** before deploying. The query in `sql/00_discover.sql` (step 6) groups rows by write day. The shape to look for is several distinct write days with the bulk of rows on the initial load and small incremental additions since. A single write day covering all rows means the connector either loaded once or reloads fully. Neither supports a watermark.

---

## The watermark column

The replication timestamp is not a Snowflake or BDC standard field. It is typically a customer extension field added to the SAP source during the SLT pipeline configuration. On the reference tenant it was named `ZZLASTCHGDATE`, typed `TIMESTAMP_NTZ(6)`, and present on all 23 objects in the share. The name varies by pipeline implementation.

Finding it requires reading the column list with `DESCRIBE TABLE` and looking for a timestamp column whose name suggests a load or replication event rather than a business event. A business date such as `PostingDate` or `DocumentDate` is not a replication watermark. The key distinction is whether the value changes when the connector writes the row, or when the business event occurred.

On the reference tenant, `ZZLASTCHGDATE` had 13,684 distinct values on the GL line item object. The timestamps are fine-grained - not one value per connector batch, but many values per run. This fine granularity is part of why per-file Iceberg statistics are effective: files written in a single connector run carry a narrow min/max range, and a predicate against a recent value eliminates most files immediately.

Once you have identified the watermark column, confirm it prunes using the queries in `sql/00_discover.sql` (step 7). The comments in that file give interpretation thresholds for the scan ratio.

---

## Why the watermark prunes and the business date does not

Because the connector writes the replication timestamp in monotonic, non-overlapping batches, the min/max of that column in each Parquet file spans a narrow range. Iceberg stores per-file column statistics. When you filter on the replication timestamp, Snowflake's query planner reads those statistics and eliminates every file whose range cannot contain a match.

A business date such as `PostingDate` is a value written by the business process. Documents from many different time periods land in the same Parquet files, because the connector groups rows by object, not by business date. Per-file statistics on a business date span the full historical range of the object and eliminate nothing.

The following measurements are from a reference tenant, with `USE_CACHED_RESULT = FALSE` and a data column projected. Note that `COUNT(*)` alone can be answered from Iceberg manifest statistics without reading any data, which would flatter the scan cost and hide the real behaviour - see the comment in `sql/00_discover.sql` step 7 for why this matters.

| Query | Data scanned | Duration (reference tenant) |
| --- | --- | --- |
| Full column read, 7 GB object | 7,035.80 MB | - |
| Watermark predicate, 30 matching rows | 0.00 MB | 28 ms |
| Business-date predicate, 549 matching rows | 4,695.75 MB | 542 ms |
| Full GROUP BY aggregate, 108,615,638 rows | 6.87 GB | 15,078 ms |
| Same aggregate scoped by watermark | 0 GB | 630 ms |

The practical implication: use the replication timestamp as the predicate, not a business date. The difference is not a performance optimisation - it is the difference between an incremental pattern that reads only changed data and one that scans the full object every cycle.

**Measuring scan cost.** `INFORMATION_SCHEMA.QUERY_HISTORY` provides `BYTES_SCANNED` but not `PARTITIONS_SCANNED` or `PARTITIONS_TOTAL`. Those columns are only available in `ACCOUNT_USAGE.QUERY_HISTORY`, which carries a latency of up to 45 minutes. `BYTES_SCANNED` is sufficient to confirm file elimination. The query history call requires a database context, as shown in `sql/00_discover.sql`.

---

## The four verified query shapes

Dynamic tables with `REFRESH_MODE = INCREMENTAL` were tested across four query shapes over the catalog-linked share. All four were accepted as incremental, with `refresh_mode_reason = null`. On a reference tenant, second-cycle no-change refreshes returned `REFRESH_ACTION = NO_DATA` in the durations below. Initial refreshes were `INCREMENTAL` with `numInsertedRows` equal to the full row count of the source.

| Shape | Description | NO_DATA duration (reference tenant) |
| --- | --- | --- |
| A | Flat projection over a view that casts dates | 348 ms |
| B | Join of two views | 486 ms |
| C | GROUP BY aggregate | 450 ms |
| D | Left join to a pre-aggregated child CTE | 570 ms |

The SQL for each shape is in `sql/02_dynamic_table_engine.sql`.

**What blocks incremental refresh.** Any non-deterministic expression inside a dynamic table's defining query forces a full refresh on every cycle, regardless of whether the source has changed. `CURRENT_DATE()`, `CURRENT_TIMESTAMP()`, and `RANDOM()` are the common cases. Month-to-date logic is the most frequent source of this problem in practice.

The correct pattern is to put time-relative logic in a view over the dynamic table rather than inside it:

```sql
-- Do NOT put this inside the dynamic table:
--   WHERE  SALESDOCUMENTDATE >= DATE_TRUNC('month', CURRENT_DATE())

-- Instead, define a view over the dynamic table:
CREATE OR REPLACE VIEW <L2_DB>.<SCHEMA>.VW_MONTH_TO_DATE AS
SELECT *
FROM   <L2_DB>.<SCHEMA>.DT_SO_LINE
WHERE  SALESDOCUMENTDATE >= DATE_TRUNC('month', CURRENT_DATE());
```

The view is always current, adds no refresh cost, and leaves the dynamic table incrementally refreshable. This pattern is included in `sql/02_dynamic_table_engine.sql`.

---

## Choosing a mechanism

Three mechanisms are available. The choice turns almost entirely on one question: do you need to capture physical deletes?

| | Dynamic table | Watermark + MERGE | INSERT_ONLY stream |
| --- | --- | --- | --- |
| Inserts | yes | yes | yes |
| Updates | yes | yes | yes |
| **Physical deletes** | **yes** - diffs snapshots | **no** - no change-mode column | **no** - by definition |
| Cheap no-change detection | yes (`NO_DATA`) | yes (0 MB scanned) | yes |
| Code required | none | task + control table + MERGE | task + MERGE |
| Can feed targets outside Snowflake | no | yes | yes |
| Goes stale if unread | no | no | yes, 14 days |
| Blocked by non-deterministic SQL | yes | no | no |

**Use dynamic tables.** They are the only mechanism that captures physical deletes, and they require no procedural code. The watermark path (`sql/04_watermark_exception_path.sql`) is the exception, not the default; use it only where a dynamic table cannot serve:

1. The transformation contains a non-deterministic expression, which forces a full refresh every cycle.
2. The target is outside Snowflake, and a dynamic table cannot push to it.

The INSERT_ONLY stream path requires the same procedural code as the watermark path, cannot detect deletes, and carries a 14-day staleness risk the other two paths do not. There is no situation in which it is preferable to the watermark path.

---

## Delete semantics - the single most important difference

The SAP BDC share carries no change-mode column. There is no `ODQ_CHANGEMODE`, `RECORDMODE`, or `UPDKZ`. A row that disappears from the share leaves no trace in the data itself.

**Watermark path.** A watermark reads rows whose replication timestamp falls in a window. If a row is removed from the source, its timestamp does not change; the window never encounters it. The divergence is invisible until you compare row counts between source and target. The delete reconciliation view in `sql/04_watermark_exception_path.sql` performs this comparison and reports a `TARGET HAS ROWS THE SOURCE NO LONGER CARRIES - REBUILD` verdict when the target exceeds the source count. Rebuilding the target is the recommended response; identifying the specific deleted rows is impractical on the watermark path.

**Dynamic table path.** A dynamic table detects the same event because it diffs consecutive snapshots of the source. When a row is present in snapshot N and absent in snapshot N+1, the dynamic table removes it from the target. No column flag is required; the absence itself is the signal.

This is the most consequential difference between the two paths. If your data product contains objects where physical deletes are meaningful to downstream consumers - cancelled orders, deactivated accounts, removed line items - the watermark path will silently diverge from the source over time.

**Business soft-delete flags are a separate matter.** Some SAP objects carry a logical deletion indicator as an ordinary data column. These arrive as normal column updates and are captured by all three mechanisms:

| Object | Soft-delete column |
| --- | --- |
| Supplier master | `DeletionIndicator` |
| GL account master | `AccountIsMarkedForDeletion` |
| Product master | `IsMarkedForDeletion` |
| Purchasing documents | `PurchasingDocumentDeletionCode` |

These columns must be filtered in the consuming layer. A query that reads the supplier master without excluding logically deleted suppliers will include them in results.

---

## Open questions

Two things were not observable in the five-day reference window. They are stated here as open, not settled.

**1. What a changed-source dynamic table refresh produces.**

Initial refresh was `INCREMENTAL` with `numInsertedRows` equal to the full row count. A no-change refresh returned `NO_DATA`. A refresh following a genuine connector write was not observed in the window. After your next connector write, query `VW_REFRESH_AUDIT` and look for `REFRESH_ACTION = INCREMENTAL` with `ROWS_INSERTED` matching the number of rows the connector changed, not the full count. If `REFRESH_ACTION = FULL` appears instead, the fallback is the watermark path in `sql/04_watermark_exception_path.sql`, whose cost is measured.

**2. Whether an INSERT_ONLY stream yields rows after a source change.**

The stream was created and correctly returned zero rows at creation. A subsequent connector write was not exercised against it. This only matters if the stream path is adopted, which is not the recommendation.

Neither gap changes the recommendation. The design is not contingent on either being settled in a particular way.
