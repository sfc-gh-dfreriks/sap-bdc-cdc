# How it works

This document explains the mechanism behind incremental capture from an SAP BDC catalog-linked share, the evidence that supports each claim, and why the standard CDC alternatives are unavailable. For deployment steps, see [deployment.md](deployment.md).

---

## Two tracks

An SAP BDC catalog-linked share can carry one of two kinds of change signal. An account can hold both at once; classify each object individually before deploying capture.

| Track | Change signal | Delete representation |
| --- | --- | --- |
| Standard | Four connector-generated columns: `__OPERATION_TYPE`, `__TIMESTAMP`, `load_type_<hash>`, `run_id_<hash>` | Structural - `__OPERATION_TYPE` exists, but no delete code was ever observed on the reference share; see [Open questions](#open-questions) |
| Custom | Source-system extension field, typically `ZZLASTCHGDATE`, typed `TIMESTAMP_NTZ` | None - a row deleted in the source disappears without a trace |

Prefer standard data products where they exist. Only the standard track carries an operation column, making delete detection structurally possible. Whether that column ever carries a delete code is unverified as of the reference measurement - but the structural capability is absent entirely on the custom track.

On both tracks, the engine of choice is dynamic tables with `REFRESH_MODE` pinned to `INCREMENTAL`. The watermark procedure is the trigger and the exception path; it is not the engine.

---

## The share and its apparent nature

A catalog-linked database created by SAP BDC Connect reports `kind = TABLE` and `is_external = N` on every object inside it. `SHOW TABLES` and `INFORMATION_SCHEMA` agree. Both are incomplete descriptions.

The objects are Iceberg tables whose catalog is managed by SAP, not by Snowflake. Snowflake can read them but does not own their metadata lifecycle. The distinction only becomes visible when you try to attach a change mechanism. Run `SHOW ICEBERG TABLES IN DATABASE <LINKED_DB>` to see the underlying type. The full discovery sequence is in `sql/00_discover.sql`.

Two behaviours make working with this share awkward and are worth knowing before you start:

- `INFORMATION_SCHEMA.COLUMNS` returns no rows for a catalog-linked database. `DESCRIBE TABLE` is the only route to a column list, one statement per object. There is no bulk alternative.
- Schema names contain a colon (for example `"dp_finance_v2_srv:v1"`) and object names are mixed case. Every reference must be double-quoted.
- A share can advertise a table that has never been materialised. `DESCRIBE TABLE` on such an object fails with `table 'refresh_table' is not initialized`. That is a property of the share, not a fault in the caller; tooling must tolerate per-object failure and continue rather than aborting an account-wide survey.

---

## What the platform refuses

The three standard CDC mechanisms Snowflake offers are all unavailable, for one shared reason - the catalog is external:

| You attempt | Error |
| --- | --- |
| `CREATE STREAM ... ON TABLE <share_object>` | *Streams on External Tables or Iceberg tables with an external catalog must have INSERT_ONLY set to true.* |
| `SELECT ... CHANGES(INFORMATION => DEFAULT)` | *CHANGES clause is not supported on external tables or Iceberg tables with an external catalog.* |
| `ALTER TABLE ... SET CHANGE_TRACKING = TRUE` | *change_tracking cannot be set for Iceberg tables unless they use Snowflake catalog.* |

**`SHOW TABLES` reports `change_tracking = ON` on standard data product objects.** This is misleading. On the reference account (CUSTOMER_V1, a standard data product), both streams and the `CHANGES()` clause are still refused with the same errors despite that value. `change_tracking = ON` in `SHOW TABLES` output does not mean Snowflake CDC mechanisms work on these objects.

**INSERT_ONLY stream.** A stream with `INSERT_ONLY = TRUE` is permitted. It creates successfully and reports `type = DELTA, mode = INSERT_ONLY`. It returns zero rows at creation, which is correct - the stream is positioned at the current state, not at the beginning of history. The 14-day staleness window applies: an unread stream expires after that period, which is a risk on any object that does not change frequently.

The INSERT_ONLY stream sees inserts and updates. It does not see physical deletes, because delete detection requires comparing two snapshots of the source, and INSERT_ONLY mode does not do that. This is not a limitation of this particular implementation; it is intrinsic to the mode. If you need delete capture, a dynamic table is the only available option.

**Metadata columns.** `METADATA$ACTION`, `METADATA$ISUPDATE`, and `METADATA$ROW_ID` do not resolve on a catalog-linked object. `METADATA$FILENAME` works and exposes the underlying Parquet file paths. The monitoring query in `sql/03_monitoring.sql` uses it to count distinct files per object, which is the signal used to detect Parquet compaction.

**Time travel.** Time travel reaches 24 hours. At 36 hours it is unavailable. `DATA_RETENTION_TIME_IN_DAYS` is 1 (the account default); the share object cannot be altered to extend it. This means you cannot use `AT(OFFSET => ...)` for comparisons beyond 24 hours on the source objects.

---

## Standard track: connector metadata columns

Standard data products carry four connector-generated columns. Their names follow a specific pattern:

- `__OPERATION_TYPE` - the change mode for the row. Fixed name, two leading underscores, uppercase. Type: TEXT.
- `__TIMESTAMP` - when the connector wrote the row. Fixed name, two leading underscores, uppercase. Type: TEXT.
- `load_type_<32-hex>` - value is `initial` or `delta`. Lowercase prefix, suffixed with a 32-character hex flow hash.
- `run_id_<32-hex>` - identifies the replication flow. Same hash suffix. Lowercase.

The hash suffix was identical across all 7 objects of the CUSTOMER_V1 product on the reference account, confirming it identifies the replication flow rather than the individual object.

**`__TIMESTAMP` is TEXT with a comma decimal separator.** The format observed is `2026-06-15T09:41:59,6426580Z` - ISO-8601 with a comma where the fractional seconds decimal point should be. `TRY_TO_TIMESTAMP_NTZ` does not error on this format; it returns NULL for every row. An unguarded cast therefore looks like an empty column rather than a format mismatch. The correct parse is `TRY_TO_TIMESTAMP_NTZ(REPLACE(col, ',', '.'))`.

**Compare as text, parse only for display.** The format is fixed-width and zero-padded, so lexicographic order is chronological order. This was verified directly: `parse(MAX(raw)) == MAX(parse(raw))` returned True. The poll cost difference is significant:

| Operation | Data scanned | Duration (39,462-row reference table) |
| --- | --- | --- |
| `MAX("__TIMESTAMP")` over raw TEXT | 0.000 MB | 2 ms |
| `MAX(TRY_TO_TIMESTAMP_NTZ(...))` over parsed expression | 0.310 MB | 45 ms |

The raw MAX is answered from Iceberg manifest statistics without touching the data. The parsed MAX reads the object. The gap grows proportionally with the object. Every comparison in `sql/05_standard_product_track.sql` stays in text; parsing appears only in display views.

**`run_id` is not a batch discriminator.** On the reference share, `run_id` held the same value on both the initial snapshot (67,087 rows) and all subsequent delta rows. It cannot be used as a watermark.

**Operation values observed.** Across 5 non-empty objects on the CUSTOMER_V1 reference share: `L` (67,087 rows, initial load) and `A` (393 rows, delta after-images). No delete code appeared anywhere. The authoritative value enumeration could not be confirmed from SAP documentation; see [Open questions](#open-questions).

**`load_type` values observed:** `initial` = 67,087 rows, `delta` = 393 rows.

---

## The trust failure

The metadata columns were clean inside every live catalog-linked database examined. Every local copy of a standard product on the same account had corrupted metadata. The corruption is silent - no query errors, no schema mismatches, just wrong values in the operation and timestamp columns that produce wrong incremental results.

Measured on three local-copy objects on the reference account:

- **`PRODUCT.PRODUCT`:** `__OPERATION_TYPE` holds media titles ("Mission Possible 1", "About a Girl - Series") plus 16,317 NULLs - 17 distinct values, the longest 33 characters.
- **`SALESORDERS.SALESORDERITEM`:** `__OPERATION_TYPE`, `__TIMESTAMP`, and `LOAD_TYPE` all hold the same value - customer identifiers, 620 distinct values, 10 characters - on 735,305 of 735,305 rows. `RUN_ID` is null throughout. Confirmed with a positional `SELECT *`, ruling out a column-resolution artifact.
- **`SALESORDERS.SALESORDER`:** the same degeneracy on 557,693 of 558,011 rows, with a single value `'C'` repeated across all three metadata columns.

The defect propagates downstream: later medallion layers built over corrupted copies carry the same corruption. The argument for validating at L0/L1 before building anything is precisely this propagation.

**How `SP_VALIDATE_STANDARD` sets its thresholds.** The contrast between clean and corrupted objects is the basis: clean objects carry 2 distinct operation codes of length 1; corrupted objects carry 17 distinct values at length 33, or 620 distinct at length 10. The verdict thresholds are: operation cardinality > 12 or max length > 2 means the column is not a change code.

**The hash suffix as a structural marker.** Every live catalog-linked database carries `load_type_<32-hex-hash>`. Every corrupted copy loses the suffix and exposes plain `LOAD_TYPE` and `RUN_ID`. The suffix is not a proof of correctness, but its absence is a reliable signal that you are not reading the live share.

**Practical rule: point capture at the catalog-linked database itself, never at a copy of it. Validate the metadata with `SP_VALIDATE_STANDARD` before building capture on any standard-track object.**

---

## The connector's merge behaviour - the precondition (custom track)

The custom-track incremental pattern rests on one observable property of the BDC connector: it merges rather than reloads. Historical rows keep their original replication timestamp while new data arrives in new Parquet files.

This is a measured property, not a documented guarantee. On a reference tenant, over five days:

- Initial load: 108,615,638 rows across 402 Parquet files.
- Day 2: 10 rows in 1 new file. Historical rows were unchanged.
- Day 3: 20 rows in 1 new file. Historical rows were unchanged.

The day-2 delta included retroactive updates - rows originally created in May 2025 and February 2026 were rewritten. The connector therefore does not deliver append-only data. A watermark design must MERGE on the business key; an INSERT-only approach will produce duplicate rows on retroactive updates.

**Why this matters.** If the connector reloaded - writing every row on every cycle with a fresh timestamp - then every row would always appear in the watermark window, and the "incremental" read would be a full scan by another name. The merge behaviour is what makes the pruning effective and the pattern viable.

**Verify on your own tenant** before deploying. The query in `sql/00_discover.sql` (step 6) groups rows by write day. The shape to look for is several distinct write days with the bulk of rows on the initial load and small incremental additions since. A single write day covering all rows means the connector either loaded once or reloads fully. Neither supports a watermark.

---

## The watermark column (custom track)

The replication timestamp on the custom track is not a Snowflake or BDC standard field. It is typically a customer extension field added to the SAP source during the SLT pipeline configuration. On the reference tenant it was named `ZZLASTCHGDATE`, typed `TIMESTAMP_NTZ(6)`, and present on all 23 objects in the share. The name varies by pipeline implementation.

Finding it requires reading the column list with `DESCRIBE TABLE` and looking for a timestamp column whose name suggests a load or replication event rather than a business event. A business date such as `PostingDate` or `DocumentDate` is not a replication watermark. The key distinction is whether the value changes when the connector writes the row, or when the business event occurred.

On the reference tenant, `ZZLASTCHGDATE` had 13,684 distinct values on the GL line item object. The timestamps are fine-grained - not one value per connector batch, but many values per run. This fine granularity is part of why per-file Iceberg statistics are effective: files written in a single connector run carry a narrow min/max range, and a predicate against a recent value eliminates most files immediately.

Once you have identified the watermark column, confirm it prunes using the queries in `sql/00_discover.sql` (step 7). The comments in that file give interpretation thresholds for the scan ratio.

---

## Why the watermark prunes and the business date does not

Because the connector writes the replication timestamp in monotonic, non-overlapping batches, the min/max of that column in each Parquet file spans a narrow range. Iceberg stores per-file column statistics. When you filter on the replication timestamp, Snowflake's query planner reads those statistics and eliminates every file whose range cannot contain a match.

A business date such as `PostingDate` is a value written by the business process. Documents from many different time periods land in the same Parquet files, because the connector groups rows by object, not by business date. Per-file statistics on a business date span the full historical range of the object and eliminate nothing.

The following measurements are from a reference tenant (custom track, `ZZLASTCHGDATE`), with `USE_CACHED_RESULT = FALSE` and a data column projected. Note that `COUNT(*)` alone can be answered from Iceberg manifest statistics without reading any data, which would flatter the scan cost and hide the real behaviour - see the comment in `sql/00_discover.sql` step 7 for why this matters.

| Query | Data scanned | Duration (reference tenant) |
| --- | --- | --- |
| Full column read, 7 GB object | 7,035.80 MB | - |
| Watermark predicate, 30 matching rows | 0.00 MB | 28 ms |
| Business-date predicate, 549 matching rows | 4,695.75 MB | 542 ms |
| Full GROUP BY aggregate, 108,615,638 rows | 6.87 GB | 15,078 ms |
| Same aggregate scoped by watermark | 0 GB | 630 ms |

The practical implication: use the replication timestamp as the predicate, not a business date. The difference is not a performance optimisation - it is the difference between an incremental pattern that reads only changed data and one that scans the full object every cycle.

**Standard track - predicate pruning was not demonstrated.** On the standard track, the reference objects were a single micropartition (39,462 rows, 320 KB). The watermark predicate scanned approximately 100% of a full read. The free poll cost (0.000 MB from manifest statistics, 2 ms) is confirmed. Iceberg file elimination on the standard track at production scale is not established and should not be assumed. At this scale, the correct approach is to drive refreshes from the free poll and let dynamic tables do the recomputation.

**Measuring scan cost.** `INFORMATION_SCHEMA.QUERY_HISTORY` provides `BYTES_SCANNED` but not `PARTITIONS_SCANNED` or `PARTITIONS_TOTAL`. Those columns are only available in `ACCOUNT_USAGE.QUERY_HISTORY`, which carries a latency of up to 45 minutes. `BYTES_SCANNED` is sufficient to confirm file elimination. The query history call requires a database context, as shown in `sql/00_discover.sql`.

---

## The four verified query shapes

Dynamic tables with `REFRESH_MODE = INCREMENTAL` were tested across four query shapes over a catalog-linked share (custom track). All four were accepted as incremental, with `refresh_mode_reason = null`. On a reference tenant, second-cycle no-change refreshes returned `REFRESH_ACTION = NO_DATA` in the durations below. Initial refreshes were `INCREMENTAL` with `numInsertedRows` equal to the full row count of the source.

| Shape | Description | NO_DATA duration (reference tenant) |
| --- | --- | --- |
| A | Flat projection over a view that casts dates | 348 ms |
| B | Join of two views | 486 ms |
| C | GROUP BY aggregate | 450 ms |
| D | Left join to a pre-aggregated child CTE | 570 ms |

The SQL for each shape is in `sql/02_dynamic_table_engine.sql`.

**Standard-track dynamic table, verified end to end.** A dynamic table was created over the live CUSTOMER_V1 share with `REFRESH_MODE = INCREMENTAL` (3,355 rows). `SP_POLL_STANDARD` triggered the first refresh; `DYNAMIC_TABLE_REFRESH_HISTORY()` confirmed `INCREMENTAL SUCCEEDED` in 5,241 ms. A second `SP_POLL_STANDARD` call found no advance in the watermark and returned `NO CHANGE`; the dynamic table reported `NO_DATA SUCCEEDED` in 610 ms. `SP_VALIDATE_STANDARD` reported all 3 registered objects USABLE: operation cardinality 2, max length 1, all timestamps parsed, 0 degenerate rows.

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

**`REFRESH_MODE = INCREMENTAL` must be stated explicitly.** Leaving it as `AUTO` allows Snowflake to fall back to `FULL` on a query it cannot support incrementally. That fallback is silent - no error, no warning. Stating `INCREMENTAL` converts the failure into a deployment-time error. Omitting the pin on a non-trivial query also cascades: `FULL` mode disables change tracking on that dynamic table, which forces every downstream dynamic table to `FULL` too. On the custom-track reference tenant, 10 of 10 L2 dynamic tables were verified `INCREMENTAL` after pinning the mode explicitly; before pinning, Snowflake chose `FULL` on several of them.

---

## Choosing a mechanism

Five conclusions are supported by the evidence in this document.

1. **Prefer standard data products where they exist.** Only the standard track carries an operation column. That column makes delete detection structurally possible; it is not available at all on the custom track.

2. **On both tracks, use dynamic tables with `REFRESH_MODE = INCREMENTAL`.** The watermark procedure path (`sql/04_watermark_exception_path.sql` for the custom track, `sql/05_standard_product_track.sql` for the standard track) is the exception: non-deterministic transformations, or an external consumer that requires a physical delta table.

3. **Validate standard metadata before building on it.** Column names alone are not sufficient evidence that the values are correct. Run `SP_VALIDATE_STANDARD` and confirm all registered objects return `USABLE` before scheduling any capture.

4. **Point capture at the catalog-linked database, never a copy of it.** Copies corrupt the metadata, and the corruption produces wrong incremental results without raising any error.

5. **Reconcile row counts on a schedule regardless of track.** Deletes are invisible on the custom track. No delete code was observed on the reference standard-track share. Schedule the delete reconciliation query for every monitored object on both tracks.

---

The three available mechanisms:

| | Dynamic table | Watermark + MERGE | INSERT_ONLY stream |
| --- | --- | --- | --- |
| Inserts | yes | yes | yes |
| Updates | yes | yes | yes |
| **Physical deletes** | **yes** - diffs snapshots | **no** (custom track); unverified (standard track) | **no** - by definition |
| Cheap no-change detection | yes (`NO_DATA`) | yes (0 MB from manifest statistics) | yes |
| Code required | none | task + control table + MERGE | task + MERGE |
| Can feed targets outside Snowflake | no | yes | yes |
| Goes stale if unread | no | no | yes, 14 days |
| Blocked by non-deterministic SQL | yes | no | no |

**Use dynamic tables.** The watermark path is the exception, not the default.

The INSERT_ONLY stream path requires the same procedural code as the watermark path, cannot detect deletes, and carries a 14-day staleness risk the other two paths do not. There is no situation in which it is preferable to the watermark path.

---

## Delete semantics - the single most important difference

**Custom track.** The share carries no change-mode column. There is no `ODQ_CHANGEMODE`, `RECORDMODE`, or `UPDKZ`. A row that disappears from the source leaves no trace in the data itself. The watermark window will never encounter it. The divergence is invisible until you compare row counts between source and target. The delete reconciliation view in `sql/04_watermark_exception_path.sql` performs this comparison and reports a `TARGET HAS ROWS THE SOURCE NO LONGER CARRIES - REBUILD` verdict when the target exceeds the source count. Rebuilding the target is the recommended response; identifying the specific deleted rows is impractical on the watermark path.

**Standard track.** `__OPERATION_TYPE` exists. On the reference share, only `L` (initial load) and `A` (delta after-image) were observed across 67,480 rows in 5 non-empty objects. No delete code appeared anywhere. Whether this data product ever emits a delete code is an open question addressed to SAP; see [Open questions](#open-questions). The MERGE template in `sql/05_standard_product_track.sql` includes a commented DELETE branch that activates if a delete code is observed, and requires the reconciliation query that follows it regardless.

**Dynamic table path (both tracks).** A dynamic table detects a delete by diffing consecutive snapshots of the source. When a row is present in snapshot N and absent in snapshot N+1, the dynamic table removes it from the target. No column flag is required; the absence itself is the signal.

This is the most consequential difference between the watermark and dynamic table paths. If your data product contains objects where physical deletes are meaningful to downstream consumers - cancelled orders, deactivated accounts, removed line items - the watermark path will silently diverge from the source over time.

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

These items were not resolved in the reference measurement windows. They are stated here as open, not settled.

**1. The authoritative `__OPERATION_TYPE` value enumeration (standard track).**

Only `L` and `A` were observed on the CUSTOMER_V1 reference share across all non-empty objects. Whether this product ever emits a delete code is unknown. The SAP Help documentation for this column is JavaScript-rendered and could not be retrieved; a web search surfaced only the SAP Datasphere BigQuery adapter documentation, which covers a different target system and different column names (`operation_flag`, `is_deleted`, values I/U/D), and must not be cited as authoritative for this. The `cdc_readiness.py` probe discovers and reports the actual distribution per tenant rather than assuming an enumeration. The MERGE template in `sql/05_standard_product_track.sql` handles a delete code if it appears; the reconciliation query after it is mandatory until the absence of deletes is confirmed on the specific tenant and product.

**2. What a changed-source dynamic table refresh produces on a production-scale custom-track object.**

On the standard track, an incremental dynamic table refresh was observed: `SP_POLL_STANDARD` triggered a refresh; `DYNAMIC_TABLE_REFRESH_HISTORY()` confirmed `INCREMENTAL SUCCEEDED` in 5,241 ms on a 3,355-row table. The equivalent on a multi-million-row custom-track object - `REFRESH_ACTION = INCREMENTAL` with `ROWS_INSERTED` matching the connector delta count - was not observed in the available window. After your next connector write on the custom track, query `VW_REFRESH_AUDIT` and record the result. If `REFRESH_ACTION = FULL` appears instead, the fallback is the watermark path in `sql/04_watermark_exception_path.sql`, whose cost is measured.

**3. Whether an INSERT_ONLY stream yields rows after a source change.**

The stream was created and correctly returned zero rows at creation. A subsequent connector write was not exercised against it. This only matters if the stream path is adopted, which is not the recommendation.

None of these gaps change the recommendation. The design is not contingent on any of them being settled in a particular way.
