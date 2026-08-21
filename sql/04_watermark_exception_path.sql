/* ============================================================================
   04  Watermark path - the exception, not the default
   ----------------------------------------------------------------------------
   Use this ONLY where a dynamic table cannot serve:

     1. the transformation contains a non-deterministic expression, which forces a
        full refresh and defeats incremental capture
     2. the target is outside Snowflake, and a dynamic table cannot push to it

   Everywhere else, use 02_dynamic_table_engine.sql. This path is strictly worse in
   one important respect: on a CUSTOM data product it CANNOT DETECT PHYSICAL
   DELETES. Such a share carries no change-mode column - no ODQ_CHANGEMODE,
   RECORDMODE or UPDKZ - so a row that the share stops carrying leaves no trace for
   a watermark to find. A dynamic table detects the same event, because it diffs
   source snapshots rather than reading a flag.

   STANDARD data products are different and this file is not the right place for
   them. They do carry a change-mode column, `__OPERATION_TYPE`, so a delete could
   in principle be applied - but on the reference share only 'L' and 'A' were ever
   observed and no delete code appeared at all, and the authoritative value list
   could not be confirmed from SAP documentation. Use 05_standard_product_track.sql
   for those objects: the watermark there is TEXT and has to be compared as text to
   stay cheap, which the procedure below does not do.

   Either way, deletes are the weak point of the watermark path. If you adopt it,
   schedule the reconciliation query at the bottom - it is not optional.
   ========================================================================== */

/* ---------------------------------------------------------------------------
   Register the object. MERGE_KEY_COLUMNS must come from a verified grain, not a
   guess: a catalog-linked table declares no primary key, and getting the grain
   wrong here duplicates rows or discards real records without any error.

   TRACK = 'CUSTOM' because this object carries a source-system extension
   timestamp rather than the connector's own metadata columns. On a standard data
   product register with TRACK = 'STANDARD' and use sql/05 instead: the watermark
   there is text and must be compared as text to stay cheap, and an operation
   column is available. The column is NOT NULL and constrained, so this cannot be
   left unset by accident.
   --------------------------------------------------------------------------- */
INSERT INTO <L2_DB>.CAPTURE_CONTROL.WATERMARK
  (OBJECT_NAME, SOURCE_FQN, TRACK, WATERMARK_COLUMN, MERGE_KEY_COLUMNS, LAST_WATERMARK)
SELECT 'SO_ITEM',
       '<L1_DB>.<L1_SCHEMA>.SALES_ORDER_ITEM',
       'CUSTOM',
       'ZZLASTCHGDATE',
       'SALESDOCUMENT,SALESDOCUMENTITEM',
       NULL
WHERE  NOT EXISTS (SELECT 1 FROM <L2_DB>.CAPTURE_CONTROL.WATERMARK
                   WHERE OBJECT_NAME = 'SO_ITEM');

/* The persisted target. Unlike a dynamic table this is an ordinary table and you
   own its lifecycle. */
CREATE TABLE IF NOT EXISTS <L2_DB>.<SCHEMA>.T_SO_ITEM (
  SALESDOCUMENT      VARCHAR,
  SALESDOCUMENTITEM  VARCHAR,
  MATERIAL           VARCHAR,
  NETAMOUNT          NUMBER(38,6),
  ORDERQUANTITY      NUMBER(38,6),
  SOURCE_WRITE_TS    TIMESTAMP_NTZ,
  CONSTRAINT PK_T_SO_ITEM PRIMARY KEY (SALESDOCUMENT, SALESDOCUMENTITEM)
);

/* ---------------------------------------------------------------------------
   The capture procedure.

   Two details are not incidental:

     The high-water mark is captured BEFORE reading data, not derived from the
     rows afterwards. A row written while the run is in progress is then picked up
     on the next cycle rather than skipped.

     The range is closed at both ends, which makes a failed run safe to repeat.
     An open upper bound would consume a different set of rows on each attempt.
   --------------------------------------------------------------------------- */
CREATE OR REPLACE PROCEDURE <L2_DB>.CAPTURE_CONTROL.SP_CAPTURE_SO_ITEM()
RETURNS VARCHAR
LANGUAGE SQL
AS
$$
DECLARE
  v_from TIMESTAMP_NTZ;
  v_to   TIMESTAMP_NTZ;
  v_rows NUMBER;
BEGIN
  UPDATE <L2_DB>.CAPTURE_CONTROL.WATERMARK
     SET LAST_RUN_STARTED = CURRENT_TIMESTAMP(), LAST_RUN_STATUS = 'RUNNING'
   WHERE OBJECT_NAME = 'SO_ITEM';

  SELECT COALESCE(LAST_WATERMARK, '1900-01-01'::TIMESTAMP_NTZ)
    INTO v_from
    FROM <L2_DB>.CAPTURE_CONTROL.WATERMARK
   WHERE OBJECT_NAME = 'SO_ITEM';

  SELECT MAX(ZZLASTCHGDATE) INTO v_to
    FROM <L1_DB>.<L1_SCHEMA>.SALES_ORDER_ITEM;

  IF (v_to IS NULL OR v_to <= :v_from) THEN
    UPDATE <L2_DB>.CAPTURE_CONTROL.WATERMARK
       SET LAST_RUN_ROWS = 0, LAST_RUN_STATUS = 'NO_DATA',
           UPDATED_AT = CURRENT_TIMESTAMP()
     WHERE OBJECT_NAME = 'SO_ITEM';
    RETURN 'no change since ' || :v_from;
  END IF;

  MERGE INTO <L2_DB>.<SCHEMA>.T_SO_ITEM AS t
  USING (
    SELECT SALESDOCUMENT, SALESDOCUMENTITEM, MATERIAL, NETAMOUNT,
           ORDERQUANTITY, ZZLASTCHGDATE
    FROM   <L1_DB>.<L1_SCHEMA>.SALES_ORDER_ITEM
    WHERE  ZZLASTCHGDATE >  :v_from
      AND  ZZLASTCHGDATE <= :v_to
  ) AS s
     ON t.SALESDOCUMENT     = s.SALESDOCUMENT
    AND t.SALESDOCUMENTITEM = s.SALESDOCUMENTITEM
  WHEN MATCHED THEN UPDATE SET
        t.MATERIAL        = s.MATERIAL,
        t.NETAMOUNT       = s.NETAMOUNT,
        t.ORDERQUANTITY   = s.ORDERQUANTITY,
        t.SOURCE_WRITE_TS = s.ZZLASTCHGDATE
  WHEN NOT MATCHED THEN INSERT
       (SALESDOCUMENT, SALESDOCUMENTITEM, MATERIAL, NETAMOUNT,
        ORDERQUANTITY, SOURCE_WRITE_TS)
  VALUES (s.SALESDOCUMENT, s.SALESDOCUMENTITEM, s.MATERIAL, s.NETAMOUNT,
          s.ORDERQUANTITY, s.ZZLASTCHGDATE);

  v_rows := SQLROWCOUNT;

  UPDATE <L2_DB>.CAPTURE_CONTROL.WATERMARK
     SET LAST_WATERMARK = :v_to,
         LAST_RUN_ROWS  = :v_rows,
         LAST_RUN_STATUS = 'OK',
         UPDATED_AT     = CURRENT_TIMESTAMP()
   WHERE OBJECT_NAME = 'SO_ITEM';

  RETURN 'merged ' || :v_rows || ' rows up to ' || :v_to;
EXCEPTION
  WHEN OTHER THEN
    /* leave LAST_WATERMARK untouched so the next run repeats the same closed
       range rather than skipping it */
    UPDATE <L2_DB>.CAPTURE_CONTROL.WATERMARK
       SET LAST_RUN_STATUS = 'ERROR: ' || SQLERRM, UPDATED_AT = CURRENT_TIMESTAMP()
     WHERE OBJECT_NAME = 'SO_ITEM';
    RAISE;
END;
$$;

/* Schedule it. */
CREATE OR REPLACE TASK <L2_DB>.CAPTURE_CONTROL.TK_CAPTURE_SO_ITEM
  WAREHOUSE = <L2_WH>
  SCHEDULE  = '15 MINUTE'
AS CALL <L2_DB>.CAPTURE_CONTROL.SP_CAPTURE_SO_ITEM();

ALTER TASK <L2_DB>.CAPTURE_CONTROL.TK_CAPTURE_SO_ITEM RESUME;

/* ---------------------------------------------------------------------------
   Delete reconciliation - MANDATORY on this path.

   The watermark cannot see a row the share has stopped carrying. Compare counts
   against the source on a schedule; a persistent positive divergence means rows
   were deleted upstream and must be removed here. Rebuilding the target is
   usually cheaper and safer than trying to identify them.
   --------------------------------------------------------------------------- */
CREATE OR REPLACE VIEW <L2_DB>.CAPTURE_CONTROL.VW_DELETE_RECONCILIATION AS
SELECT 'SO_ITEM' AS OBJECT_NAME,
       (SELECT COUNT(*) FROM <L2_DB>.<SCHEMA>.T_SO_ITEM)                  AS TARGET_ROWS,
       (SELECT COUNT(*) FROM <L1_DB>.<L1_SCHEMA>.SALES_ORDER_ITEM)        AS SOURCE_ROWS,
       (SELECT COUNT(*) FROM <L2_DB>.<SCHEMA>.T_SO_ITEM)
         - (SELECT COUNT(*) FROM <L1_DB>.<L1_SCHEMA>.SALES_ORDER_ITEM)    AS DIVERGENCE,
       IFF((SELECT COUNT(*) FROM <L2_DB>.<SCHEMA>.T_SO_ITEM)
             > (SELECT COUNT(*) FROM <L1_DB>.<L1_SCHEMA>.SALES_ORDER_ITEM),
           'TARGET HAS ROWS THE SOURCE NO LONGER CARRIES - REBUILD',
           'OK')                                                          AS VERDICT;
