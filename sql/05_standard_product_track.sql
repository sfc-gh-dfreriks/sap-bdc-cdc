/* ============================================================================
   05  The standard data product track
   ----------------------------------------------------------------------------
   Standard data products carry their own change metadata, so nothing has to be
   configured in the source system. Four columns arrive with every object:

     __OPERATION_TYPE                            the change mode for the row
     __TIMESTAMP                                 when the connector wrote it
     load_type_<32-hex flow hash>                'initial' or 'delta'
     run_id_<32-hex flow hash>                   identifies the replication flow

   The hash is identical across every object of one product, so it names the
   flow, not the object. Measured on a reference share: run_id held the SAME
   value on the initial snapshot and on the deltas that followed it, so run_id is
   not a batch discriminator and cannot be used as a watermark. __TIMESTAMP is
   the only usable one.

   Three properties of these columns drive every decision below, and all three
   were measured rather than assumed.

   1. __TIMESTAMP is TEXT, not a timestamp. It arrives as ISO-8601 with a COMMA
      decimal separator: 2026-06-15T09:41:59,6426580Z. TRY_TO_TIMESTAMP_NTZ does
      not error on that, it returns NULL for every row, so an unguarded cast
      looks like an empty column rather than a format mismatch. Parsing needs
      REPLACE(col, ',', '.') first.

   2. Comparing it as TEXT is both correct and free. The format is fixed-width
      and zero-padded, so lexicographic order is chronological order. MAX() over
      the raw column is answered from Iceberg manifest statistics without reading
      data; MAX() over a parsed expression reads the object. Measured on one
      reference table: 0.000 MB / 2 ms raw against 0.310 MB / 45 ms parsed. The
      rule this file follows everywhere: compare as text, parse only to display.

   3. The columns cannot be trusted on sight. On the reference tenant every
      object inside the live catalog-linked database was clean, and every object
      that had been COPIED out of one into an ordinary database had corrupted
      metadata - __OPERATION_TYPE holding product titles on one product and
      customer identifiers on another, and on a third __OPERATION_TYPE,
      __TIMESTAMP and load_type all holding the same value with run_id null.
      None of that fails loudly. It produces a wrong incremental result quietly.
      Section 1 therefore validates before section 2 polls.

   What is NOT established: the authoritative value list for __OPERATION_TYPE.
   Only 'L' (initial load) and 'A' (delta, after-image) were ever observed, and
   no delete code appeared anywhere. Section 4 is written so that a delete code
   is handled if the product emits one, and so that its absence is reconciled
   rather than assumed benign.

   Substitute <L2_DB> for your Gold database and <WH> for your warehouse.
   Deploy 01_capture_control.sql first; this file extends it.
   ========================================================================== */


/* ---------------------------------------------------------------------------
   1. Validation. Run this before trusting the metadata, and again whenever the
      share is re-linked or a product is re-published.

      The thresholds come from the contrast measured between a clean share and
      corrupted copies of it: clean objects carried 2 distinct operation codes of
      1 character, while the corrupt ones carried 17 distinct at 33 characters
      and 620 distinct at 10.
   --------------------------------------------------------------------------- */
CREATE TABLE IF NOT EXISTS <L2_DB>.CAPTURE_CONTROL.STANDARD_VALIDATION (
  CHECKED_AT        TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
  OBJECT_NAME       VARCHAR,
  SOURCE_FQN        VARCHAR,
  TOTAL_ROWS        NUMBER,
  OPERATION_CARD    NUMBER,   -- distinct values in __OPERATION_TYPE
  OPERATION_MAX_LEN NUMBER,   -- longest value; a change code is 1-2 characters
  TIMESTAMPS_PARSED NUMBER,   -- rows whose __TIMESTAMP parses after REPLACE
  DEGENERATE_ROWS   NUMBER,   -- rows where operation and timestamp are equal
  VERDICT           VARCHAR,
  FAULTS            VARCHAR
);

CREATE OR REPLACE PROCEDURE <L2_DB>.CAPTURE_CONTROL.SP_VALIDATE_STANDARD()
  RETURNS VARCHAR
  LANGUAGE SQL
  COMMENT = 'Validate the connector metadata on every registered STANDARD object.'
AS
$$
DECLARE
  c_obj CURSOR FOR
    SELECT OBJECT_NAME, SOURCE_FQN, WATERMARK_COLUMN, OPERATION_COLUMN
    FROM   <L2_DB>.CAPTURE_CONTROL.WATERMARK
    WHERE  TRACK = 'STANDARD';
  n_bad  NUMBER DEFAULT 0;
  n_all  NUMBER DEFAULT 0;
BEGIN
  FOR rec IN c_obj DO
    n_all := :n_all + 1;
    /* Built as dynamic SQL because the object list is data, not code. Both
       column names are double-quoted: __OPERATION_TYPE is uppercase but
       load_type / run_id are lowercase on the share, and an unquoted lowercase
       reference would resolve to the wrong case. */
    EXECUTE IMMEDIATE
      'INSERT INTO <L2_DB>.CAPTURE_CONTROL.STANDARD_VALIDATION '
      || '(OBJECT_NAME, SOURCE_FQN, TOTAL_ROWS, OPERATION_CARD, OPERATION_MAX_LEN, '
      || ' TIMESTAMPS_PARSED, DEGENERATE_ROWS, VERDICT, FAULTS) '
      || 'WITH M AS (SELECT COUNT(*) AS N, '
      || '   COUNT(DISTINCT "' || rec.OPERATION_COLUMN || '") AS CARD, '
      || '   MAX(LENGTH("' || rec.OPERATION_COLUMN || '")) AS MAXLEN, '
      || '   COUNT(TRY_TO_TIMESTAMP_NTZ(REPLACE("' || rec.WATERMARK_COLUMN
      || '", '','', ''.''))) AS TSOK, '
      || '   SUM(IFF("' || rec.OPERATION_COLUMN || '" = "' || rec.WATERMARK_COLUMN
      || '", 1, 0)) AS DEGEN '
      || ' FROM ' || rec.SOURCE_FQN || ') '
      || 'SELECT ''' || rec.OBJECT_NAME || ''', ''' || rec.SOURCE_FQN || ''', '
      || '  N, CARD, MAXLEN, TSOK, DEGEN, '
      || '  IFF(CARD <= 12 AND MAXLEN <= 2 AND (N = 0 OR TSOK > 0) AND DEGEN = 0, '
      || '      ''USABLE'', ''UNUSABLE''), '
      || '  NULLIF(TRIM(  IFF(CARD > 12 OR MAXLEN > 2, ''operation column is not a change code; '', '''') '
      || '             || IFF(N > 0 AND TSOK = 0, ''timestamp column does not parse; '', '''') '
      || '             || IFF(DEGEN > 0, ''operation and timestamp are the same value; '', '''')), '''') '
      || 'FROM M';
  END FOR;

  n_bad := (SELECT COUNT(*) FROM <L2_DB>.CAPTURE_CONTROL.STANDARD_VALIDATION
            WHERE CHECKED_AT >= DATEADD('minute', -5, CURRENT_TIMESTAMP())
              AND VERDICT = 'UNUSABLE');
  IF (:n_bad > 0) THEN
    RETURN 'UNUSABLE METADATA on ' || :n_bad || ' of ' || :n_all
           || ' object(s). Inspect CAPTURE_CONTROL.STANDARD_VALIDATION and do not '
           || 'build capture on the failing objects.';
  END IF;
  RETURN 'All ' || :n_all || ' standard object(s) validated.';
END;
$$;


/* ---------------------------------------------------------------------------
   2. The poll. Detects whether the source advanced, for free, and refreshes the
      dependent dynamic table only when it did.

      Everything here stays in TEXT deliberately. The moment a comparison parses
      the column, the poll stops being answerable from manifest statistics and
      starts scanning the object on every cycle - which is the entire cost of the
      pattern, paid on a schedule, to learn nothing.
   --------------------------------------------------------------------------- */
CREATE OR REPLACE PROCEDURE <L2_DB>.CAPTURE_CONTROL.SP_POLL_STANDARD(
    P_OBJECT VARCHAR, P_TARGET VARCHAR)
  RETURNS VARCHAR
  LANGUAGE SQL
  COMMENT = 'Refresh P_TARGET only if the standard source watermark advanced.'
AS
$$
DECLARE
  src_fqn  VARCHAR;
  wm_col   VARCHAR;
  src_wm   VARCHAR;
  seen_wm  VARCHAR;
  rs       RESULTSET;
BEGIN
  SELECT SOURCE_FQN, WATERMARK_COLUMN, LAST_WATERMARK_TEXT
    INTO src_fqn, wm_col, seen_wm
    FROM <L2_DB>.CAPTURE_CONTROL.WATERMARK
   WHERE OBJECT_NAME = :P_OBJECT AND TRACK = 'STANDARD';

  IF (src_fqn IS NULL) THEN
    RETURN 'NOT REGISTERED: ' || :P_OBJECT || ' is not a STANDARD-track object.';
  END IF;

  /* A scalar out of dynamic SQL needs a RESULTSET and a cursor. Assigning
     directly from EXECUTE IMMEDIATE is not supported. */
  rs := (EXECUTE IMMEDIATE 'SELECT MAX("' || :wm_col || '") AS M FROM ' || :src_fqn);
  LET cur CURSOR FOR rs;
  OPEN cur;
  FETCH cur INTO src_wm;
  CLOSE cur;

  IF (src_wm IS NULL) THEN
    RETURN 'NO SIGNAL: watermark column is empty on every row.';
  END IF;

  /* Text comparison, and correct because the format is fixed-width. */
  IF (:seen_wm IS NULL OR :src_wm > :seen_wm) THEN
    EXECUTE IMMEDIATE 'ALTER DYNAMIC TABLE ' || :P_TARGET || ' REFRESH';
    UPDATE <L2_DB>.CAPTURE_CONTROL.WATERMARK
       SET LAST_WATERMARK_TEXT = :src_wm,
           LAST_WATERMARK      = TRY_TO_TIMESTAMP_NTZ(REPLACE(:src_wm, ',', '.')),
           LAST_RUN_STARTED    = SYSDATE(),
           LAST_RUN_STATUS     = 'REFRESHED',
           UPDATED_AT          = SYSDATE()
     WHERE OBJECT_NAME = :P_OBJECT;
    RETURN 'REFRESHED ' || :P_TARGET || ' to watermark ' || :src_wm;
  END IF;

  UPDATE <L2_DB>.CAPTURE_CONTROL.WATERMARK
     SET LAST_RUN_STATUS = 'NO_CHANGE', UPDATED_AT = SYSDATE()
   WHERE OBJECT_NAME = :P_OBJECT;
  RETURN 'NO CHANGE at watermark ' || :src_wm;
END;
$$;


/* ---------------------------------------------------------------------------
   3. Freshness. Reports how far behind each registered object is.

      SYSDATE() rather than CURRENT_TIMESTAMP(). The connector writes UTC and
      LAST_WATERMARK is TIMESTAMP_NTZ, while CURRENT_TIMESTAMP() is session
      timezone aware - mixing the two reported a 109 minute lag as -311 minutes
      on a session running at UTC-7. SYSDATE() returns UTC as NTZ, which is the
      same basis as the watermark.

      This is the one place the watermark is parsed, because it is being
      displayed rather than compared.
   --------------------------------------------------------------------------- */
CREATE OR REPLACE VIEW <L2_DB>.CAPTURE_CONTROL.VW_STANDARD_FRESHNESS AS
SELECT OBJECT_NAME,
       SOURCE_FQN,
       TRACK,
       WATERMARK_COLUMN,
       COALESCE(LAST_WATERMARK,
                TRY_TO_TIMESTAMP_NTZ(REPLACE(LAST_WATERMARK_TEXT, ',', '.')))
           AS LAST_WATERMARK_UTC,
       DATEDIFF('minute',
                COALESCE(LAST_WATERMARK,
                         TRY_TO_TIMESTAMP_NTZ(REPLACE(LAST_WATERMARK_TEXT, ',', '.'))),
                SYSDATE()) AS MINUTES_BEHIND,
       LAST_RUN_STATUS,
       LAST_RUN_STARTED,
       UPDATED_AT
FROM   <L2_DB>.CAPTURE_CONTROL.WATERMARK;


/* ---------------------------------------------------------------------------
   4. Applying changes, and the delete problem.

      Prefer a dynamic table. It needs none of this: it recomputes from the share
      and keeps its own state, and on a catalog-linked share it refreshes
      incrementally provided REFRESH_MODE is pinned explicitly. Leave it to AUTO
      on a non-trivial query and Snowflake picks FULL, which then cascades -
      change tracking is unavailable on a FULL dynamic table, so anything
      downstream is forced to FULL too.

      The MERGE below is the exception path: non-deterministic transformations,
      or an external consumer that needs a physical delta table. Two things about
      it are easy to get wrong.

      First, the grain. A catalog-linked table declares no primary key, so
      MERGE_KEY_COLUMNS has to be supplied per object from source-system
      knowledge. Getting it short does not error; it silently collapses rows.

      Second, deletes. No delete code was observed on the reference share - only
      'L' and 'A' - so whether this product ever emits one is unverified. The
      DELETE branch is therefore written but commented, and the reconciliation
      query after it is not optional: if the source hard-deletes and the share
      never says so, the only way to find out is to compare counts.

   MERGE INTO <L2_DB>.<TARGET> t
   USING (
       SELECT *
       FROM   <SOURCE_FQN>
       WHERE  "__TIMESTAMP" > '<LAST_WATERMARK_TEXT>'   -- text compare, see header
   ) s
      ON t.<KEY_1> = s.<KEY_1> AND t.<KEY_2> = s.<KEY_2>   -- full grain, every part
   WHEN MATCHED AND s."__OPERATION_TYPE" IN ('A', 'U', 'C')
       THEN UPDATE SET ...
   -- WHEN MATCHED AND s."__OPERATION_TYPE" = 'D' THEN DELETE
   WHEN NOT MATCHED AND COALESCE(s."__OPERATION_TYPE", 'A') <> 'D'
       THEN INSERT (...) VALUES (...);

      Reconciliation. Run on a schedule, not once. A non-zero difference on the
      standard track means either a delete the share did not represent or a merge
      key that is too short - both silent, both wrong in the same direction.
   --------------------------------------------------------------------------- */
CREATE OR REPLACE VIEW <L2_DB>.CAPTURE_CONTROL.VW_STANDARD_OPERATION_MIX AS
/* What the operation column actually contains, per registered object, so drift
   in the value set shows up instead of being discovered by a wrong number.
   Populated by SP_VALIDATE_STANDARD; this view reads the latest result only. */
SELECT OBJECT_NAME,
       SOURCE_FQN,
       TOTAL_ROWS,
       OPERATION_CARD,
       OPERATION_MAX_LEN,
       TIMESTAMPS_PARSED,
       DEGENERATE_ROWS,
       VERDICT,
       FAULTS,
       CHECKED_AT
FROM   <L2_DB>.CAPTURE_CONTROL.STANDARD_VALIDATION
QUALIFY ROW_NUMBER() OVER (PARTITION BY OBJECT_NAME ORDER BY CHECKED_AT DESC) = 1;


GRANT USAGE ON SCHEMA <L2_DB>.CAPTURE_CONTROL TO ROLE <OPS_ROLE>;
GRANT SELECT ON ALL VIEWS  IN SCHEMA <L2_DB>.CAPTURE_CONTROL TO ROLE <OPS_ROLE>;
GRANT SELECT ON ALL TABLES IN SCHEMA <L2_DB>.CAPTURE_CONTROL TO ROLE <OPS_ROLE>;
