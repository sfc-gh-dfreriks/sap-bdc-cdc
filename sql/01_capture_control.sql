/* ============================================================================
   01  Capture control schema
   ----------------------------------------------------------------------------
   Three objects. Deploy these FIRST, before configuring any dynamic table, so
   that you have a baseline to compare against and can detect the one event that
   invalidates the whole pattern.

   Substitute <L2_DB> for your Gold database.
   ========================================================================== */

CREATE SCHEMA IF NOT EXISTS <L2_DB>.CAPTURE_CONTROL
  COMMENT = 'Change data capture state, audit and health for the SAP BDC share.';

/* ---------------------------------------------------------------------------
   1. Watermark state. Only needed for the exception path (see 04). Dynamic
      tables keep their own state and need nothing here.

      MERGE_KEY_COLUMNS is deliberately a required column with no default. The
      correct grain of an SAP object is not discoverable from the share - a
      catalog-linked table declares no primary key - and two objects in a typical
      BDC share have keys a modeller would not guess. Purchase order history, for
      instance, needs all seven parts of the EKBE key including the account
      assignment number and the history category; on a five-part key it appears
      to hold duplicates, and de-duplicating on that key silently discards real
      goods movements.

      TRACK records which change signal the object carries, because the two are
      not interchangeable:

        CUSTOM    a source-system extension timestamp, typically ZZLASTCHGDATE,
                  already typed as TIMESTAMP_NTZ. No operation column, so a
                  delete in the source is indistinguishable from no change.
        STANDARD  the connector's own __TIMESTAMP, which arrives as TEXT in
                  ISO-8601 with a comma decimal separator
                  (2026-06-15T09:41:59,6426580Z).

      Hence two watermark columns rather than one. LAST_WATERMARK_TEXT is not a
      convenience: on the standard track the comparison must stay in text.
      MAX() over the raw column is answered from Iceberg manifest statistics and
      scans nothing, while MAX() over a parsed expression reads the object -
      measured at 0.000 MB against 0.310 MB on a small reference table, and the
      gap grows with the object. The format is fixed-width and zero-padded, so
      lexicographic order is chronological order and comparing as text is
      correct as well as cheap. Parse it for display, never for comparison.
   --------------------------------------------------------------------------- */
CREATE TABLE IF NOT EXISTS <L2_DB>.CAPTURE_CONTROL.WATERMARK (
  OBJECT_NAME          VARCHAR       NOT NULL PRIMARY KEY,
  SOURCE_FQN           VARCHAR       NOT NULL,
  TRACK                VARCHAR       NOT NULL
      CONSTRAINT CK_WATERMARK_TRACK CHECK (TRACK IN ('CUSTOM', 'STANDARD')),
  WATERMARK_COLUMN     VARCHAR       NOT NULL,
  MERGE_KEY_COLUMNS    VARCHAR       NOT NULL,
  OPERATION_COLUMN     VARCHAR,          -- STANDARD only; NULL on the custom track
  LAST_WATERMARK       TIMESTAMP_NTZ,    -- CUSTOM track
  LAST_WATERMARK_TEXT  VARCHAR,          -- STANDARD track, compared as text
  LAST_RUN_STARTED     TIMESTAMP_NTZ,
  LAST_RUN_ROWS        NUMBER,
  LAST_RUN_STATUS      VARCHAR,
  UPDATED_AT           TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()
);

/* ---------------------------------------------------------------------------
   2. Refresh audit. Reads Snowflake's own history rather than maintaining a log,
      so it cannot drift from reality.

      Note STATISTICS is returned as an OBJECT, not a VARCHAR. It is addressed
      with the colon path operator. Wrapping it in TRY_PARSE_JSON - the natural
      thing to write - fails with "Invalid argument types for function
      'TRY_PARSE_JSON': (OBJECT)".
   --------------------------------------------------------------------------- */
CREATE OR REPLACE VIEW <L2_DB>.CAPTURE_CONTROL.VW_REFRESH_AUDIT AS
SELECT NAME,
       SCHEMA_NAME,
       STATE,
       REFRESH_ACTION,
       REFRESH_TRIGGER,
       REFRESH_START_TIME,
       REFRESH_END_TIME,
       DATEDIFF('millisecond', REFRESH_START_TIME, REFRESH_END_TIME) AS DURATION_MS,
       STATISTICS:numInsertedRows::NUMBER  AS ROWS_INSERTED,
       STATISTICS:numDeletedRows::NUMBER   AS ROWS_DELETED,
       STATISTICS:numCopiedRows::NUMBER    AS ROWS_COPIED,
       STATISTICS:numAddedPartitions::NUMBER AS PARTITIONS_ADDED,
       /* the condition worth alerting on: a steady-state refresh that is doing
          full work rather than incremental work, which is a silent cost
          regression with nothing in the object's behaviour to point at */
       IFF(REFRESH_ACTION = 'FULL' AND REFRESH_TRIGGER <> 'CREATION', TRUE, FALSE)
           AS UNEXPECTED_FULL_REFRESH
FROM   TABLE(<L2_DB>.INFORMATION_SCHEMA.DYNAMIC_TABLE_REFRESH_HISTORY());

/* ---------------------------------------------------------------------------
   3. Baseline. Captured once, so drift can be measured against it rather than
      against an assumption. Without a baseline a share that has always been 85%
      sparse looks identical to one that broke last night.
   --------------------------------------------------------------------------- */
CREATE TABLE IF NOT EXISTS <L2_DB>.CAPTURE_CONTROL.CAPTURE_BASELINE (
  CAPTURED_AT        TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
  OBJECT_NAME        VARCHAR,
  TOTAL_ROWS         NUMBER,
  FILE_COUNT         NUMBER,
  DISTINCT_STAMPS    NUMBER,
  LAST_WRITE         TIMESTAMP_NTZ,
  PCT_RESTAMPED_24H  NUMBER(9,6)
);

GRANT USAGE ON SCHEMA <L2_DB>.CAPTURE_CONTROL TO ROLE <OPS_ROLE>;
GRANT SELECT ON ALL VIEWS  IN SCHEMA <L2_DB>.CAPTURE_CONTROL TO ROLE <OPS_ROLE>;
GRANT SELECT ON ALL TABLES IN SCHEMA <L2_DB>.CAPTURE_CONTROL TO ROLE <OPS_ROLE>;
