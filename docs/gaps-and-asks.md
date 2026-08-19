# Gaps and asks

What incremental capture from an SAP BDC catalog-linked share solves, what it does not solve, known limitations, and the changes to the platform and data products that would improve the pattern.

---

## What capture solves and what it does not

These two complaints arrive together. They require different conversations with different owners.

| Complaint | Category | Solved here? |
| --- | --- | --- |
| "The data is hours or a day behind" | Freshness - the consuming layer re-reads the entire share on a schedule because nothing tells it what changed | Yes. Dynamic tables with incremental refresh detect no-change in a few hundred milliseconds and read only changed rows otherwise. |
| "The ledger has no amounts" | Completeness - a field or object was never included in the SAP data product | No. No refresh frequency fixes a column that was never shared. Delivering fifteen-minute freshness on a ledger with no amount columns answers the wrong question convincingly. |
| "There is no customer master" | Completeness - an entire object is absent from the share | No. The data product must be extended on the SAP side. |

Capture is a platform mechanism layered over the share. The content of the share is a data product decision made on the SAP side. Before investing in deployment, confirm that the objects and fields the consuming team actually needs are present in the share.

---

## Limitations

### Physical delete detection on the watermark path

**Severity: high.**

The SAP BDC share carries no change-mode column. There is no `ODQ_CHANGEMODE`, `RECORDMODE`, or `UPDKZ`. A row that disappears from the source leaves no trace in the data. The watermark window will never encounter it.

On the watermark path, the only available detection is a count comparison between source and target (`VW_DELETE_RECONCILIATION` in `sql/04_watermark_exception_path.sql`). A persistent positive divergence means rows were deleted upstream. Identifying which rows were deleted is impractical; rebuilding the target is the recommended response.

Dynamic tables handle physical deletes correctly by diffing consecutive source snapshots. This is the primary reason dynamic tables are the recommended path.

### Watermark column name varies by pipeline implementation

**Severity: medium.**

The replication timestamp is a customer extension field added to the SAP source during SLT pipeline configuration. Its name - `ZZLASTCHGDATE` on the reference tenant - is set per BDC pipeline implementation. It may differ between data products on the same tenant, or be absent on a BDC deployment that was not configured to include it. The readiness assessment discovers the column name; do not assume a name.

### Grain not declared on catalog-linked tables

**Severity: medium.**

Catalog-linked tables carry no primary key constraint. The correct merge key for some SAP objects is not obvious from the column names. Purchase order history (EKBE) requires a seven-part key including `AccountAssignmentNumber` and `PurchasingHistoryCategory`. On a five-part key the table appears to hold duplicates, and de-duplicating on that key silently discards real goods movements without any error. Confirm the grain before writing any MERGE statement.

### `INFORMATION_SCHEMA.COLUMNS` returns no rows

**Severity: low - operationally inconvenient.**

Column discovery requires `DESCRIBE TABLE`, one statement per object. There is no bulk alternative. On a share with many objects, this is a scripting problem rather than a blocker, but it prevents using standard information-schema tooling for documentation or impact analysis.

### Schema and object names require quoting

**Severity: low.**

Schema names contain a colon (for example `"dp_finance_v2_srv:v1"`). Object names are mixed case. Every reference in SQL must be double-quoted. This is a consistent rule once known, but it causes confusing errors on first encounter and must be applied in every tool that generates SQL against the share.

### Non-deterministic expressions block incremental refresh

**Severity: medium - design constraint.**

`CURRENT_DATE()`, `CURRENT_TIMESTAMP()`, and `RANDOM()` inside a dynamic table's defining query force a full refresh on every cycle. Month-to-date and rolling-window calculations are the common cases. The pattern is to put time-relative logic in a view over the dynamic table. This is a design constraint, not an error, but it requires knowing the rule before writing the query.

### Parquet compaction degrades watermark pruning

**Severity: low - latent.**

The watermark's near-zero scan cost depends on per-file Iceberg statistics spanning a narrow range. If SAP compacts the underlying Parquet files - merging many small files into fewer large ones - the per-file ranges widen and file elimination degrades toward a full scan. The file count monitor in `sql/03_monitoring.sql` detects this. If it fires, re-run the pruning probe in `sql/00_discover.sql` to measure the new scan ratio. Dynamic table refreshes are unaffected because they do not depend on the watermark predicate.

### Time travel limited to 24 hours

**Severity: low.**

`DATA_RETENTION_TIME_IN_DAYS` is fixed at 1 on the catalog-linked share and cannot be altered. Time travel is unavailable at 36 hours. This rules out `AT(OFFSET => ...)` for historical comparisons on the source objects beyond 24 hours.

### INSERT_ONLY stream expires if unread

**Severity: low (relevant only if adopting the stream path).**

An INSERT_ONLY stream expires after 14 days if not consumed. A dynamic table does not have a staleness window. This is one of several reasons the stream path is not recommended.

---

## Risks

| Risk | How it would present | Detection |
| --- | --- | --- |
| BDC connector switches from merge to reload | Dynamic table refreshes become as expensive as a full rebuild every cycle; no error is raised. Watermark procedure processes every row every cycle. Neither path produces incorrect results; cost and lag increase. | `VW_RELOAD_DETECTION.CAPTURE_MODE = 'FULL RELOAD DETECTED'`; hourly alert in `AL_CAPTURE_HEALTH`. |
| Non-deterministic expression enters a dynamic table query | `REFRESH_ACTION` becomes `FULL` on every cycle; cost rises silently. No error is raised. | `VW_REFRESH_AUDIT.UNEXPECTED_FULL_REFRESH = TRUE`; `SHOW DYNAMIC TABLES` exposes `refresh_mode_reason`. |
| Connector stops writing | Dynamic table continues to report `NO_DATA` and appears healthy. Downstream consumers receive stale data with no signal. | `VW_RELOAD_DETECTION.CAPTURE_MODE = 'CONNECTOR STALLED'` when `LAST_WRITE` exceeds the staleness threshold. |
| Parquet compaction widens file ranges | Watermark predicate scan cost rises toward a full scan; no error is raised. Dynamic tables are unaffected. | `VW_CAPTURE_EXCEPTIONS` `FILE_COUNT_DROP` row; file count drops below 70% of the baseline. |
| Wrong merge key chosen (watermark path) | Duplicate rows in the target, or real records silently discarded. No constraint violation is raised. | Row count comparison between source and target; business-level data quality audits. |

---

## Asks for SAP

These changes to BDC data products would make the pattern more reliable, close the delete gap, and reduce the risk of deployment errors.

### 1. A guaranteed load timestamp on every object

The replication timestamp is a customer extension field. It is absent on some deployments and may vary in name between data products on the same tenant. A first-class `_BDC_LOAD_TS` column with a guaranteed name on every shared object would remove the discovery step, make the readiness assessment unnecessary, and give all consumers a consistent watermark predicate.

### 2. A change-mode column on every object

Without a column that distinguishes insert, update, and delete at row level, physical deletes are undetectable on the watermark path. An `_BDC_CHANGE_MODE` column - equivalent to `RECORDMODE` in classic SAP delta queues - would close this gap for the watermark path without requiring dynamic table snapshot diffing. It would also allow streams to report deletes if Snowflake adds support for `METADATA$ACTION` on externally catalogued Iceberg (see Snowflake ask 1 below).

### 3. Documented grain and primary key per object

Catalog-linked tables carry no primary key constraint. For most objects, the business key is published in SAP documentation. For several it is not obvious. A machine-readable key specification in the data product metadata, or a supplementary reference document covering every object in each data product, would prevent silent merge key errors.

---

## Asks for Snowflake product management

These asks are derived from the platform refusals documented in [how-it-works.md](how-it-works.md#what-the-platform-refuses). Each is a direct consequence of a specific error message.

### 1. `CHANGES()` clause on externally catalogued Iceberg tables

Currently refused with: *CHANGES clause is not supported on external tables or Iceberg tables with an external catalog.*

A read-only `CHANGES()` implementation that reads Iceberg snapshot metadata - without requiring Snowflake to own or write to the catalog - would give consumers a standard CDC interface. The Iceberg format already stores snapshot history; the information required to compute a change set is present in the catalog.

### 2. Change tracking on externally catalogued Iceberg tables

Currently refused with: *change_tracking cannot be set for Iceberg tables unless they use Snowflake catalog.*

Even a read-only implementation that consumes existing Iceberg snapshot history, rather than writing CDC metadata, would allow streams to expose `METADATA$ACTION` and `METADATA$ISUPDATE` to consumers. This would also enable the delete detection that `_BDC_CHANGE_MODE` would otherwise provide.

### 3. Longer time travel on catalog-linked shares

`DATA_RETENTION_TIME_IN_DAYS` is fixed at 1 on the linked objects and cannot be altered. Seven-day time travel would support historical queries, some recovery scenarios, and point-in-time snapshot comparisons that are currently impossible without the consumer copying the data.

### 4. A first-class change feed for catalog-linked databases

The current options are INSERT_ONLY streams (no deletes, 14-day expiry, no standard metadata columns) and dynamic tables (full snapshot diff, no direct stream interface). A purpose-built change feed that reads Iceberg snapshot diffs and exposes them as a standard Snowflake stream - without requiring the consumer to own the catalog - would be the complete long-term answer. The four items above are partial mitigations; this is what they collectively point toward.

---

## Open questions

Two things were not observable in the five-day reference window. They are stated here as open, not settled. Both appear in [how-it-works.md](how-it-works.md#open-questions); they are repeated here because their resolution affects the confidence level of the pattern.

**1. What a changed-source dynamic table refresh produces.**

Initial refresh was `INCREMENTAL`. A no-change refresh returned `NO_DATA`. A refresh following a genuine connector write was not observed. The expected behaviour is `REFRESH_ACTION = INCREMENTAL` with `ROWS_INSERTED` matching the changed row count. If instead `REFRESH_ACTION = FULL` is observed, the pattern's cost model changes: each refresh after a connector write would scan the full object. The fallback in that case is the watermark path in `sql/04_watermark_exception_path.sql`, whose cost is measured.

To confirm: after your next connector write, query `VW_REFRESH_AUDIT` and record the result.

**2. Whether an INSERT_ONLY stream yields rows after a source change.**

The stream was created and returned zero rows at creation. A subsequent connector write was not exercised against it. This only matters if the stream path is adopted, which is not the recommendation. Resolution is low priority unless a stream-based integration with an external target is being considered.
