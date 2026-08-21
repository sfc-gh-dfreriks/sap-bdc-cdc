# SAP BDC change data capture readiness

**Verdict: READY WITH CAVEATS**

- The change signal is present and usable, but the recommended mechanism was not proven: re-run with --with-scratch-tests to confirm that a dynamic table refreshes incrementally over this share rather than inferring it.
- The watermark prunes, so the exception path is available regardless.

Assessed 2026-08-21T16:59:11+00:00.

## Findings

| Check | Status | Detail |
| --- | --- | --- |
| `share.present` | PASS | 4 catalog-linked database(s): DP_FINANCE_V2_DELTA_SHARING_V1, DP_MASTER_DATA_DELTA_SHARING_V1, DP_PROCUREMENT_V2_DELTA_SHARING_V1, DP_SALES_DISTRIBUTION_DELTA_SHARING_V1 |
| `share.inventory` | INFO | 23 objects, 1825 columns |
| `signal.standard` | INFO | No standard data products found. 23 object(s) carry a custom extension timestamp only. |
| `signal.watermark` | PASS | Replication timestamp present on all 23 objects (column: ZZLASTCHGDATE) |
| `signal.merge_or_reload` | PASS | zcds_i_glacctlineitemrawdata: 5 write days, 100.0000% of rows on the initial load and small deltas since. The connector merges, so a watermark is viable. |
| `cost.pruning` | PASS | Watermark predicate scans 0.00 MB against 7,035.80 MB for a full read - 0.0000%. Near-complete file elimination. |
| `capability.dynamic_table` | INFO | Not tested. Re-run with --with-scratch-tests to prove the recommended mechanism rather than infer it. |

## Which track applies

- **Standard data products: 0 object(s).** SAP generates `__OPERATION_TYPE`, `__TIMESTAMP` and `load_type` / `run_id`. Poll `MAX("__TIMESTAMP")` on the raw text - it is answered from Iceberg manifest statistics, and parsing it before comparing turns a free poll into a full scan. The format is fixed-width ISO-8601, so text order is chronological order.
- **Custom data products: 23 object(s).** Poll the extension timestamp, typically `ZZLASTCHGDATE`. There is no operation column, so deletes are invisible on this track and row counts have to be reconciled separately.

Where an object has both, prefer the standard track: only it can represent a delete.

## What to do next

If the verdict is READY, configure your Gold layer dynamic tables with
`REFRESH_MODE = INCREMENTAL` stated explicitly and set `TARGET_LAG` per object
class. See `docs/how-it-works.md` and `sql/`.

Before building anything on the standard metadata columns, read the
`semantics.trustworthy` finding. Those columns are reliable on a live
catalog-linked share and were found corrupted on every local copy of one on
the reference tenant - holding business data, or duplicating each other. A
corrupt operation column does not fail loudly; it produces a wrong
incremental result quietly.

Whatever the verdict, deploy `sql/03_monitoring.sql`. It detects the single
event that invalidates the whole pattern: the connector switching from
incremental merge to full reload.
