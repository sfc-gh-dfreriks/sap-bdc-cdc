/* ============================================================================
   01  Capture control schema
   ----------------------------------------------------------------------------
   The registry, audit, baseline and declared-key objects. Deploy these FIRST,
   before configuring any dynamic table, so that you have a baseline to compare
   against and can detect the one event that invalidates the whole pattern.

   Substitute <L2_DB> for your Gold database and <CLD_DB> for the
   catalog-linked database.
   ========================================================================== */

CREATE SCHEMA IF NOT EXISTS <L2_DB>.CAPTURE_CONTROL
  COMMENT = 'Change data capture state, audit and health for the SAP BDC share.';

/* ---------------------------------------------------------------------------
   1. Watermark state. Only needed for the exception path (see 04). Dynamic
      tables keep their own state and need nothing here.

      MERGE_KEY_COLUMNS is the grain the MERGE joins on, as a comma-separated
      list of exact-case column names. Quote each part when building SQL from
      it: declared keys on standard products are camel case (SalesOrganization).

      Where it comes from is recorded in KEY_SOURCE:

        DECLARED  read from the share by SP_LOAD_DECLARED_KEYS (section 4). Since
                  2026-10-30 SAP publishes a primary key on standard data products
                  - SHOW PRIMARY KEYS IN DATABASE returns one row per key part,
                  with rely = true. This is the default. Do not hand-enter a key
                  for an object that declares one.
        MANUAL    entered by hand, for objects that declare no key - expected on
                  custom data products. Never overwritten by the loader. The grain
                  has to come from source-system knowledge, and a short key does
                  not error: purchase order history needs all seven parts of the
                  EKBE key, and on five it appears to hold duplicates that
                  de-duplication then silently discards.

      Either way the key is VERIFIED, not trusted. RELY means SAP asserts the key;
      Snowflake does not enforce it. SP_VERIFY_MERGE_KEYS records COUNT(*) against
      COUNT(DISTINCT key) in KEY_STATUS, and nothing should merge on a key whose
      status is not UNIQUE.

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
  MERGE_KEY_COLUMNS    VARCHAR,          -- NULL until SP_LOAD_DECLARED_KEYS fills it
  KEY_SOURCE           VARCHAR
      CONSTRAINT CK_WATERMARK_KEY_SOURCE CHECK (KEY_SOURCE IN ('DECLARED', 'MANUAL')),
  KEY_STATUS           VARCHAR,          -- UNIQUE | NOT UNIQUE, from SP_VERIFY_MERGE_KEYS
  KEY_VERIFIED_AT      TIMESTAMP_NTZ,
  OPERATION_COLUMN     VARCHAR,          -- STANDARD only; NULL on the custom track
  LAST_WATERMARK       TIMESTAMP_NTZ,    -- CUSTOM track
  LAST_WATERMARK_TEXT  VARCHAR,          -- STANDARD track, compared as text
  LAST_RUN_STARTED     TIMESTAMP_NTZ,
  LAST_RUN_ROWS        NUMBER,
  LAST_RUN_STATUS      VARCHAR,
  UPDATED_AT           TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()
);

/* Upgrade path for a registry created before keys were declared on the share. */
ALTER TABLE <L2_DB>.CAPTURE_CONTROL.WATERMARK ALTER COLUMN MERGE_KEY_COLUMNS DROP NOT NULL;
ALTER TABLE <L2_DB>.CAPTURE_CONTROL.WATERMARK ADD COLUMN IF NOT EXISTS KEY_SOURCE VARCHAR;
ALTER TABLE <L2_DB>.CAPTURE_CONTROL.WATERMARK ADD COLUMN IF NOT EXISTS KEY_STATUS VARCHAR;
ALTER TABLE <L2_DB>.CAPTURE_CONTROL.WATERMARK ADD COLUMN IF NOT EXISTS KEY_VERIFIED_AT TIMESTAMP_NTZ;

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

/* ---------------------------------------------------------------------------
   4. Declared keys. The share publishes each standard object's grain; read it
      rather than typing it.

      SHOW PRIMARY KEYS returns its rows in no particular order, so the key is
      reassembled by KEY_SEQUENCE. The match to the registry is on the
      unquoted path (database.schema.table), because SOURCE_FQN is written with
      whatever quoting the object needs and catalog-linked names are case
      sensitive. Rows with KEY_SOURCE = 'MANUAL' are left alone.

      Run after registering objects, and again whenever a product is
      re-published:

        CALL <L2_DB>.CAPTURE_CONTROL.SP_LOAD_DECLARED_KEYS('<CLD_DB>');
        CALL <L2_DB>.CAPTURE_CONTROL.SP_VERIFY_MERGE_KEYS();
   --------------------------------------------------------------------------- */
CREATE TABLE IF NOT EXISTS <L2_DB>.CAPTURE_CONTROL.DECLARED_KEY (
  LOADED_AT        TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
  DATABASE_NAME    VARCHAR NOT NULL,
  SCHEMA_NAME      VARCHAR NOT NULL,
  TABLE_NAME       VARCHAR NOT NULL,
  COLUMN_NAME      VARCHAR NOT NULL,
  KEY_SEQUENCE     NUMBER  NOT NULL,
  CONSTRAINT_NAME  VARCHAR,           -- SYS_CONSTRAINT_<uuid>, set by SAP
  RELY             BOOLEAN,           -- asserted, not enforced
  CREATED_ON       TIMESTAMP_LTZ
);

CREATE OR REPLACE PROCEDURE <L2_DB>.CAPTURE_CONTROL.SP_LOAD_DECLARED_KEYS(P_DATABASE VARCHAR)
  RETURNS VARCHAR
  LANGUAGE SQL
  COMMENT = 'Load SHOW PRIMARY KEYS for one catalog-linked database and fill MERGE_KEY_COLUMNS.'
  EXECUTE AS CALLER
AS
$$
DECLARE
  n_parts   NUMBER DEFAULT 0;
  n_filled  NUMBER DEFAULT 0;
  n_without NUMBER DEFAULT 0;
BEGIN
  EXECUTE IMMEDIATE 'SHOW PRIMARY KEYS IN DATABASE "' || REPLACE(:P_DATABASE, '"', '""') || '"';
  LET qid VARCHAR := SQLID;

  DELETE FROM <L2_DB>.CAPTURE_CONTROL.DECLARED_KEY WHERE DATABASE_NAME = :P_DATABASE;
  INSERT INTO <L2_DB>.CAPTURE_CONTROL.DECLARED_KEY
    (DATABASE_NAME, SCHEMA_NAME, TABLE_NAME, COLUMN_NAME, KEY_SEQUENCE,
     CONSTRAINT_NAME, RELY, CREATED_ON)
  SELECT "database_name", "schema_name", "table_name", "column_name",
         "key_sequence", "constraint_name", TRY_TO_BOOLEAN("rely"::VARCHAR), "created_on"
  FROM   TABLE(RESULT_SCAN(:qid));
  n_parts := SQLROWCOUNT;

  UPDATE <L2_DB>.CAPTURE_CONTROL.WATERMARK w
     SET MERGE_KEY_COLUMNS = k.KEY_COLUMNS,
         KEY_SOURCE        = 'DECLARED',
         KEY_STATUS        = NULL,      -- a new key is unverified until checked
         KEY_VERIFIED_AT   = NULL,
         UPDATED_AT        = SYSDATE()
    FROM (SELECT DATABASE_NAME || '.' || SCHEMA_NAME || '.' || TABLE_NAME AS PLAIN_FQN,
                 LISTAGG(COLUMN_NAME, ',') WITHIN GROUP (ORDER BY KEY_SEQUENCE) AS KEY_COLUMNS
            FROM <L2_DB>.CAPTURE_CONTROL.DECLARED_KEY
           WHERE DATABASE_NAME = :P_DATABASE
           GROUP BY 1) k
   WHERE REPLACE(w.SOURCE_FQN, '"', '') = k.PLAIN_FQN
     AND COALESCE(w.KEY_SOURCE, '') <> 'MANUAL'
     AND w.MERGE_KEY_COLUMNS IS DISTINCT FROM k.KEY_COLUMNS;
  n_filled := SQLROWCOUNT;

  n_without := (SELECT COUNT(*) FROM <L2_DB>.CAPTURE_CONTROL.WATERMARK
                 WHERE SPLIT_PART(REPLACE(SOURCE_FQN, '"', ''), '.', 1) = :P_DATABASE
                   AND MERGE_KEY_COLUMNS IS NULL);

  RETURN :n_parts || ' key part(s) read from ' || :P_DATABASE || '; '
         || :n_filled || ' registered object(s) updated; '
         || :n_without || ' registered object(s) still without a key - enter those '
         || 'by hand with KEY_SOURCE = ''MANUAL''.';
END;
$$;

CREATE OR REPLACE PROCEDURE <L2_DB>.CAPTURE_CONTROL.SP_VERIFY_MERGE_KEYS()
  RETURNS VARCHAR
  LANGUAGE SQL
  COMMENT = 'Test every registered merge key with COUNT(*) against COUNT(DISTINCT key).'
  EXECUTE AS CALLER
AS
$$
DECLARE
  c_obj CURSOR FOR
    SELECT OBJECT_NAME, SOURCE_FQN, MERGE_KEY_COLUMNS
    FROM   <L2_DB>.CAPTURE_CONTROL.WATERMARK
    WHERE  MERGE_KEY_COLUMNS IS NOT NULL;
  n_all  NUMBER DEFAULT 0;
  n_bad  NUMBER DEFAULT 0;
  n_rows NUMBER;
  n_dist NUMBER;
  n_null NUMBER;
  cols   VARCHAR;
  nulls  VARCHAR;
  status VARCHAR;
BEGIN
  FOR rec IN c_obj DO
    n_all := :n_all + 1;
    /* Each part is quoted so camel-case declared names resolve. Rows with a NULL
       key part are counted separately: COUNT(DISTINCT a, b) skips them, which
       would let a NULL-keyed duplicate pass. */
    cols  := '"' || REPLACE(rec.MERGE_KEY_COLUMNS, ',', '","') || '"';
    nulls := '"' || REPLACE(rec.MERGE_KEY_COLUMNS, ',', '" IS NULL OR "') || '" IS NULL';
    EXECUTE IMMEDIATE 'SELECT COUNT(*) AS N, COUNT(DISTINCT ' || :cols || ') AS D, '
                      || 'COUNT_IF(' || :nulls || ') AS NK FROM ' || rec.SOURCE_FQN;
    SELECT N, D, NK INTO :n_rows, :n_dist, :n_null
      FROM TABLE(RESULT_SCAN(LAST_QUERY_ID()));
    status := IFF(:n_dist = :n_rows AND :n_null = 0, 'UNIQUE', 'NOT UNIQUE');
    IF (:status <> 'UNIQUE') THEN
      n_bad := :n_bad + 1;
    END IF;
    UPDATE <L2_DB>.CAPTURE_CONTROL.WATERMARK
       SET KEY_STATUS = :status, KEY_VERIFIED_AT = SYSDATE(), UPDATED_AT = SYSDATE()
     WHERE OBJECT_NAME = rec.OBJECT_NAME;
  END FOR;

  IF (:n_bad > 0) THEN
    RETURN 'NOT UNIQUE on ' || :n_bad || ' of ' || :n_all || ' key(s). Do not merge on '
           || 'those objects: correct the key and set KEY_SOURCE = ''MANUAL''.';
  END IF;
  RETURN 'All ' || :n_all || ' registered key(s) are unique.';
END;
$$;

GRANT USAGE ON SCHEMA <L2_DB>.CAPTURE_CONTROL TO ROLE <OPS_ROLE>;
GRANT SELECT ON ALL VIEWS  IN SCHEMA <L2_DB>.CAPTURE_CONTROL TO ROLE <OPS_ROLE>;
GRANT SELECT ON ALL TABLES IN SCHEMA <L2_DB>.CAPTURE_CONTROL TO ROLE <OPS_ROLE>;
