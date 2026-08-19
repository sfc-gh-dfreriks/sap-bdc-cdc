/* ============================================================================
   03  Monitoring
   ----------------------------------------------------------------------------
   Deploy this even if you deploy nothing else.

   The pattern rests on one behaviour: the BDC connector merges rather than
   reloads. If that changes, incremental refresh still produces CORRECT results -
   it simply becomes as expensive as a full rebuild, and the watermark path starts
   reprocessing every row on every cycle. Neither failure raises an error. The
   only way to know is to measure.

   A second, subtler risk: if SAP ever compacts the underlying Parquet files, rows
   with old watermarks are rewritten into new files, per-file min/max ranges widen,
   and pruning degrades from near-zero toward a full scan. The file count in the
   view below is what detects that.

   Substitute <L2_DB> and the linked-share references.
   ========================================================================== */

/* ---------------------------------------------------------------------------
   1. Reload detection. One row per monitored object.

      Add a UNION ALL branch per object you care about. Monitoring the largest
      one or two per data product is normally sufficient - the connector's mode
      is a property of the pipeline, not of an individual table.
   --------------------------------------------------------------------------- */
CREATE OR REPLACE VIEW <L2_DB>.CAPTURE_CONTROL.VW_RELOAD_DETECTION AS
WITH per_object AS (

  SELECT 'GL_LINE_ITEM'                       AS OBJECT_NAME,
         COUNT(*)                             AS TOTAL_ROWS,
         SUM(IFF(ZZLASTCHGDATE >= DATEADD('hour', -24, CURRENT_TIMESTAMP()), 1, 0))
                                              AS ROWS_STAMPED_24H,
         COUNT(DISTINCT METADATA$FILENAME)    AS FILE_COUNT,
         MAX(ZZLASTCHGDATE)                   AS LAST_WRITE
  FROM   <FINANCE_LINKED_DB>."<finance_schema:v1>"."<gl_line_item_object>"

  UNION ALL

  SELECT 'SALES_ORDER_ITEM',
         COUNT(*),
         SUM(IFF(ZZLASTCHGDATE >= DATEADD('hour', -24, CURRENT_TIMESTAMP()), 1, 0)),
         COUNT(DISTINCT METADATA$FILENAME),
         MAX(ZZLASTCHGDATE)
  FROM   <SALES_LINKED_DB>."<sales_schema:v1>"."<sales_order_item_object>"

)
SELECT OBJECT_NAME,
       TOTAL_ROWS,
       ROWS_STAMPED_24H,
       FILE_COUNT,
       LAST_WRITE,
       ROUND(100.0 * ROWS_STAMPED_24H / NULLIF(TOTAL_ROWS, 0), 6) AS PCT_RESTAMPED,
       DATEDIFF('hour', LAST_WRITE, CURRENT_TIMESTAMP())          AS HOURS_SINCE_WRITE,
       CASE
         /* A full reload restamps everything. Normal incremental behaviour restamps
            a vanishingly small fraction - on a reference tenant, 30 rows out of
            108.6 million over two days, or 0.00003 per cent. Half is therefore a
            threshold that cannot fire on healthy operation. */
         WHEN ROWS_STAMPED_24H > 0.50 * TOTAL_ROWS          THEN 'FULL RELOAD DETECTED'
         /* The connector has stopped. Every dynamic table refresh will continue to
            report success and NO_DATA, so this is invisible without a staleness
            test. Tune the threshold to the connector's observed cadence. */
         WHEN DATEDIFF('hour', LAST_WRITE, CURRENT_TIMESTAMP()) > 24
                                                            THEN 'CONNECTOR STALLED'
         ELSE 'INCREMENTAL - HEALTHY'
       END                                                        AS CAPTURE_MODE
FROM   per_object;

/* ---------------------------------------------------------------------------
   2. Capture the baseline once, immediately after deploying the view above.
      Drift is only meaningful against a recorded starting point.
   --------------------------------------------------------------------------- */
INSERT INTO <L2_DB>.CAPTURE_CONTROL.CAPTURE_BASELINE
  (OBJECT_NAME, TOTAL_ROWS, FILE_COUNT, DISTINCT_STAMPS, LAST_WRITE, PCT_RESTAMPED_24H)
SELECT OBJECT_NAME, TOTAL_ROWS, FILE_COUNT, NULL, LAST_WRITE, PCT_RESTAMPED
FROM   <L2_DB>.CAPTURE_CONTROL.VW_RELOAD_DETECTION;

/* ---------------------------------------------------------------------------
   3. The single view to alert on. Everything that should page someone, in one
      place, so an alert has exactly one dependency.
   --------------------------------------------------------------------------- */
CREATE OR REPLACE VIEW <L2_DB>.CAPTURE_CONTROL.VW_CAPTURE_EXCEPTIONS AS
SELECT 'CAPTURE_MODE'  AS EXCEPTION_TYPE,
       OBJECT_NAME     AS SUBJECT,
       CAPTURE_MODE    AS DETAIL,
       'HIGH'          AS SEVERITY
FROM   <L2_DB>.CAPTURE_CONTROL.VW_RELOAD_DETECTION
WHERE  CAPTURE_MODE <> 'INCREMENTAL - HEALTHY'

UNION ALL

/* A steady-state refresh doing full work. Silent, and the most likely cost
   regression in this pattern. */
SELECT 'UNEXPECTED_FULL_REFRESH',
       NAME,
       'REFRESH_ACTION = FULL at ' || TO_VARCHAR(REFRESH_START_TIME),
       'HIGH'
FROM   <L2_DB>.CAPTURE_CONTROL.VW_REFRESH_AUDIT
WHERE  UNEXPECTED_FULL_REFRESH
  AND  REFRESH_START_TIME >= DATEADD('hour', -24, CURRENT_TIMESTAMP())

UNION ALL

/* Pruning degradation. Compare the live file count against the baseline: a sharp
   drop means compaction, which widens per-file watermark ranges and erodes the
   file elimination the watermark path depends on. */
SELECT 'FILE_COUNT_DROP',
       d.OBJECT_NAME,
       'files ' || b.FILE_COUNT || ' -> ' || d.FILE_COUNT ||
         ' (possible compaction; re-measure pruning)',
       'MEDIUM'
FROM       <L2_DB>.CAPTURE_CONTROL.VW_RELOAD_DETECTION d
JOIN       <L2_DB>.CAPTURE_CONTROL.CAPTURE_BASELINE    b
        ON b.OBJECT_NAME = d.OBJECT_NAME
WHERE  d.FILE_COUNT < 0.70 * b.FILE_COUNT

UNION ALL

/* A dynamic table that has stopped being scheduled. */
SELECT 'DT_SUSPENDED', NAME, 'scheduling_state is not ACTIVE', 'HIGH'
FROM   <L2_DB>.CAPTURE_CONTROL.VW_REFRESH_AUDIT
WHERE  STATE NOT IN ('SUCCEEDED', 'RUNNING')
  AND  REFRESH_START_TIME >= DATEADD('hour', -24, CURRENT_TIMESTAMP());

/* ---------------------------------------------------------------------------
   4. Alert. One dependency, one condition.
   --------------------------------------------------------------------------- */
CREATE OR REPLACE ALERT <L2_DB>.CAPTURE_CONTROL.AL_CAPTURE_HEALTH
  WAREHOUSE = <L2_WH>
  SCHEDULE  = '60 MINUTE'
IF (EXISTS (
  SELECT 1 FROM <L2_DB>.CAPTURE_CONTROL.VW_CAPTURE_EXCEPTIONS WHERE SEVERITY = 'HIGH'
))
THEN
  CALL SYSTEM$SEND_EMAIL(
    '<NOTIFICATION_INTEGRATION>',
    '<ops@example.com>',
    'SAP BDC capture health: exception raised',
    'One or more HIGH severity capture exceptions are open. Query '
    || '<L2_DB>.CAPTURE_CONTROL.VW_CAPTURE_EXCEPTIONS for detail.');

ALTER ALERT <L2_DB>.CAPTURE_CONTROL.AL_CAPTURE_HEALTH RESUME;

/* ---------------------------------------------------------------------------
   5. Day-to-day queries
   --------------------------------------------------------------------------- */
-- is capture healthy right now?
SELECT * FROM <L2_DB>.CAPTURE_CONTROL.VW_RELOAD_DETECTION;

-- what has refreshed recently, and did any of it do full work?
SELECT NAME, REFRESH_ACTION, DURATION_MS, ROWS_INSERTED, ROWS_DELETED
FROM   <L2_DB>.CAPTURE_CONTROL.VW_REFRESH_AUDIT
WHERE  REFRESH_START_TIME >= DATEADD('day', -1, CURRENT_TIMESTAMP())
ORDER  BY REFRESH_START_TIME DESC;

-- anything open?
SELECT * FROM <L2_DB>.CAPTURE_CONTROL.VW_CAPTURE_EXCEPTIONS ORDER BY SEVERITY, SUBJECT;
