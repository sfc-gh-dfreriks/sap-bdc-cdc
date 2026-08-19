/* ============================================================================
   02  The capture engine: dynamic tables over the share
   ----------------------------------------------------------------------------
   This is the whole engine. There is no custom code, no task, no stream and no
   merge procedure on this path.

   Two configuration choices carry all the weight, and both are easy to get
   wrong by omission rather than by error.
   ========================================================================== */

/* ---------------------------------------------------------------------------
   Choice 1: state REFRESH_MODE explicitly. Never leave it as AUTO.

   AUTO lets Snowflake decide, and it will silently fall back to FULL when it
   cannot support incremental for a given query. A full refresh of a Gold layer
   over hundreds of millions of rows every hour is a completely different cost
   profile from an incremental one, and nothing in the object's behaviour
   announces the difference - no error, no warning, just a larger bill and a
   longer lag.

   Stating INCREMENTAL forces Snowflake either to honour it or to refuse at
   CREATE time. That converts a silent cost regression into a deployment-time
   error, which is what you want.

   All four shapes below have been verified to sustain incremental refresh over
   a catalog-linked share: a flat projection over a view that casts dates, a
   two-view join, a GROUP BY aggregate, and a left join to a pre-aggregated
   child. So there is no reason to fall back to AUTO.
   --------------------------------------------------------------------------- */

/* ---------------------------------------------------------------------------
   Choice 2: set TARGET_LAG per object class, not uniformly.

   A no-change refresh costs a few hundred milliseconds, so a uniform lag is not
   expensive - but it is uninformed, and it obscures intent. Transactional
   objects change continuously; master data dimensions in a typical BDC share may
   not have been written for days. Differentiating costs nothing.

     transactional, high change   15 minutes
     transactional, low change    1 hour
     master data, active          1 hour
     master data, static          24 hours, or DOWNSTREAM
   --------------------------------------------------------------------------- */

/* --------------------------------------------------- shape A: flat projection */
CREATE OR REPLACE DYNAMIC TABLE <L2_DB>.<SCHEMA>.DT_SALES_ORDER_ITEM
  TARGET_LAG   = '15 minutes'
  WAREHOUSE    = <L2_WH>
  REFRESH_MODE = INCREMENTAL
  INITIALIZE   = ON_CREATE
AS
SELECT SALESDOCUMENT,
       SALESDOCUMENTITEM,
       MATERIAL,
       PLANT,
       NETAMOUNT,
       ORDERQUANTITY,
       ORDERQUANTITYUNIT,
       CREATIONDATE,
       /* Carry the watermark through into Gold. It costs one column and makes
          every downstream row traceable to the connector batch that delivered
          it. The freshness monitor reads it, so put it on every object rather
          than only where it is needed today. */
       ZZLASTCHGDATE AS SOURCE_WRITE_TS
FROM   <L1_DB>.<L1_SCHEMA>.SALES_ORDER_ITEM;

/* --------------------------------------------------------- shape B: two-view join */
CREATE OR REPLACE DYNAMIC TABLE <L2_DB>.<SCHEMA>.DT_SO_LINE
  TARGET_LAG   = '15 minutes'
  WAREHOUSE    = <L2_WH>
  REFRESH_MODE = INCREMENTAL
AS
SELECT i.SALESDOCUMENT,
       i.SALESDOCUMENTITEM,
       i.MATERIAL,
       i.PLANT,
       i.NETAMOUNT,
       i.ORDERQUANTITY,
       i.ORDERQUANTITYUNIT,
       h.SALESORGANIZATION,
       h.SOLDTOPARTY,
       h.SALESDOCUMENTDATE,
       i.ZZLASTCHGDATE AS SOURCE_WRITE_TS
FROM      <L1_DB>.<L1_SCHEMA>.SALES_ORDER_ITEM   i
JOIN      <L1_DB>.<L1_SCHEMA>.SALES_ORDER_HEADER h
       ON h.SALESDOCUMENT = i.SALESDOCUMENT;

/* ---------------------------------------------------------- shape C: aggregate */
CREATE OR REPLACE DYNAMIC TABLE <L2_DB>.<SCHEMA>.DT_SALES_BY_MONTH
  TARGET_LAG   = '1 hour'
  WAREHOUSE    = <L2_WH>
  REFRESH_MODE = INCREMENTAL
AS
SELECT h.SALESORGANIZATION,
       i.MATERIAL,
       DATE_TRUNC('month', h.SALESDOCUMENTDATE) AS ORDER_MONTH,
       COUNT(*)                                 AS ORDER_LINES,
       SUM(i.NETAMOUNT)                         AS NET_AMOUNT,
       SUM(i.ORDERQUANTITY)                     AS ORDER_QUANTITY,
       MAX(i.ZZLASTCHGDATE)                     AS SOURCE_WRITE_TS
FROM      <L1_DB>.<L1_SCHEMA>.SALES_ORDER_ITEM   i
JOIN      <L1_DB>.<L1_SCHEMA>.SALES_ORDER_HEADER h
       ON h.SALESDOCUMENT = i.SALESDOCUMENT
GROUP  BY 1, 2, 3;

/* ------------------------------------- shape D: left join to aggregated child */
CREATE OR REPLACE DYNAMIC TABLE <L2_DB>.<SCHEMA>.DT_ORDER_FULFILMENT
  TARGET_LAG   = '15 minutes'
  WAREHOUSE    = <L2_WH>
  REFRESH_MODE = INCREMENTAL
AS
WITH delivered AS (
  SELECT REFERENCESDDOCUMENT           AS DOC,
         REFERENCESDDOCUMENTITEM       AS ITM,
         SUM(ACTUALDELIVERYQUANTITY)   AS DELIVERED_QTY,
         COUNT(*)                      AS DELIVERY_LINES
  FROM   <L1_DB>.<L1_SCHEMA>.DELIVERY_ITEM
  /* Honour the reference document category. Deliveries that reference a purchase
     order are stock transport movements, not sales fulfilment; on a typical share
     they are the majority of delivery lines. Omitting this predicate does not
     error - it silently drops or mis-attributes most shipments. */
  WHERE  REFERENCESDDOCUMENTCATEGORY = 'C'
  GROUP  BY 1, 2
)
SELECT i.SALESDOCUMENT,
       i.SALESDOCUMENTITEM,
       i.ORDERQUANTITY,
       COALESCE(d.DELIVERED_QTY, 0)   AS DELIVERED_QTY,
       COALESCE(d.DELIVERY_LINES, 0)  AS DELIVERY_LINES,
       i.ORDERQUANTITY - COALESCE(d.DELIVERED_QTY, 0) AS OPEN_QTY,
       i.ZZLASTCHGDATE                AS SOURCE_WRITE_TS
FROM      <L1_DB>.<L1_SCHEMA>.SALES_ORDER_ITEM i
LEFT JOIN delivered d
       ON d.DOC = i.SALESDOCUMENT
      AND d.ITM = i.SALESDOCUMENTITEM;

/* ---------------------------------------------------------------------------
   What NOT to put inside a dynamic table

   Any non-deterministic expression forces a full refresh on every cycle, which
   defeats the entire pattern. CURRENT_DATE(), CURRENT_TIMESTAMP() and
   RANDOM() are the usual culprits.

   Month-to-date logic is the common case. Put it in a view OVER the dynamic
   table rather than inside it: the view is always current, costs nothing, and
   leaves the dynamic table incrementally refreshable.
   --------------------------------------------------------------------------- */
CREATE OR REPLACE VIEW <L2_DB>.<SCHEMA>.VW_MONTH_TO_DATE AS
SELECT *
FROM   <L2_DB>.<SCHEMA>.DT_SO_LINE
WHERE  SALESDOCUMENTDATE >= DATE_TRUNC('month', CURRENT_DATE());   -- safe here

/* ---------------------------------------------------------------------------
   Verify what Snowflake actually chose, rather than trusting what you asked for
   --------------------------------------------------------------------------- */
SHOW DYNAMIC TABLES IN SCHEMA <L2_DB>.<SCHEMA>;
--  check refresh_mode = INCREMENTAL and refresh_mode_reason IS NULL

SELECT NAME, REFRESH_ACTION, REFRESH_TRIGGER, DURATION_MS, ROWS_INSERTED
FROM   <L2_DB>.CAPTURE_CONTROL.VW_REFRESH_AUDIT
ORDER  BY REFRESH_START_TIME DESC
LIMIT  20;
/*  expected steady state:
      REFRESH_ACTION = NO_DATA        nothing changed, a few hundred ms
      REFRESH_ACTION = INCREMENTAL    changed rows only
    a problem:
      REFRESH_ACTION = FULL           re-reading everything; investigate      */
