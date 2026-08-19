/* ============================================================================
   00  Discover the SAP BDC catalog-linked share
   ----------------------------------------------------------------------------
   Run these first, in order, against any account that has BDC data products
   linked into it. Nothing here writes. Substitute the names you find as you go.

   Two behaviours make this share awkward and are worth knowing up front:
     - schema names contain a colon and object names are mixed case, so every
       reference must be double quoted
     - INFORMATION_SCHEMA.COLUMNS returns NO ROWS for a catalog-linked database,
       so DESCRIBE TABLE is the only route to a column list
   ========================================================================== */

/* 1. Find the linked databases by kind, never by name. The kind is what BDC
      Connect sets and it is stable across tenants and naming conventions. */
SHOW DATABASES;
--  then keep the rows where "kind" = 'CATALOG-LINKED DATABASE'

/* 2. What is inside one of them. Note the colon in the schema name. */
SHOW TABLES IN DATABASE <LINKED_DB>;

/* 3. These report kind = TABLE and is_external = N, which understates what they
      are. This is the query that tells the truth, and it governs which change
      mechanisms are available. */
SHOW ICEBERG TABLES IN DATABASE <LINKED_DB>;

/* 4. Column list. One statement per object; there is no relational alternative. */
DESCRIBE TABLE <LINKED_DB>."<schema:v1>"."<object>";

/* 5. Find the replication watermark. SAP BDC pipelines built with SLT commonly
      carry a customer extension field holding the load timestamp. The name varies
      by implementation - ZZLASTCHGDATE is the most common - so look for a
      TIMESTAMP column whose name suggests load or change rather than a business
      event. A business date such as PostingDate is NOT a watermark. */
SELECT COLUMN_NAME, DATA_TYPE          -- will return nothing on a linked DB;
FROM   INFORMATION_SCHEMA.COLUMNS      -- shown here so the gap is explicit
WHERE  TABLE_NAME = '<object>';

/* 6. Confirm the watermark is usable: several distinct write days, with the bulk
      of rows on the initial load and small deltas since. That shape means the
      connector MERGES. A single write day covering every row means it either
      loaded once, or it reloads - and if it reloads, no watermark can work. */
SELECT TO_CHAR(ZZLASTCHGDATE, 'YYYY-MM-DD')        AS WRITE_DAY,
       COUNT(*)                                    AS ROWS_WRITTEN,
       COUNT(DISTINCT METADATA$FILENAME)           AS FILES_TOUCHED,
       TO_VARCHAR(MIN(ZZLASTCHGDATE), 'HH24:MI:SS') AS FIRST_WRITE,
       TO_VARCHAR(MAX(ZZLASTCHGDATE), 'HH24:MI:SS') AS LAST_WRITE
FROM   <LINKED_DB>."<schema:v1>"."<object>"
GROUP  BY 1 ORDER BY 1;

/* 7. Confirm the watermark prunes. Project a data column in BOTH queries:
      COUNT(*) alone can be answered from Iceberg manifest statistics without
      reading data, which flatters the result and hides the real cost. */
ALTER SESSION SET USE_CACHED_RESULT = FALSE;

SELECT COUNT(DISTINCT "<some_varchar_column>") AS N     -- the comparison point
FROM   <LINKED_DB>."<schema:v1>"."<object>";

SELECT COUNT("<some_varchar_column>") AS N               -- one incremental cycle
FROM   <LINKED_DB>."<schema:v1>"."<object>"
WHERE  ZZLASTCHGDATE > DATEADD('hour', -36, CURRENT_TIMESTAMP());

ALTER SESSION UNSET USE_CACHED_RESULT;

/* Read each query's own cost back. Needs a database context. */
SELECT QUERY_ID, BYTES_SCANNED, ROWS_PRODUCED, TOTAL_ELAPSED_TIME, QUERY_TEXT
FROM   TABLE(INFORMATION_SCHEMA.QUERY_HISTORY())
ORDER  BY START_TIME DESC LIMIT 10;

/*  Interpreting step 7
    watermark scan under ~2% of the full scan   near-complete file elimination.
                                                The watermark is the right predicate.
    2% to ~35%                                  partial pruning. Workable, not free.
    above ~35%                                  the column does not prune. Use
                                                dynamic tables, whose refresh does
                                                not depend on this.                */
