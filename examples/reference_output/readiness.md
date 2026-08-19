# SAP BDC change data capture readiness

**Verdict: READY**

- Dynamic tables refresh incrementally over the share and detect no-change cheaply. This is the recommended engine and needs no code.
- A pruning watermark is also available for the exception path - non-deterministic transformations and external consumers.

Assessed 2026-08-19T17:12:04+00:00.

> Anonymised sample from a reference tenant, included so you can see the shape of the
> output before running it. Database names, object names, row counts and byte figures
> will all differ on your account. The `evidence` blocks in the JSON output carry the
> raw measurement behind each line.

## Findings

| Check | Status | Detail |
| --- | --- | --- |
| `share.present` | PASS | 4 catalog-linked database(s) found by kind (names redacted) |
| `share.inventory` | INFO | 23 objects, 1825 columns |
| `signal.watermark` | PASS | Replication timestamp present on all 23 objects (column: ZZLASTCHGDATE) |
| `signal.merge_or_reload` | PASS | 3 write days, 100.0000% of rows on the initial load and small deltas since. The connector merges, so a watermark is viable. |
| `cost.pruning` | PASS | Watermark predicate scans 0.00 MB against 7,036.05 MB for a full read - 0.0000%. Near-complete file elimination. |
| `capability.stream_standard` | INFO | Standard stream refused; INSERT_ONLY required. Expected for an Iceberg table on an external catalog. |
| `capability.changes_clause` | INFO | CHANGES clause unsupported, as expected on an external catalog. |
| `capability.stream_metadata` | INFO | Stream metadata columns do not resolve; change tracking is not active. |
| `capability.file_metadata` | PASS | METADATA$FILENAME available, so file-level change detection is possible. |
| `capability.time_travel` | INFO | Time travel reaches about 24 hour(s). |
| `capability.dynamic_table` | PASS | Incremental refresh accepted, and a no-change refresh returned NO_DATA in 305 ms - Snowflake established that nothing changed without recomputing the source. |

## What to do next

If the verdict is READY, configure your Gold layer dynamic tables with
`REFRESH_MODE = INCREMENTAL` stated explicitly and set `TARGET_LAG` per object
class. See `docs/deployment.md` and `sql/`.

Whatever the verdict, deploy `sql/03_monitoring.sql`. It detects the single
event that invalidates the whole pattern: the connector switching from
incremental merge to full reload.
