#!/usr/bin/env python3
"""
Generate SAP BDC CDC collateral artefacts.

    python3 generate_collateral.py [white-paper|exec-summary|deck|all]

Requires reportlab and python-pptx.  No Snowflake connection required.
All content is embedded in this script; re-running overwrites the output files.

Output:
    collateral/SAP_BDC_CDC_White_Paper.pdf
    collateral/SAP_BDC_CDC_Executive_Summary.pdf
    collateral/SAP_BDC_CDC_Solution_Overview.pptx
"""
from __future__ import annotations

import sys
from pathlib import Path

OUT_DIR    = Path(__file__).resolve().parent.parent   # collateral/
ASSETS_DIR = OUT_DIR.parent / "assets" / "diagrams"   # the repo's own diagrams
BUILD_DATE = "August 2026"

FILENAMES = {
    "white-paper":  "SAP_BDC_CDC_White_Paper.pdf",
    "exec-summary": "SAP_BDC_CDC_Executive_Summary.pdf",
    "deck":         "SAP_BDC_CDC_Solution_Overview.pptx",
    "recommendation": "SAP_BDC_CDC_Enablement_Recommendation.pdf",
    "exec-deck":      "SAP_BDC_CDC_Executive_Overview.pptx",
}

# ── palette (blue / slate / grey - no Mosaic green) ───────────────────────
NAVY    = "#1F3A6B"
BLUE    = "#2C5282"
SLATE   = "#4A5568"
MID     = "#718096"
RULE    = "#CBD5E0"
LIGHT   = "#EBF4FF"
ALT     = "#F7F9FC"
CODE_BG = "#F1F5F9"
TEXT    = "#1A202C"
WHITE   = "#FFFFFF"
WARNBG  = "#FFFBEB"
WARNFG  = "#744210"


# ═══════════════════════════════════════════════════════════════════════════
# Content - White Paper
# ═══════════════════════════════════════════════════════════════════════════

WP_TITLE    = "Change Data Capture from SAP BDC Catalog-Linked Shares"
WP_SUBTITLE = "A dual-track technical reference"

# Each section is a dict with "heading", "level" (1 or 2), and "body".
# Body items: ("para", text) | ("h2", text) | ("table", dict) |
#             ("code", text) | ("note", text) | ("warn", text)

WP_SECTIONS = [
    {
        "heading": "1.  Overview",
        "level": 1,
        "body": [
            ("para", (
                "SAP Business Data Cloud (BDC) makes data products available in Snowflake "
                "as catalog-linked databases - Iceberg tables whose metadata is managed by "
                "SAP, not by Snowflake. Snowflake can read them, but the three standard "
                "change-data-capture mechanisms are all refused. Two alternative signals "
                "exist; which one applies depends on the kind of data product. Both tracks "
                "share the same engine: dynamic tables with REFRESH_MODE = INCREMENTAL."
            )),
            ("para", (
                "This document records the mechanism, the measured evidence, and a risk "
                "that affects both tracks: copying the share breaks its metadata silently "
                "and without any error. The recommended first step on any new tenant is "
                "the readiness probe in probes/cdc_readiness.py. It classifies every "
                "object by track, validates the standard metadata, measures poll and "
                "extraction cost, and returns a verdict in under a minute."
            )),
        ],
    },
    {
        "heading": "2.  Why standard CDC is unavailable",
        "level": 1,
        "body": [
            ("para", (
                "A catalog-linked database reports kind = TABLE and is_external = N, "
                "which suggests ordinary tables and ordinary CDC options. The objects "
                "are Iceberg tables on an external SAP-managed catalog, and Snowflake's "
                "own error messages are the clearest evidence of the real situation:"
            )),
            ("table", {
                "headers": ["Attempted operation", "Error returned"],
                "rows": [
                    ["CREATE STREAM ... ON TABLE <share>",
                     "Streams on External Tables or Iceberg tables with an external "
                     "catalog must have INSERT_ONLY set to true."],
                    ["SELECT ... CHANGES(INFORMATION => DEFAULT)",
                     "CHANGES clause is not supported on external tables or Iceberg "
                     "tables with an external catalog."],
                    ["ALTER TABLE ... SET CHANGE_TRACKING = TRUE",
                     "change_tracking cannot be set for Iceberg tables unless they "
                     "use Snowflake catalog."],
                ],
                "col_widths": [0.38, 0.62],
            }),
            ("para", (
                "SHOW TABLES reports change_tracking = ON on catalog-linked objects. "
                "Streams and CHANGES() are refused regardless. That ON is misleading "
                "and should not be taken as evidence that the mechanisms will work."
            )),
            ("para", (
                "INSERT_ONLY streams are permitted and do not error on creation. They see "
                "inserts and updates but cannot detect physical deletes, and the 14-day "
                "staleness window applies. There is no situation in which the INSERT_ONLY "
                "path is preferable to a dynamic table."
            )),
        ],
    },
    {
        "heading": "3.  Two tracks, one engine",
        "level": 1,
        "body": [
            ("para", (
                "Which change signal is available depends on the kind of data product. "
                "Custom data products expose a source-system timestamp configured by the "
                "pipeline owner. Standard data products expose four connector metadata "
                "columns generated by SAP with no source-system configuration required. "
                "The capture engine is the same on both tracks: dynamic tables with "
                "REFRESH_MODE pinned to INCREMENTAL."
            )),
            ("table", {
                "headers": ["", "Track 1 - Custom products", "Track 2 - Standard products"],
                "rows": [
                    ["Change signal",
                     "Source-system timestamp (e.g. ZZLASTCHGDATE), added by SLT "
                     "pipeline configuration",
                     "__OPERATION_TYPE and __TIMESTAMP, generated by the connector"],
                    ["Requires source configuration",
                     "Yes",
                     "No"],
                    ["Operation column",
                     "None - deletes are invisible on this track",
                     "Present; values observed: L=initial, A=delta. Delete code: "
                     "unverified (see section 5)"],
                    ["Watermark comparison",
                     "Parse as TIMESTAMP_NTZ",
                     "Compare raw text - lexicographic equals chronological. "
                     "Parsing a column expression turns a free poll into a scan."],
                    ["Delete detection path",
                     "Dynamic table snapshot diff only",
                     "Dynamic table snapshot diff; operation column available "
                     "but delete code unverified"],
                ],
                "col_widths": [0.22, 0.39, 0.39],
            }),
        ],
    },
    {
        "heading": "4.  Track 1 - Custom data products",
        "level": 1,
        "body": [
            ("h2", "The watermark column"),
            ("para", (
                "The replication timestamp is a customer extension field added to the "
                "SAP source during SLT pipeline configuration. On the reference tenant "
                "it was named ZZLASTCHGDATE, typed TIMESTAMP_NTZ(6), and present on "
                "all 23 objects in the share. Across the full reference account: 59 "
                "objects carry it. The name varies by pipeline implementation; do not "
                "assume a name. The readiness probe discovers it."
            )),
            ("para", (
                "Finding it requires reading the column list with DESCRIBE TABLE and "
                "looking for a timestamp column whose name suggests a replication event "
                "rather than a business event. A business date such as PostingDate or "
                "DocumentDate is not a replication watermark. The key distinction: does "
                "the value change when the connector writes the row, or when the "
                "business event occurred?"
            )),
            ("h2", "Why the watermark prunes"),
            ("para", (
                "The BDC connector merges rather than reloads. Historical rows keep "
                "their original replication timestamp; new data lands in new Parquet "
                "files. Because the connector writes timestamps in monotonic, "
                "non-overlapping batches, per-file Iceberg statistics span a narrow "
                "range. Snowflake's query planner reads those statistics and eliminates "
                "every file whose range cannot contain a match."
            )),
            ("para", (
                "This is a measured property of the connector, not a documented "
                "guarantee. Verify it on your tenant using the readiness probe "
                "before deploying. The merge-versus-reload check in the probe is "
                "the most critical: if the connector reloads, the watermark path "
                "reads every row every cycle and dynamic tables rebuild fully on "
                "every refresh."
            )),
            ("h2", "Measured scan cost (reference tenant, USE_CACHED_RESULT = FALSE)"),
            ("table", {
                "headers": ["Query", "Data scanned", "Duration"],
                "rows": [
                    ["Full column read, 108,615,680-row GL line-item object",
                     "7,035.80 MB", "-"],
                    ["Watermark predicate, 30 matching rows",
                     "0.00 MB", "28 ms"],
                    ["Business-date predicate, 549 matching rows",
                     "4,695.75 MB", "542 ms"],
                    ["Full GROUP BY aggregate, 108,615,638 rows",
                     "6.87 GB", "15,078 ms"],
                    ["Same aggregate scoped by watermark",
                     "0 GB", "630 ms"],
                    ["Polling the current watermark value",
                     "0.00 MB", "47-79 ms"],
                ],
                "col_widths": [0.56, 0.22, 0.22],
            }),
            ("para", (
                "The business-date predicate scanned 4.7 GB for the same answer as "
                "the watermark predicate because business dates are scattered across "
                "every file - per-file statistics span the full historical range. The "
                "difference is not a performance optimisation: it is the difference "
                "between reading only changed data and scanning the full object."
            )),
            ("h2", "Dynamic table results (reference tenant)"),
            ("para", (
                "10 of 10 Gold-layer dynamic tables were confirmed INCREMENTAL once "
                "REFRESH_MODE was pinned explicitly. Initial refresh inserted 4,314,292 "
                "rows in 18.3 s. The subsequent no-change refresh returned NO_DATA in "
                "984 ms - Snowflake established that nothing had changed without "
                "reading the source data."
            )),
            ("h2", "Delete semantics on Track 1"),
            ("para", (
                "There is no operation column on the custom track. A row that disappears "
                "from the source leaves no trace in the data; the watermark window will "
                "never encounter it. The only detection is a count comparison between "
                "source and target. A persistent positive divergence means rows were "
                "deleted upstream. Reconcile row counts on a schedule; rebuilding the "
                "target is the recommended response to a confirmed divergence."
            )),
        ],
    },
    {
        "heading": "5.  Track 2 - Standard data products",
        "level": 1,
        "body": [
            ("h2", "The connector metadata columns"),
            ("para", (
                "SAP generates four metadata columns on standard data products. Nothing "
                "needs to be configured in the source system."
            )),
            ("table", {
                "headers": ["Column name", "Description", "Type"],
                "rows": [
                    ["__OPERATION_TYPE",
                     "Operation code. Values observed: L=initial load, A=delta append. "
                     "Delete code: not observed and not confirmed by SAP documentation "
                     "(unverified - see note below).",
                     "TEXT"],
                    ["__TIMESTAMP",
                     "Replication timestamp. Format: ISO-8601 with a COMMA decimal "
                     "separator (2026-06-15T09:41:59,6426580Z). Arrives as TEXT.",
                     "TEXT"],
                    ["load_type_<32-hex-hash>",
                     "Snapshot or delta marker. Hash identifies the replication flow, "
                     "not the object. Identical across all 7 objects of the reference "
                     "product.",
                     "TEXT"],
                    ["run_id_<32-hex-hash>",
                     "Flow identifier. Held the SAME value on both the initial snapshot "
                     "and the delta rows. Not a batch discriminator; cannot serve as a "
                     "watermark.",
                     "TEXT"],
                ],
                "col_widths": [0.25, 0.57, 0.18],
            }),
            ("note", (
                "* The authoritative enumeration of __OPERATION_TYPE could not be "
                "confirmed: the SAP Help page is JavaScript-rendered. The readiness "
                "probe discovers the value distribution per tenant rather than "
                "assuming one. Do not assume a delete code exists until one is "
                "observed on your tenant."
            )),
            ("h2", "The __TIMESTAMP format trap"),
            ("para", (
                "On the reference account __TIMESTAMP arrives as: "
                "2026-06-15T09:41:59,6426580Z (a comma as the decimal separator, not "
                "a period). TRY_TO_TIMESTAMP_NTZ does not error on this; it returns "
                "NULL for every row. An unguarded cast looks like an empty column "
                "rather than a format mismatch. Parse with: "
                "TRY_TO_TIMESTAMP_NTZ(REPLACE(__TIMESTAMP, ',', '.'))."
            )),
            ("h2", "Compare as text; parse only for display"),
            ("para", (
                "Because the format is fixed-width and zero-padded, lexicographic "
                "order equals chronological order. This was verified directly on the "
                "reference data: parse(MAX(raw)) == MAX(parse(raw))."
            )),
            ("table", {
                "headers": ["Operation", "Data scanned", "Latency", "How"],
                "rows": [
                    ["MAX(__TIMESTAMP) as raw TEXT",
                     "0.000 MB", "2 ms",
                     "Answered from Iceberg manifest statistics"],
                    ["MAX(TRY_TO_TIMESTAMP_NTZ(REPLACE(__TIMESTAMP, ',', '.')))",
                     "0.310 MB", "45 ms",
                     "Must scan micropartitions"],
                ],
                "col_widths": [0.42, 0.15, 0.10, 0.33],
            }),
            ("para", (
                "Using a parsed expression for polling turns a free manifest-statistics "
                "lookup into a micropartition scan on every cycle. Compare as text; "
                "parse in application code for display only."
            )),
            ("h2", "Operation distribution (reference product, 5 non-empty objects)"),
            ("table", {
                "headers": ["Code", "Row count", "Meaning"],
                "rows": [
                    ["L", "67,087", "Initial load snapshot"],
                    ["A", "393", "Delta (append / update)"],
                    ["(delete code)", "0 observed",
                     "Unverified - no delete code seen on reference product"],
                ],
                "col_widths": [0.15, 0.20, 0.65],
            }),
            ("h2", "End-to-end standard-track engine (reference account)"),
            ("para", (
                "A dynamic table built over the live share at REFRESH_MODE = INCREMENTAL "
                "covered 3,355 rows. Poll 1 returned: REFRESHED to watermark "
                "2026-08-19T04:32:03,9099180Z. Poll 2 returned: NO CHANGE. Snowflake "
                "refresh history: INCREMENTAL in 5,241 ms, then NO_DATA in 610 ms."
            )),
            ("h2", "Extraction cost on Track 2"),
            ("para", (
                "Predicate pruning was NOT demonstrated at reference scale. The reference "
                "objects were a single micropartition (39,462 rows, 320 KB) and an "
                "incremental cycle scanned approximately 100% of a full read. Conclusion: "
                "drive refreshes from the free poll, but let dynamic tables do the "
                "recomputation rather than hand-written incremental MERGEs. Do not claim "
                "predicate pruning on the standard track."
            )),
        ],
    },
    {
        "heading": "6.  The headline risk: never trust a local copy",
        "level": 1,
        "body": [
            ("warn", (
                "The connector metadata columns were reliable only inside the live "
                "catalog-linked database. Every local copy of a standard product on "
                "the reference account had corrupted metadata. None of this fails "
                "loudly - it produces a wrong incremental result quietly and "
                "propagates downstream."
            )),
            ("para", (
                "Three separate local copies were examined. All three were corrupted:"
            )),
            ("table", {
                "headers": ["Copy", "Defect observed", "Affected rows"],
                "rows": [
                    ["Copy 1",
                     "__OPERATION_TYPE held media titles ('Mission Possible 1', "
                     "'About a Girl - Series') and 16,317 NULLs. "
                     "17 distinct values, up to 33 characters.",
                     "-"],
                    ["Copy 2",
                     "__OPERATION_TYPE, __TIMESTAMP, and LOAD_TYPE all held "
                     "customer identifiers (620 distinct values) on every row. "
                     "RUN_ID was NULL. Confirmed with positional SELECT * - "
                     "it is the data, not a query artefact.",
                     "735,305 of 735,305"],
                    ["Copy 3",
                     "Same degeneracy as Copy 2.",
                     "557,693 of 558,011"],
                ],
                "col_widths": [0.10, 0.68, 0.22],
            }),
            ("para", (
                "The defect propagated downstream. Medallion layers built on those copies "
                "inherited the corrupted column values. A downstream consumer filtering on "
                "__OPERATION_TYPE = 'A' against a corrupted copy would return zero rows "
                "or rows matched on random customer identifiers, with no error raised."
            )),
            ("para", (
                "Local copies also lose the 32-hex hash suffix on the metadata column "
                "names (load_type_<hash>, run_id_<hash>). The hash was identical across "
                "all 7 objects of the reference product. Its presence is a useful marker "
                "of a genuine live share; its absence is a signal that something is wrong."
            )),
            ("h2", "Design rules that follow"),
            ("table", {
                "headers": ["Rule", "Rationale"],
                "rows": [
                    ["Point capture at the catalog-linked database, never at a copy.",
                     "Metadata is reliable only in the live share."],
                    ["Validate the metadata before building on it.",
                     "Column name alone is not sufficient evidence. "
                     "Check value distribution."],
                    ["Treat the hash suffix as a liveness marker.",
                     "Copies lose it. Its presence confirms a live share."],
                    ["Reconcile row counts on a schedule, regardless of track.",
                     "Deletes are unrepresented on Track 1 and unverified on Track 2."],
                ],
                "col_widths": [0.50, 0.50],
            }),
        ],
    },
    {
        "heading": "7.  The engine: dynamic tables",
        "level": 1,
        "body": [
            ("para", (
                "Dynamic tables with REFRESH_MODE = INCREMENTAL are the recommended "
                "engine on both tracks. They require no procedural code and are the "
                "only mechanism that captures physical deletes, by diffing consecutive "
                "source snapshots. The watermark path is the exception, not the default; "
                "use it only where a dynamic table cannot serve."
            )),
            ("h2", "REFRESH_MODE must be pinned explicitly"),
            ("para", (
                "Never leave REFRESH_MODE as AUTO. AUTO allows Snowflake to fall back "
                "silently to FULL when it cannot support incremental for a given query. "
                "A FULL refresh over hundreds of millions of rows carries a completely "
                "different cost profile, and nothing in the object's behaviour announces "
                "the difference - no error, no warning."
            )),
            ("para", (
                "FULL also cascades: change tracking is unsupported on a FULL dynamic "
                "table, which forces everything downstream to FULL as well. Stating "
                "REFRESH_MODE = INCREMENTAL converts a silent cost regression into a "
                "deployment-time error, which is what you want."
            )),
            ("h2", "Four verified query shapes (reference tenant)"),
            ("table", {
                "headers": ["Shape", "Description", "NO_DATA duration"],
                "rows": [
                    ["A", "Flat projection over a view that casts dates", "348 ms"],
                    ["B", "Join of two views", "486 ms"],
                    ["C", "GROUP BY aggregate", "450 ms"],
                    ["D", "Left join to a pre-aggregated child CTE", "570 ms"],
                ],
                "col_widths": [0.10, 0.60, 0.30],
            }),
            ("para", (
                "All four shapes were accepted as INCREMENTAL with refresh_mode_reason "
                "= NULL. Initial refreshes were INCREMENTAL with numInsertedRows equal "
                "to the full row count of the source."
            )),
            ("h2", "What blocks incremental refresh"),
            ("para", (
                "Non-deterministic expressions inside a dynamic table's defining query "
                "force a full refresh on every cycle: CURRENT_DATE(), "
                "CURRENT_TIMESTAMP(), RANDOM(). Month-to-date logic is the most "
                "frequent source of this in practice. Put time-relative logic in a "
                "view over the dynamic table; the view is always current and adds no "
                "refresh cost."
            )),
            ("h2", "Freshness measurement"),
            ("para", (
                "Use SYSDATE() (UTC, TIMESTAMP_NTZ) not CURRENT_TIMESTAMP() "
                "(session-timezone aware) when measuring connector freshness. On the "
                "reference tenant, mixing them reported a real 109-minute lag as "
                "-311 minutes."
            )),
            ("h2", "Mechanism comparison"),
            ("table", {
                "headers": ["", "Dynamic table", "Watermark + MERGE", "INSERT_ONLY stream"],
                "rows": [
                    ["Inserts", "Yes", "Yes", "Yes"],
                    ["Updates", "Yes", "Yes", "Yes"],
                    ["Physical deletes", "Yes - diffs snapshots",
                     "No - no operation column", "No - by definition"],
                    ["Cheap no-change detection", "YES - NO_DATA in <1 s",
                     "Yes - 0 MB scanned", "Yes"],
                    ["Code required", "None",
                     "Task + control table + MERGE", "Task + MERGE"],
                    ["Can feed targets outside Snowflake", "No", "Yes", "Yes"],
                    ["Goes stale if unread", "No", "No", "Yes - 14 days"],
                    ["Blocked by non-deterministic SQL", "Yes", "No", "No"],
                ],
                "col_widths": [0.33, 0.22, 0.27, 0.18],
            }),
        ],
    },
    {
        "heading": "8.  Operating concerns",
        "level": 1,
        "body": [
            ("h2", "Uninitialized objects"),
            ("para", (
                "A share can advertise a table that was never materialised. One object "
                "on the reference account could not be DESCRIBEd: \"table "
                "'refresh_table' is not initialized\". Tooling must tolerate per-object "
                "failure rather than abort the entire run. Ask the pipeline owner "
                "whether uninitialised objects are expected."
            )),
            ("h2", "Column discovery"),
            ("para", (
                "INFORMATION_SCHEMA.COLUMNS returns no rows for a catalog-linked "
                "database. DESCRIBE TABLE is the only route to a column list, one "
                "statement per object. Schema names contain a colon "
                "(e.g. \"dp_finance_v2_srv:v1\") and object names are mixed case; "
                "every reference in SQL must be double-quoted."
            )),
            ("h2", "Monitoring"),
            ("para", (
                "Deploy sql/03_monitoring.sql. The incremental pattern depends on "
                "the connector merging. If the connector ever switches to reload, "
                "dynamic tables stay correct while becoming as expensive as a full "
                "rebuild on every refresh. That transition raises no error. "
                "VW_RELOAD_DETECTION is what detects it."
            )),
        ],
    },
    {
        "heading": "9.  Decision guide",
        "level": 1,
        "body": [
            ("table", {
                "headers": ["Situation", "Recommended path"],
                "rows": [
                    ["Standard data product available; metadata validated",
                     "Dynamic table, REFRESH_MODE = INCREMENTAL, against the live "
                     "catalog-linked database. Poll __TIMESTAMP as raw text."],
                    ["Custom product; watermark column present; connector merges",
                     "Dynamic table, REFRESH_MODE = INCREMENTAL. Watermark path "
                     "as exception for non-deterministic transforms or external targets."],
                    ["No watermark column; connector merges",
                     "Dynamic table only. No watermark-based path is available."],
                    ["Connector reloads (merge_or_reload check fails)",
                     "Dynamic tables and watermark both scan fully every cycle. "
                     "Correct but expensive. Raise with SAP BDC pipeline owner."],
                    ["Object shows NOT ASSESSABLE on probe",
                     "Resolve the access or initialisation issue before deploying."],
                ],
                "col_widths": [0.42, 0.58],
            }),
        ],
    },
    {
        "heading": "10.  Open asks to SAP",
        "level": 1,
        "body": [
            ("para", (
                "The following gaps would reduce deployment risk and improve the "
                "reliability of the pattern:"
            )),
            ("table", {
                "headers": ["Ask", "Reason"],
                "rows": [
                    ["(a) Authoritative enumeration and contract for "
                     "__OPERATION_TYPE, including whether a delete code is ever emitted.",
                     "No delete code was observed on the reference product. One may "
                     "exist but had not been emitted. Without confirmation, delete "
                     "handling cannot be implemented with confidence."],
                    ["(b) Why __TIMESTAMP ships as TEXT with a comma decimal "
                     "separator rather than a real timestamp type.",
                     "TRY_TO_TIMESTAMP_NTZ silently returns NULL on the raw value. "
                     "A standard timestamp type would remove a hidden failure mode."],
                    ["(c) Whether change_tracking = ON in SHOW TABLES is meaningful "
                     "when streams and CHANGES() are refused.",
                     "The flag is misleading and causes diagnostic errors on "
                     "first encounter."],
                    ["(d) Declared primary keys per catalog-linked object.",
                     "No primary key is declared. Wrong merge grain silently discards "
                     "real records without any constraint violation."],
                    ["(e) Guidance on uninitialised objects.",
                     "A share can advertise a table that has never been materialised. "
                     "Tooling must handle this per-object failure gracefully."],
                ],
                "col_widths": [0.48, 0.52],
            }),
        ],
    },
    {
        "heading": "11.  The readiness probe",
        "level": 1,
        "body": [
            ("para", (
                "The repo ships probes/cdc_readiness.py, which should be the first "
                "step on any new tenant. It classifies every object by track, validates "
                "the standard metadata columns (value distribution, not just column "
                "name), measures poll and extraction cost, checks the connector "
                "merge-versus-reload pattern, and returns a verdict."
            )),
            ("code", (
                "cd probes\n"
                "pip install -r requirements.txt\n"
                "python3 cdc_readiness.py --connection <your_connection> "
                "--report readiness.md\n"
                "\n"
                "# Add --with-scratch-tests to prove the mechanism directly\n"
                "# (creates a dynamic table in a scratch database then drops it)"
            )),
            ("table", {
                "headers": ["Verdict", "Meaning and action"],
                "rows": [
                    ["READY",
                     "All checks passed. Proceed with deployment."],
                    ["READY WITH CAVEATS",
                     "Core mechanism works; at least one signal is degraded. "
                     "Check which probe is flagged and read its output. "
                     "Dynamic tables remain the recommended engine."],
                    ["NOT READY",
                     "A critical check failed. Most likely: connector reloads, "
                     "no watermark column found, or share is absent. "
                     "Do not deploy until the cause is understood."],
                    ["NOT ASSESSABLE",
                     "A probe could not run, usually insufficient privilege or "
                     "no catalog-linked database found. Resolve and re-run."],
                ],
                "col_widths": [0.22, 0.78],
            }),
        ],
    },
]


# The repository's own diagrams, attached to the sections they illustrate. Kept as
# a mapping rather than inline in WP_SECTIONS so prose and artwork can be edited
# independently, and so that a checkout without assets/ still produces a readable
# text-only document instead of failing.
#
# Captions follow the descriptions the README already uses for these images, so the
# paper and the repository describe the same picture the same way.
WP_FIGURES: dict[str, list[tuple[str, str]]] = {
    "1.  Overview": [
        ("D1_problem.png",
         "Figure 1. The problem capture is asked to solve."),
        ("D3_architecture.png",
         "Figure 2. Where capture sits between the SAP share and the Gold layer."),
    ],
    "2.  Why standard CDC is unavailable": [
        ("D2_capability.png",
         "Figure 3. What the platform permits on a catalog-linked share."),
    ],
    "4.  Track 1 - Custom data products": [
        ("D4_connector_writes.png",
         "Figure 4. The connector write pattern the watermark depends on."),
        ("D5_cost.png",
         "Figure 5. Scan cost: a watermark predicate against a full read."),
    ],
    "9.  Decision guide": [
        ("D6_decision.png",
         "Figure 6. Choosing a mechanism."),
    ],
    "10.  Open asks to SAP": [
        ("D7_gaps.png",
         "Figure 7. What capture does not solve."),
    ],
}

for _section in WP_SECTIONS:
    for _file, _caption in WP_FIGURES.get(_section["heading"], []):
        _section["body"].append(
            ("figure", {"file": _file, "caption": _caption, "width": 0.90})
        )

# ═══════════════════════════════════════════════════════════════════════════
# Content - CDC Enablement Recommendation
# ═══════════════════════════════════════════════════════════════════════════

REC_TITLE    = "Enabling Change Data Capture on SAP BDC Data Products"
REC_SUBTITLE = "What the standard configuration teaches, and what to ask for on a custom one"

REC_SECTIONS = [
    {
        "heading": "1.  The question, and the short answer",
        "level": 1,
        "body": [
            ("para",
             "Standard SAP BDC data products arrive with four connector-generated "
             "change columns. Custom data products - those built through SLT "
             "configuration - arrive with whatever their pipeline owner chose to "
             "expose, commonly a single change timestamp. The question this paper "
             "answers is what a custom data product should carry in order to support "
             "incremental capture properly, using the standard configuration as the "
             "specification."),
            ("para",
             "The short answer is not the obvious one. Of the four standard columns, "
             "one should be adopted, one should be adopted but NOT in the form SAP "
             "ships it, one is a convenience, and one has no value for capture at "
             "all. On the two properties that actually determine what capture costs "
             "and whether it is correct - watermark typing and predicate pruning - "
             "the reference custom product measured ahead of the reference standard "
             "product."),
            ("warn",
             "And before asking SAP for anything: the highest-value action found in "
             "this work required nothing from SAP. The reference custom share already "
             "carried SAP's own deletion and blocking codes, populated on 19.0% of "
             "purchase order items and accounting for 42.2% of reported USD ordered "
             "value, and the Gold layer discarded them. Section 5."),
        ],
    },
    {
        "heading": "2.  Why the standard configuration is the right yardstick",
        "level": 1,
        "body": [
            ("para",
             "A catalog-linked database holds Iceberg tables on an SAP-managed "
             "external catalog. Snowflake's three native change mechanisms are all "
             "refused on them, which is what makes the share's own columns the whole "
             "of the available design space:"),
            ("table", {
                "headers": ["Attempted operation", "Error returned"],
                "rows": [
                    ["CREATE STREAM ... ON TABLE <share>",
                     "Streams on External Tables or Iceberg tables with an external "
                     "catalog must have INSERT_ONLY set to true"],
                    ["SELECT ... CHANGES(INFORMATION => DEFAULT)",
                     "CHANGES clause is not supported on external tables or Iceberg "
                     "tables with an external catalog"],
                    ["ALTER TABLE ... SET CHANGE_TRACKING = TRUE",
                     "change_tracking cannot be set for Iceberg tables unless they "
                     "use Snowflake catalog"],
                ],
                "col_widths": [0.42, 0.58],
            }),
            ("note",
             "SHOW TABLES reports change_tracking = ON on these objects regardless. "
             "That flag is uninformative here and must not be read as evidence that a "
             "stream-based design is available."),
            ("para",
             "Because nothing can be added from the Snowflake side, whatever the "
             "share carries is the change signal. The four standard columns are "
             "therefore worth reading as SAP's own answer to the question 'what does "
             "a consumer need in order to process this incrementally' - not as a "
             "guess, but as a shipped contract. That makes them the natural "
             "specification to hold a custom product against."),
        ],
    },
    {
        "heading": "3.  What each standard column is actually worth",
        "level": 1,
        "body": [
            ("para",
             "Measured on a live standard data product across five non-empty objects "
             "(67,480 rows). The verdict column is the point: the four columns are "
             "not of equal value, and one of them has no value for capture at all."),
            ("table", {
                "headers": ["Column", "What was measured", "Verdict"],
                "rows": [
                    ["__TIMESTAMP",
                     "Row write time. TEXT, ISO-8601 with a COMMA decimal separator "
                     "(2026-06-15T09:41:59,6426580Z). MAX() on the raw text scanned "
                     "0.000 MB in 2 ms from Iceberg manifest statistics; MAX() over a "
                     "parsed expression scanned 0.310 MB in 45 ms. Lexicographic "
                     "order equals chronological order, verified.",
                     "Essential. The TEXT typing is a liability, not a feature."],
                    ["__OPERATION_TYPE",
                     "Change mode. Only L (initial load, 67,087 rows) and A (delta "
                     "after-image, 393 rows) ever appeared. No delete code was "
                     "observed anywhere, and the authoritative value list could not "
                     "be confirmed from SAP documentation.",
                     "Necessary in principle. Unproven in practice."],
                    ["load_type_<hash>",
                     "Load phase. initial = 67,087, delta = 393. Confirms the "
                     "connector appends rather than reloading, which is the "
                     "precondition for any watermark to mean anything.",
                     "Useful, cheap, and replaceable by inference."],
                    ["run_id_<hash>",
                     "Held the SAME value on the initial snapshot and on every delta "
                     "that followed it. Identifies the replication flow, not the "
                     "batch. The 32-hex suffix is likewise per product, not per "
                     "object - identical across all seven objects.",
                     "No value for capture. Do not replicate it."],
                ],
                "col_widths": [0.17, 0.53, 0.30],
            }),
            ("para",
             "The __TIMESTAMP finding is the one that generalises into a rule. "
             "Because the format is fixed-width and zero-padded, a text comparison is "
             "both correct and free, while parsing before comparing turns a free "
             "poll into a scan on every cycle in order to learn nothing. Any "
             "implementation on this track should compare as text and parse only for "
             "display."),
        ],
    },
    {
        "heading": "4.  Scoring a custom product against the specification",
        "level": 1,
        "body": [
            ("para",
             "The reference custom-track account carries ZZLASTCHGDATE on 23 of 23 "
             "L1 objects, with 59 objects carrying it account-wide and no "
             "connector metadata columns anywhere. Scoring it against the four "
             "standard capabilities, plus the two properties that govern cost:"),
            ("table", {
                "headers": ["Capability", "Standard product", "Custom product",
                            "Ahead"],
                "rows": [
                    ["Row change timestamp",
                     "__TIMESTAMP, TEXT with comma separator; an unguarded cast "
                     "returns NULL on every row",
                     "ZZLASTCHGDATE, TIMESTAMP_NTZ, zero nulls across 4,314,292 / "
                     "1,073,135 / 108,615,680 rows",
                     "custom"],
                    ["Operation code",
                     "__OPERATION_TYPE present, but only L and A observed and no "
                     "delete code",
                     "none generated; SAP business deletion codes present and "
                     "populated (section 5)",
                     "neither"],
                    ["Load phase",
                     "load_type declared",
                     "inferable - 4,314,285 rows on the load day, then 6, then 1",
                     "standard"],
                    ["Flow identity",
                     "run_id, measured not to be a batch discriminator",
                     "none",
                     "neither"],
                    ["Predicate pruning",
                     "NOT demonstrated - the reference objects are a single "
                     "micropartition (39,462 rows, 320 KB) and the predicate read "
                     "essentially everything",
                     "0.00 MB against 7,035.80 MB for a full read - 0.0000% - on a "
                     "108,615,680-row object",
                     "custom"],
                    ["Free no-change poll",
                     "0.000 MB in 2 ms, but only if compared as text",
                     "0.00 MB in 47-79 ms",
                     "tie"],
                ],
                "col_widths": [0.16, 0.30, 0.32, 0.11],
            }),
            ("para",
             "The engine is identical on both tracks and proven on both. On the "
             "custom track an initial refresh inserted 4,314,292 rows in 18.3 s and "
             "the next no-change refresh returned NO_DATA in 984 ms, with 10 of 10 "
             "Gold-layer dynamic tables INCREMENTAL once REFRESH_MODE was pinned "
             "explicitly. On the standard track a dynamic table over the live share "
             "refreshed INCREMENTAL in 5,241 ms and then returned NO_DATA in 610 ms, "
             "with a watermark poll returning REFRESHED and then NO CHANGE."),
            ("note",
             "REFRESH_MODE must be stated explicitly on both tracks. Left to AUTO, "
             "Snowflake selects FULL on a non-trivial query, and FULL cascades - "
             "change tracking is unsupported on a FULL dynamic table, so everything "
             "downstream is forced to FULL as well."),
        ],
    },
    {
        "heading": "5.  The finding that reorders the priorities",
        "level": 1,
        "body": [
            ("para",
             "The custom track has no connector-generated operation column, and the "
             "usual conclusion drawn from that is that deletes are simply invisible. "
             "That conclusion is too broad. SAP's own business deletion and blocking "
             "codes were present on the reference custom share, populated, and "
             "material - and the Gold layer dropped them."),
            ("table", {
                "headers": ["Deletion code on purchase order items", "Items",
                            "Share"],
                "rows": [
                    ["(blank) - active", "3,494,547", "81.0%"],
                    ["L", "812,913", "18.8%"],
                    ["S", "6,831", "0.2%"],
                    ["X", "1", "0.0%"],
                    ["Flagged in total", "819,745", "19.0%"],
                ],
                "col_widths": [0.56, 0.24, 0.20],
            }),
            ("para",
             "Restricted to a single currency, so that the figures are additive: of "
             "69.50 bn USD of ordered value on those items, 40.17 bn sits on active "
             "items and 29.33 bn - 42.2% - sits on items carrying a deletion or "
             "blocking code. The same 69.50 bn is the headline ordered-value figure "
             "the Gold layer reports."),
            ("warn",
             "The deletion codes exist in the Silver layer and do not reach Gold. The "
             "Gold purchase order table carries all 4,314,292 rows, the same count as "
             "its source, with no deletion column and no filter. Deleted and blocked "
             "items are therefore included in reported value, unlabelled and "
             "unfilterable by the consumer."),
            ("para",
             "The same pattern appears elsewhere on the share: a deletion code on "
             "purchase order headers (1,594 flagged), ISMARKEDFORDELETION on products "
             "(1,086 flagged), and DELETIONINDICATOR, ACCOUNTISMARKEDFORDELETION and "
             "SUPPLIERCENTRALDELETIONISBLOCK across master data."),
            ("para",
             "Two qualifications matter. These are BUSINESS deletion flags - a record "
             "marked deleted in the source - not replication operation codes; a row "
             "physically removed from the share still leaves no trace on this track. "
             "And whether L and S items should be excluded from spend, shown "
             "separately, or retained is a business decision rather than a technical "
             "one. What is not defensible is carrying them silently and unlabelled, "
             "which is the current behaviour."),
        ],
    },
    {
        "heading": "6.  The recommendation",
        "level": 1,
        "body": [
            ("para",
             "In priority order, highest value first. Only item 3 requires anything "
             "from SAP."),
            ("h2", "6.1  Surface the change metadata the share already carries"),
            ("para",
             "Propagate the deletion and blocking codes from Silver to Gold and into "
             "the semantic layer, so a consumer can filter or label them. This costs "
             "nothing, requires no SAP change, and on the reference account it "
             "governs 19.0% of purchase order items and 42.2% of reported USD "
             "ordered value. It also delivers most of what an operation column would "
             "deliver, because logical deletion is the dominant deletion mode in SAP."),
            ("h2", "6.2  Keep the watermark strongly typed - do not copy the standard form"),
            ("para",
             "Specify the change timestamp as TIMESTAMP_NTZ and NOT NULL. The "
             "reference custom product already achieves zero nulls across 114 million "
             "rows; make that a contract rather than a happy accident. Explicitly do "
             "NOT adopt __TIMESTAMP's TEXT-with-comma representation - it casts "
             "silently to NULL and would be a regression."),
            ("h2", "6.3  Ask for a replication operation code, or a soft-delete guarantee"),
            ("para",
             "This is the one genuine capability gap, and the only item here that is "
             "an ask to the pipeline owner. Either emit an operation code that "
             "distinguishes insert, update and physical delete, or guarantee that "
             "rows are soft-deleted rather than removed. A row that simply stops "
             "being carried is unrecoverable by any watermark design. Note that the "
             "standard track has not been shown to satisfy this either, so it is an "
             "ask for both tracks rather than a reason to prefer one."),
            ("h2", "6.4  Add a declared load-phase flag"),
            ("para",
             "A column distinguishing the initial snapshot from subsequent deltas "
             "removes the need to infer the load phase from the timestamp "
             "distribution. Inference works today - 4,314,285 rows on the load day, "
             "then 6, then 1 - so this is a convenience rather than a requirement."),
            ("h2", "6.5  Do not replicate run_id"),
            ("para",
             "It was measured to hold the same value on the initial snapshot and on "
             "every delta, so it identifies the flow rather than the batch and adds "
             "no capture capability. Copying it into a custom product would add a "
             "column and no information."),
        ],
    },
    {
        "heading": "7.  What this means for choosing between product types",
        "level": 1,
        "body": [
            ("para",
             "Where a standard data product exists, prefer it - for one structural "
             "reason. It carries an operation column at all, which means a delete can "
             "in principle be represented, and no source-system configuration is "
             "required to obtain it. That is a real advantage and it is the right "
             "default."),
            ("para",
             "But a standard product is not a shortcut to a correct design. It ships "
             "a worse timestamp representation, an operation enumeration that could "
             "not be confirmed and in which no delete code was ever observed, a flow "
             "identifier of no use to capture, and metadata that is destroyed by the "
             "ordinary act of copying the share. Its predicate pruning was not "
             "demonstrated. A well-configured custom product with its deletion codes "
             "surfaced is materially closer to a correct and economical CDC design "
             "than an unvalidated standard product is."),
            ("para",
             "The practical conclusion is that product type is a weaker determinant "
             "of CDC quality than configuration and validation are. Both tracks run "
             "the same engine, and on both the deciding questions are whether the "
             "watermark is trustworthy, whether the connector merges rather than "
             "reloads, and whether deletion is represented at all."),
        ],
    },
    {
        "heading": "8.  What is not established",
        "level": 1,
        "body": [
            ("para",
             "Four things are open, and each is stated as open rather than smoothed "
             "over:"),
            ("para",
             "First, the authoritative __OPERATION_TYPE enumeration, and whether a "
             "delete code is ever emitted. Only L and A were observed. The SAP "
             "documentation page is JavaScript-rendered and could not be read; a web "
             "search surfaces the SAP Datasphere BigQuery adapter, which is a "
             "different target and is deliberately not cited here as authority. Until "
             "this is answered, treat physical deletes as unrepresented on both "
             "tracks and reconcile row counts on a schedule."),
            ("para",
             "Second, whether a watermark predicate prunes on the standard track at "
             "production scale. It did not prune on the reference objects, but those "
             "are a single micropartition, so the test had no headroom."),
            ("para",
             "Third, what a dynamic table refresh does when the source has genuinely "
             "changed after the initial load. Refreshes were observed as INCREMENTAL "
             "on first build and NO_DATA when nothing had changed; a refresh driven by "
             "a new connector write was not observed on either account."),
            ("para",
             "Fourth, whether the SAP deletion codes L, S and X should exclude items "
             "from spend reporting. That is a business determination, not a "
             "measurement, and this paper deliberately stops at establishing that the "
             "codes are populated, material, and currently discarded."),
        ],
    },
    {
        "heading": "9.  Verifying this on your own tenant",
        "level": 1,
        "body": [
            ("para",
             "None of the above should be taken on trust. The accompanying probe "
             "classifies every object by track, validates the standard metadata "
             "columns, measures poll and extraction cost separately, and returns a "
             "verdict:"),
            ("code",
             "cd probes\n"
             "python3 cdc_readiness.py --connection <your_account> --report readiness.md"),
            ("para",
             "The validation step matters most, because the metadata columns cannot "
             "be trusted on sight. On the reference tenant they were clean inside the "
             "live catalog-linked database and corrupted on every local copy of it - "
             "on one product __OPERATION_TYPE held media titles, and on another "
             "__OPERATION_TYPE, __TIMESTAMP and LOAD_TYPE all held the same customer "
             "identifier on 735,305 of 735,305 rows with RUN_ID null. The thresholds "
             "the probe applies come from that measured contrast: an operation column "
             "with cardinality above 12 or values longer than two characters is not a "
             "change code, a timestamp column where nothing parses is unusable, and "
             "operation equal to timestamp on any row indicates a damaged copy."),
            ("para",
             "The deployable SQL is in sql/, with sql/05_standard_product_track.sql "
             "covering validation, the free poll, freshness and a merge template for "
             "the standard track, and sql/04 covering the custom-track exception "
             "path. Point capture at the catalog-linked database itself and never at "
             "a copy of it."),
        ],
    },
]


# ═══════════════════════════════════════════════════════════════════════════
# Content - Executive Overview deck
# ═══════════════════════════════════════════════════════════════════════════
#
# A separate deck from the solution overview on purpose. That one is a technical
# walkthrough of the mechanism; this one answers an executive's three questions -
# what are the two options, what does each cost, and what do you want me to
# approve - and it leans on the diagrams rather than on bullet density.

EXEC_SLIDES = [
    {
        "type": "title",
        "title": "Change Data Capture on\nSAP Business Data Cloud",
        "subtitle": (
            "How it is done on standard data products, and what to ask for on custom ones"
            "\n\n"
            "Executive overview  |  August 2026  |  All figures measured on live tenants"
        ),
    },
    {
        "type": "bullets",
        "title": "The situation",
        "bullets": [
            "SAP BDC data products arrive in Snowflake as catalog-linked shares - "
            "read-only, zero-copy, no storage cost",
            "Nothing in the share tells the Gold layer what changed, so the default "
            "behaviour is to re-read all of it on a schedule",
            "Snowflake's three native change mechanisms are all refused on these "
            "objects, because they are Iceberg tables on an SAP-managed catalog",
            "So the change signal has to come from the share itself - and what the "
            "share carries depends on which kind of data product it is",
            "That distinction is the subject of this deck. It decides whether CDC is "
            "a procedure to run or a specification to negotiate",
        ],
    },
    {
        "type": "figure",
        "title": "Two kinds of data product, two different jobs",
        "file": "D10_delineation.png",
        "caption": "The engine is identical on both. What differs is who supplies the "
                   "change signal, and whether a delete can be represented at all.",
    },
    {
        "type": "figure",
        "title": "Standard data products - a procedure",
        "file": "D8_standard_track.png",
        "caption": "SAP generates the change columns. Validate them, then poll them "
                   "for free. No source-system change is required.",
    },
    {
        "type": "figure",
        "title": "Custom data products - a specification",
        "file": "D9_custom_track.png",
        "caption": "Nothing is generated. Two capabilities are already in the share, "
                   "one is present but discarded, and one must be requested.",
    },
    {
        "type": "table",
        "title": "What it costs, measured",
        "headers": ["", "Standard product", "Custom product"],
        "rows": [
            ["Detecting no change",
             "0.000 MB in 2 ms - free",
             "0.00 MB in 47-79 ms - free"],
            ["Reading only the change",
             "not demonstrated at scale",
             "0.0000% of a full read, on 108,615,680 rows"],
            ["Refresh when nothing moved",
             "NO_DATA in 610 ms",
             "NO_DATA in 984 ms"],
            ["Refresh with data",
             "INCREMENTAL in 5,241 ms",
             "4,314,292 rows in 18.3 s"],
            ["Code to write",
             "none - dynamic tables",
             "none - dynamic tables"],
        ],
        "col_widths": [0.26, 0.34, 0.40],
    },
    {
        "type": "risk",
        "title": "The risk on standard products: never trust a copy",
        "risk_statement": "The connector metadata was reliable inside the live "
                          "share and corrupted in every local copy of it we examined.",
        "bullets": [
            "On one product the operation column held media titles - 17 distinct "
            "values, up to 33 characters, plus 16,317 nulls",
            "On another, three metadata columns all held the same customer "
            "identifier across 735,305 of 735,305 rows",
            "The defect propagated downstream into the layers built on those copies",
            "None of it raises an error. It produces a wrong incremental result "
            "quietly, which is the expensive kind of wrong",
        ],
        "rule": "Point capture at the catalog-linked share itself, and validate the "
                "columns before building on them. Both are one-line checks.",
    },
    {
        "type": "risk",
        "title": "The finding that changed our recommendation",
        "risk_statement": "SAP's own deletion and blocking codes are already in "
                          "the share - and the Gold layer drops them.",
        "bullets": [
            "19.0% of purchase order items carry a deletion or blocking code - "
            "819,745 of 4,314,292",
            "In USD, where the amounts are additive: 29.33 bn of 69.50 bn of ordered "
            "value sits on flagged items - 42.2%",
            "That 69.50 bn is the same headline ordered-value figure the Gold layer "
            "reports today, with no label and no way to filter",
            "Surfacing those codes costs nothing and requires nothing from SAP",
        ],
        "rule": "Whether deleted and blocked items belong in spend is a business "
                "decision. Carrying them silently is not defensible either way.",
    },
    {
        "type": "bullets_detail",
        "title": "The recommendation",
        "intro": "In priority order. Only item 3 is a request to SAP.",
        "bullets": [
            "1. Surface the deletion and blocking codes already in the share, so "
            "reported value can be labelled or filtered. Free, and worth 42.2% of the "
            "headline figure",
            "2. Keep the change timestamp strongly typed and never null. The custom "
            "track already does this better than the standard one does",
            "3. Ask SAP for a replication operation code, or a guarantee that "
            "deletion is soft. This is the one genuine capability gap - and it is "
            "open on both tracks",
            "4. Add a declared load-phase flag. A convenience; inference works today",
            "5. Do not copy run_id into a custom product - it carries no change "
            "information",
        ],
        "caveat": "Prefer a standard data product where one exists, because only it "
                  "carries an operation column. But product type matters less than "
                  "configuration and validation do.",
    },
    {
        "type": "bullets",
        "title": "What we do not yet know",
        "bullets": [
            "Whether the standard operation column ever emits a delete code - only "
            "'initial load' and 'delta' values were ever observed",
            "The authoritative list of operation values. SAP's documentation page "
            "could not be read, and the nearest published equivalent describes a "
            "different target, so we do not cite it",
            "Whether a change predicate reads only the change at production scale on "
            "the standard track - the reference tables were too small to tell",
            "Until the delete question is answered, row counts must be reconciled on "
            "a schedule on both tracks",
            "These are open asks to SAP, not blockers. Capture works today on both "
            "tracks without them",
        ],
    },
    {
        "type": "closing",
        "title": "Next steps, and what we are asking SAP",
        "next_steps": [
            "Run the readiness probe against the account - about a minute, read-only, "
            "classifies every object as standard or custom and validates the metadata",
            "Surface the deletion and blocking codes from Silver to Gold, so reported "
            "value can be labelled or filtered. Free, and worth 42.2% of the headline "
            "USD figure",
            "Confirm REFRESH_MODE = INCREMENTAL is pinned on every Gold dynamic table, "
            "so a silent fall back to FULL cannot cascade",
            "Decide, as a business question, whether logically deleted and blocked "
            "items belong in spend reporting",
            "Schedule row-count reconciliation on both tracks until the delete "
            "question is closed",
        ],
        "open_asks": [
            "An authoritative list of operation-code values, and whether a delete "
            "code is ever emitted",
            "Why the standard change timestamp ships as text with a comma decimal "
            "separator rather than as a timestamp",
            "Whether change_tracking = ON means anything on these objects, given "
            "streams and CHANGES() are both refused",
            "Declared primary keys per object, since the merge grain currently comes "
            "from source-system knowledge",
        ],
    },
]


# ═══════════════════════════════════════════════════════════════════════════
# Content - Executive Summary
# ═══════════════════════════════════════════════════════════════════════════

ES_TITLE    = "SAP BDC Change Data Capture"
ES_SUBTITLE = "Executive Summary - " + BUILD_DATE

ES_SECTIONS = [
    {
        "heading": "The situation",
        "body": [
            ("para", (
                "SAP Business Data Cloud delivers data products into Snowflake as "
                "catalog-linked databases. Consumers on the reference accounts measured "
                "data arriving hours to a day behind business events because their Gold "
                "layers re-read the entire share on a schedule - nothing signalled what "
                "had changed."
            )),
            ("para", (
                "Snowflake's standard change-data-capture mechanisms - streams, "
                "CHANGES(), and change tracking - are refused on these objects. They "
                "are Iceberg tables on an external SAP-managed catalog, and that single "
                "fact explains why a different approach is needed."
            )),
        ],
    },
    {
        "heading": "What was found",
        "body": [
            ("para", "Two change signals exist. Which one applies depends on the kind of data product:"),
            ("table", {
                "headers": ["Track", "Signal", "Deletes"],
                "rows": [
                    ["Track 1\nCustom products",
                     "Source-system timestamp (e.g. ZZLASTCHGDATE), configured by "
                     "the pipeline owner. Found on 59 objects across the reference "
                     "account.",
                     "No operation column. Invisible on this track. "
                     "Reconcile row counts on a schedule."],
                    ["Track 2\nStandard products",
                     "Four SAP-generated columns: __OPERATION_TYPE, __TIMESTAMP, "
                     "and two hash-suffixed columns. Nothing to configure in "
                     "the source system.",
                     "Operation column present. Delete code not observed and not "
                     "confirmed by SAP documentation (unverified). "
                     "Reconcile row counts on a schedule."],
                ],
                "col_widths": [0.15, 0.50, 0.35],
            }),
            ("para", (
                "On Track 1, a watermark predicate returned changed rows while "
                "scanning 0.00 MB of a 7,035.80 MB object containing 108 million rows. "
                "Polling the watermark cost 0.00 MB and 47-79 ms. On Track 2, "
                "polling the current timestamp as raw text cost 0.000 MB in 2 ms, "
                "answered from Iceberg manifest statistics. Dynamic tables with "
                "REFRESH_MODE = INCREMENTAL were proven end-to-end on both tracks; "
                "a no-change refresh returned NO_DATA in under one second."
            )),
        ],
    },
    {
        "heading": "The decision",
        "body": [
            ("para", (
                "1.  Prefer standard data products where they exist. Only they carry "
                "an operation column, so only they can represent a delete."
            )),
            ("para", (
                "2.  On both tracks, use dynamic tables with REFRESH_MODE pinned "
                "explicitly to INCREMENTAL. The watermark is a trigger and exception "
                "path, not the engine. Never leave REFRESH_MODE as AUTO: it falls back "
                "silently to full-rebuild mode with no warning."
            )),
            ("para", (
                "3.  Point capture at the catalog-linked database. Never at a copy. "
                "Validate the standard metadata before building on it."
            )),
            ("para", (
                "4.  Reconcile row counts on a schedule on both tracks. Deletes are "
                "unrepresented on Track 1 and unverified on Track 2."
            )),
        ],
    },
    {
        "heading": "The risk",
        "body": [
            ("warn", (
                "Every local copy of a standard data product on the reference account "
                "had corrupted metadata. On one copy, the operation column held media "
                "titles. On two others, it held customer identifiers across all rows "
                "(735,305 of 735,305 and 557,693 of 558,011 respectively). None of "
                "this failed loudly. It produced a wrong incremental result quietly "
                "and the defect propagated into downstream medallion layers."
            )),
            ("para", (
                "This is not theoretical: three separate copies on a live account "
                "were all corrupted. The safeguard is to point capture at the "
                "catalog-linked database itself and validate the metadata columns. "
                "The readiness probe does both."
            )),
        ],
    },
    {
        "heading": "What to do now",
        "body": [
            ("table", {
                "headers": ["Step", "Action"],
                "rows": [
                    ["1",
                     "Run probes/cdc_readiness.py against your tenant. It classifies "
                     "every object by track and validates the metadata in under a "
                     "minute. Review the verdict before deploying anything."],
                    ["2",
                     "Create Gold-layer dynamic tables with REFRESH_MODE = INCREMENTAL "
                     "stated explicitly. Never AUTO. One wrong setting cascades "
                     "downstream."],
                    ["3",
                     "Deploy sql/03_monitoring.sql - specifically VW_RELOAD_DETECTION. "
                     "The pattern rests on the connector merging; a switch to reload "
                     "raises no error and is only visible through this monitor."],
                    ["4",
                     "Reconcile row counts on a regular schedule. Dynamic tables "
                     "capture physical deletes; the watermark path does not. "
                     "Either way, an independent count check is the safety net."],
                ],
                "col_widths": [0.05, 0.95],
            }),
        ],
    },
]


# ═══════════════════════════════════════════════════════════════════════════
# Content - Deck (12 slides)
# ═══════════════════════════════════════════════════════════════════════════

# Each slide dict: "type", "title", and type-specific fields.
# Types: "title", "bullets", "bullets_detail", "risk", "table", "closing"

SLIDES = [
    {
        "type": "title",
        "title": "Change Data Capture\nfrom SAP BDC Catalog-Linked Shares",
        "subtitle": (
            "A dual-track technical walkthrough\n\n"
            "August 2026  |  Vendor-neutral community pattern  |  Apache 2.0"
        ),
    },
    {
        "type": "bullets",
        "title": "The challenge",
        "bullets": [
            "SAP BDC delivers data products into Snowflake as catalog-linked databases",
            "Consumers report data arriving hours to a day behind business events",
            "The Gold layer re-reads the entire share on a schedule because nothing signals what changed",
            "The obvious answers - add a stream, enable change tracking - are all refused",
            "The objects are Iceberg tables on an SAP-managed external catalog; "
            "standard CDC mechanisms do not apply",
            "Two alternative signals exist, and the right engine is already in Snowflake",
        ],
    },
    {
        "type": "bullets",
        "title": "Why standard CDC is refused",
        "bullets": [
            "SHOW TABLES reports kind=TABLE, is_external=N - this is misleading",
            "CREATE STREAM: 'Streams on External Tables or Iceberg tables with an "
            "external catalog must have INSERT_ONLY set to true'",
            "CHANGES() clause: 'not supported on external tables or Iceberg tables "
            "with an external catalog'",
            "ALTER TABLE SET CHANGE_TRACKING: 'cannot be set for Iceberg tables "
            "unless they use Snowflake catalog'",
            "SHOW TABLES reports change_tracking=ON - but both mechanisms are still "
            "refused. That ON is misleading.",
            "INSERT_ONLY streams are permitted but cannot detect physical deletes "
            "and expire after 14 days unread",
        ],
    },
    {
        "type": "bullets",
        "title": "Two tracks, one engine",
        "bullets": [
            "Which signal is available depends on the kind of data product",
            "Track 1 - Custom: source-system timestamp (e.g. ZZLASTCHGDATE), "
            "added by SLT pipeline configuration; 59 objects across reference account",
            "Track 2 - Standard: four SAP-generated metadata columns "
            "(__OPERATION_TYPE, __TIMESTAMP, and two hash-suffixed columns); "
            "nothing to configure",
            "The engine is the same on both tracks: dynamic tables with "
            "REFRESH_MODE = INCREMENTAL stated explicitly",
            "The watermark is the trigger and exception path, not the engine",
            "Start with the readiness probe: probes/cdc_readiness.py classifies "
            "every object and validates the signal before you deploy anything",
        ],
    },
    {
        "type": "bullets_detail",
        "title": "Track 1 - Custom data products",
        "intro": (
            "The connector merges rather than reloads. Historical rows keep their "
            "original replication timestamp; new data lands in new Parquet files. "
            "Per-file Iceberg statistics span a narrow range, enabling near-total "
            "file elimination on a watermark predicate."
        ),
        "bullets": [
            "108M-row GL object: watermark predicate scanned 0.00 MB vs "
            "7,035.80 MB for a full read",
            "Business-date predicate on the same object: 4,695.75 MB "
            "(scattered across all files - the wrong tool)",
            "Poll cost: 0.00 MB, 47-79 ms",
            "10 of 10 Gold dynamic tables confirmed INCREMENTAL once "
            "REFRESH_MODE was pinned explicitly",
            "Initial load: 4,314,292 rows in 18.3 s; no-change refresh: "
            "NO_DATA in 984 ms",
        ],
        "caveat": (
            "No operation column on this track. Deletes are invisible. "
            "Reconcile row counts on a schedule."
        ),
    },
    {
        "type": "bullets_detail",
        "title": "Track 2 - Standard data products",
        "intro": (
            "SAP generates four metadata columns. __TIMESTAMP arrives as TEXT "
            "with a comma decimal separator (2026-06-15T09:41:59,6426580Z). "
            "TRY_TO_TIMESTAMP_NTZ returns NULL silently on the raw value - "
            "an unguarded cast looks like an empty column."
        ),
        "bullets": [
            "MAX(__TIMESTAMP) as raw text: 0.000 MB, 2 ms - from manifest statistics",
            "MAX(parsed expression): 0.310 MB, 45 ms - must scan micropartitions",
            "Lexicographic order = chronological order (verified on reference data)",
            "Operations observed: L=67,087 (initial), A=393 (delta); "
            "delete code: 0 observed, unverified",
            "End-to-end: 3,355 rows; INCREMENTAL in 5,241 ms; NO_DATA in 610 ms",
            "run_id held the SAME value on both initial and delta rows - "
            "not a batch discriminator",
        ],
        "caveat": (
            "Predicate pruning NOT demonstrated at scale (single micropartition). "
            "Delete code unverified. Use dynamic tables as the engine."
        ),
    },
    {
        "type": "risk",
        "title": "The headline risk: never trust a local copy",
        "risk_statement": (
            "The metadata columns were reliable only inside the live catalog-linked "
            "database. Every local copy examined had corrupted metadata. "
            "None of this fails loudly."
        ),
        "bullets": [
            "Copy 1: __OPERATION_TYPE held media titles "
            "('Mission Possible 1', 'About a Girl - Series') + 16,317 NULLs; "
            "17 distinct values up to 33 chars",
            "Copy 2: __OPERATION_TYPE, __TIMESTAMP, and LOAD_TYPE all held "
            "customer identifiers (620 distinct) on 735,305 of 735,305 rows",
            "Copy 3: same degeneracy on 557,693 of 558,011 rows",
            "Defect propagated downstream into later medallion layers",
            "Local copies lose the hash suffix - its absence marks a non-live source",
            "A downstream filter on __OPERATION_TYPE = 'A' returns zero rows "
            "or rows matched on customer IDs, silently",
        ],
        "rule": (
            "Point capture at the catalog-linked database. "
            "Validate metadata before building on it. "
            "Treat the hash suffix as a liveness marker."
        ),
    },
    {
        "type": "bullets",
        "title": "The engine: dynamic tables",
        "bullets": [
            "REFRESH_MODE = INCREMENTAL - stated explicitly, always. Never AUTO.",
            "AUTO falls back silently to FULL when it cannot support incremental; "
            "FULL cascades: it forces everything downstream to FULL as well",
            "Dynamic tables are the only mechanism that captures physical deletes "
            "(they diff consecutive source snapshots)",
            "Four query shapes verified INCREMENTAL on a live share: flat projection, "
            "join, GROUP BY aggregate, left join with CTE",
            "NO_DATA in 348-610 ms on no-change cycles (Snowflake checks manifest "
            "statistics without reading source data)",
            "Watermark path is the exception: use it for non-deterministic transforms "
            "or targets outside Snowflake",
        ],
    },
    {
        "type": "table",
        "title": "Mechanism comparison",
        "headers": ["", "Dynamic table", "Watermark + MERGE", "INSERT_ONLY stream"],
        "rows": [
            ["Inserts", "Yes", "Yes", "Yes"],
            ["Updates", "Yes", "Yes", "Yes"],
            ["Physical deletes", "Yes", "No", "No"],
            ["No-change detection", "NO_DATA in <1 s", "0 MB poll", "Yes"],
            ["Code required", "None", "Task + MERGE + control", "Task + MERGE"],
            ["Can feed outside Snowflake", "No", "Yes", "Yes"],
            ["Goes stale unread", "No", "No", "14 days"],
            ["Blocked by non-deterministic SQL", "Yes", "No", "No"],
        ],
        "col_widths": [0.30, 0.22, 0.28, 0.20],
    },
    {
        "type": "bullets",
        "title": "Decision guide",
        "bullets": [
            "Prefer standard data products - only they can represent a delete",
            "Both tracks: dynamic tables with REFRESH_MODE = INCREMENTAL pinned explicitly",
            "Point capture at the live catalog-linked database, never a copy",
            "Validate standard metadata before building - column name is not enough",
            "Reconcile row counts on a schedule regardless of track",
            "Watermark path only for: non-deterministic transforms or external "
            "(non-Snowflake) targets",
            "Deploy VW_RELOAD_DETECTION (sql/03_monitoring.sql) - connector switching "
            "to reload raises no error",
        ],
    },
    {
        "type": "bullets",
        "title": "The readiness probe",
        "bullets": [
            "Run probes/cdc_readiness.py before deploying anything on a new tenant",
            "Classifies every object: standard track, custom track, or no signal",
            "Validates standard metadata by checking value distribution, "
            "not just column name presence",
            "Measures poll cost and extraction cost against the live share",
            "Checks connector merge-versus-reload behaviour",
            "Returns READY / READY WITH CAVEATS / NOT READY / NOT ASSESSABLE",
            "Re-run with --with-scratch-tests to prove the dynamic table mechanism "
            "directly (creates a table in a scratch database then drops it)",
        ],
    },
    {
        "type": "closing",
        "title": "Next steps and open asks to SAP",
        "next_steps": [
            "Run probes/cdc_readiness.py on your tenant",
            "Deploy sql/ scripts in order: 00 through 04",
            "Pin REFRESH_MODE = INCREMENTAL on every dynamic table - never AUTO",
            "Deploy sql/03_monitoring.sql (VW_RELOAD_DETECTION in particular)",
            "Reconcile row counts on a schedule on both tracks",
        ],
        "open_asks": [
            "(a) Authoritative __OPERATION_TYPE contract - does a delete code exist?",
            "(b) Why __TIMESTAMP is TEXT with comma decimal separator, not a timestamp type",
            "(c) Whether change_tracking=ON is meaningful when streams are refused",
            "(d) Declared primary keys per catalog-linked object",
            "(e) Guidance on uninitialised objects in a share",
        ],
    },
]


# Diagram slides, spliced in after the slide they illustrate. Each diagram gets its
# own slide: the rest of the deck is one idea per slide, and a diagram shrunk to sit
# beside six bullets is readable from nowhere.
#
# Deliberately a subset. All seven diagrams appear in the white paper, where a
# reader can study them; a deck that stops to show every picture stops being a
# walkthrough. D4 (connector writes) and D7 (gaps) are the two left out.
DECK_FIGURES: list[tuple[str, str, str]] = [
    ("The challenge",
     "D1_problem.png", "The problem capture is asked to solve."),
    ("Why standard CDC is refused",
     "D2_capability.png", "What the platform permits on a catalog-linked share."),
    ("Two tracks, one engine",
     "D3_architecture.png", "Where capture sits between the SAP share and the Gold layer."),
    ("Track 1 - Custom data products",
     "D5_cost.png", "Scan cost: a watermark predicate against a full read."),
    ("Decision guide",
     "D6_decision.png", "Choosing a mechanism."),
]

for _after_title, _file, _caption in DECK_FIGURES:
    for _i, _slide_spec in enumerate(SLIDES):
        if _slide_spec.get("title") == _after_title:
            SLIDES.insert(_i + 1, {
                "type": "figure",
                "title": _after_title,
                "file": _file,
                "caption": _caption,
            })
            break
    else:
        raise SystemExit(
            f"DECK_FIGURES refers to a slide that does not exist: {_after_title!r}. "
            "Retitling a slide without updating this list would silently drop the "
            "diagram, so this is fatal rather than a warning."
        )

# ═══════════════════════════════════════════════════════════════════════════
# PDF infrastructure
# ═══════════════════════════════════════════════════════════════════════════

def _hex(c: str):
    from reportlab.lib.colors import HexColor
    return HexColor(c)


def _build_styles(body_size: float = 9.5):
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_LEFT, TA_JUSTIFY, TA_CENTER

    s = {}
    s["h1"] = ParagraphStyle(
        "h1", fontName="Helvetica-Bold", fontSize=13,
        textColor=_hex(NAVY), spaceBefore=18, spaceAfter=6, leading=18,
    )
    s["h2"] = ParagraphStyle(
        "h2", fontName="Helvetica-Bold", fontSize=11,
        textColor=_hex(BLUE), spaceBefore=10, spaceAfter=4, leading=15,
    )
    s["body"] = ParagraphStyle(
        "body", fontName="Helvetica", fontSize=body_size,
        textColor=_hex(TEXT), spaceBefore=0, spaceAfter=6,
        leading=body_size * 1.45, alignment=TA_JUSTIFY,
    )
    s["code"] = ParagraphStyle(
        "code", fontName="Courier", fontSize=8, textColor=_hex(TEXT),
        spaceBefore=2, spaceAfter=2, leading=11,
    )
    s["note"] = ParagraphStyle(
        "note", fontName="Helvetica-Oblique", fontSize=8.5,
        textColor=_hex(SLATE), spaceBefore=2, spaceAfter=4, leading=12,
    )
    s["warn"] = ParagraphStyle(
        "warn", fontName="Helvetica", fontSize=body_size,
        textColor=_hex(WARNFG), spaceBefore=0, spaceAfter=0,
        leading=body_size * 1.45, alignment=TA_JUSTIFY,
    )
    s["th"] = ParagraphStyle(
        "th", fontName="Helvetica-Bold", fontSize=8.5,
        textColor=_hex(WHITE), leading=12,
    )
    s["td"] = ParagraphStyle(
        "td", fontName="Helvetica", fontSize=8.5,
        textColor=_hex(TEXT), leading=12,
    )
    s["caption"] = ParagraphStyle(
        "caption", fontName="Helvetica-Oblique", fontSize=8,
        textColor=_hex(SLATE), spaceBefore=0, spaceAfter=0, leading=11,
        alignment=TA_CENTER,
    )
    return s


def _make_figure(spec, page_w, styles):
    """Embed one of the repo's diagrams, scaled to the text width.

    The diagrams in assets/diagrams are the same ones the README refers to, so the
    collateral and the repository show the reader the same pictures.

    A missing file is skipped rather than fatal. The generator has to stay runnable
    in a checkout where assets/ was not fetched, and a white paper with one diagram
    absent is more useful than a build that refuses to produce anything.
    """
    from reportlab.platypus import Image, Paragraph, Spacer, KeepTogether
    from reportlab.lib.units import mm
    from reportlab.lib.utils import ImageReader

    path = ASSETS_DIR / spec["file"]
    if not path.is_file():
        print(f"    note: diagram not found, skipping: {path.name}")
        return []

    iw, ih = ImageReader(str(path)).getSize()
    w = page_w * float(spec.get("width", 1.0))
    h = w * ih / iw
    # Cap the height so a tall diagram cannot crowd its own caption onto the next
    # page, which KeepTogether would otherwise resolve by pushing both.
    max_h = float(spec.get("max_h_mm", 100)) * mm
    if h > max_h:
        h, w = max_h, max_h * iw / ih

    img = Image(str(path), width=w, height=h)
    img.hAlign = "CENTER"
    group = [Spacer(1, 1 * mm), img]
    if spec.get("caption"):
        group += [Spacer(1, 1.5 * mm), Paragraph(spec["caption"], styles["caption"])]
    group.append(Spacer(1, 4 * mm))
    return [KeepTogether(group)]


def _make_table(headers, rows, col_frac, page_w, styles):
    """Build a reportlab Table from header/rows data."""
    from reportlab.platypus import Table, TableStyle
    from reportlab.lib.units import mm

    usable = page_w
    col_w = [f * usable for f in col_frac]

    from reportlab.platypus import Paragraph
    th = styles["th"]
    td = styles["td"]

    data = []
    # header row
    data.append([Paragraph(h, th) for h in headers])
    for row in rows:
        data.append([Paragraph(str(c), td) for c in row])

    n_cols = len(headers)
    tbl = Table(data, colWidths=col_w, repeatRows=1)

    row_commands = []
    for i in range(1, len(data)):
        bg = _hex(ALT) if i % 2 == 0 else _hex(WHITE)
        row_commands.append(("BACKGROUND", (0, i), (-1, i), bg))

    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), _hex(BLUE)),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [_hex(WHITE), _hex(ALT)]),
        ("TOPPADDING",    (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING",   (0, 0), (-1, -1), 5),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 5),
        ("GRID", (0, 0), (-1, -1), 0.4, _hex(RULE)),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    return tbl


def _make_code_block(text, page_w, styles):
    """Wrap code text in a shaded table cell."""
    from reportlab.platypus import Table, TableStyle, Paragraph
    para = Paragraph(text.replace("\n", "<br/>"), styles["code"])
    tbl = Table([[para]], colWidths=[page_w])
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), _hex(CODE_BG)),
        ("TOPPADDING",    (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING",   (0, 0), (-1, -1), 8),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 8),
        ("BOX", (0, 0), (-1, -1), 0.5, _hex(RULE)),
    ]))
    return tbl


def _make_warn_block(text, page_w, styles):
    """Amber warning box."""
    from reportlab.platypus import Table, TableStyle, Paragraph
    para = Paragraph(text, styles["warn"])
    tbl = Table([[para]], colWidths=[page_w])
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), _hex(WARNBG)),
        ("TOPPADDING",    (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ("LEFTPADDING",   (0, 0), (-1, -1), 9),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 9),
        ("BOX", (0, 0), (-1, -1), 1.0, _hex(WARNFG)),
    ]))
    return tbl


def _section_to_flowables(section, page_w, styles):
    """Convert a section dict into a list of reportlab flowables."""
    from reportlab.platypus import Paragraph, Spacer, KeepTogether
    from reportlab.lib.units import mm

    items = []

    level = section.get("level", 1)
    heading_style = styles["h1"] if level == 1 else styles["h2"]
    items.append(Paragraph(section["heading"], heading_style))
    items.append(Spacer(1, 2 * mm))

    for kind, content in section.get("body", []):
        if kind == "para":
            items.append(Paragraph(content, styles["body"]))
        elif kind == "h2":
            items.append(Spacer(1, 1 * mm))
            items.append(Paragraph(content, styles["h2"]))
        elif kind == "note":
            items.append(Paragraph(content, styles["note"]))
        elif kind == "warn":
            items.append(_make_warn_block(content, page_w, styles))
            items.append(Spacer(1, 4 * mm))
        elif kind == "code":
            items.append(_make_code_block(content, page_w, styles))
            items.append(Spacer(1, 3 * mm))
        elif kind == "figure":
            items.extend(_make_figure(content, page_w, styles))
        elif kind == "table":
            td = content
            tbl = _make_table(
                td["headers"], td["rows"], td["col_widths"], page_w, styles
            )
            items.append(tbl)
            items.append(Spacer(1, 3 * mm))
        else:
            pass  # unknown kind - skip

    items.append(Spacer(1, 3 * mm))
    return items


class _NumberedCanvas:
    """Canvas factory that adds page number footer."""

    def __init__(self, doc_title: str, is_cover_fn=None):
        self._doc_title = doc_title
        self._is_cover_fn = is_cover_fn or (lambda n: n == 1)
        # Return a class, not an instance, because reportlab calls the class
        # We use a closure instead
        raise RuntimeError("Use make_canvas_factory() instead")


def _make_canvas_factory(doc_title: str, skip_first: bool = True):
    """Return a canvas class (not instance) that draws footer on each page."""
    from reportlab.pdfgen import canvas as rl_canvas
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm

    class _FC(rl_canvas.Canvas):
        def __init__(self, filename, **kw):
            kw.setdefault("invariant", 1)   # fix creation date for determinism
            super().__init__(filename, **kw)
            self._pages = []

        def showPage(self):
            self._pages.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            count = len(self._pages)
            for i, state in enumerate(self._pages):
                self.__dict__.update(state)
                page_num = i + 1
                if not (skip_first and page_num == 1):
                    self._draw_footer(page_num, count)
                super().showPage()
            super().save()

        def _draw_footer(self, page_num, total):
            w, h = A4
            self.saveState()
            self.setFont("Helvetica", 7.5)
            self.setFillColor(_hex(MID))
            # left: doc title
            self.drawString(20 * mm, 10 * mm, doc_title)
            # right: page number
            txt = f"{page_num} / {total}"
            self.drawRightString(w - 20 * mm, 10 * mm, txt)
            # rule
            self.setStrokeColor(_hex(RULE))
            self.setLineWidth(0.4)
            self.line(20 * mm, 13 * mm, w - 20 * mm, 13 * mm)
            self.restoreState()

    return _FC


# ═══════════════════════════════════════════════════════════════════════════
# White Paper generator
# ═══════════════════════════════════════════════════════════════════════════

def gen_white_paper(out_path: Path) -> int:
    """Generate the dual-track technical reference. Returns page count."""
    return _gen_paper(
        out_path, WP_TITLE, WP_SUBTITLE, WP_SECTIONS,
        "This paper documents a dual-track pattern for incremental capture from "
        "SAP Business Data Cloud catalog-linked shares in Snowflake. The standard "
        "CDC mechanisms are refused; two alternative signals exist. Both tracks "
        "share one engine: dynamic tables with REFRESH_MODE = INCREMENTAL. "
        "A headline risk - silent metadata corruption in local copies - applies "
        "to both. All claims carry their measured evidence.",
    )


def gen_recommendation(out_path: Path) -> int:
    """Generate the CDC enablement recommendation paper. Returns page count."""
    return _gen_paper(
        out_path, REC_TITLE, REC_SUBTITLE, REC_SECTIONS,
        "Standard SAP BDC data products ship four connector-generated change "
        "columns. Custom data products expose only what their pipeline owner "
        "chose to. This paper treats the standard configuration as a "
        "specification, scores a well-configured custom product against it, and "
        "arrives at a recommendation that is not the obvious one: on the two "
        "properties that govern cost and correctness the custom track measured "
        "ahead, and the highest-value action requires nothing from SAP at all.",
    )


def _gen_paper(out_path: Path, title: str, subtitle: str, sections: list,
               abstract: str) -> int:
    """Render a cover page plus body sections to a deterministic PDF.

    Shared by both papers. The two differ only in title, abstract and content, so
    duplicating the page-template and cover machinery would mean two places to fix
    every layout bug.
    """
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        BaseDocTemplate, Frame, PageTemplate, Paragraph,
        Spacer, PageBreak, HRFlowable,
    )

    W, H = A4
    L, R, T, B = 22 * mm, 22 * mm, 22 * mm, 20 * mm

    styles = _build_styles(body_size=9.5)

    # ── page templates ────────────────────────────────────────────────────
    cover_frame = Frame(L, B, W - L - R, H - T - B, id="cover")
    body_frame  = Frame(L, B + 6 * mm, W - L - R, H - T - B - 6 * mm, id="body")

    def draw_cover(canvas, doc):
        canvas.saveState()
        # full-bleed navy header strip
        canvas.setFillColor(_hex(NAVY))
        canvas.rect(0, H - 55 * mm, W, 55 * mm, fill=1, stroke=0)
        # thin accent rule
        canvas.setFillColor(_hex(BLUE))
        canvas.rect(0, H - 57 * mm, W, 2 * mm, fill=1, stroke=0)
        canvas.restoreState()

    def draw_body(canvas, doc):
        pass  # footer drawn by numbered canvas factory

    cover_tmpl = PageTemplate(id="Cover", frames=[cover_frame], onPage=draw_cover)
    body_tmpl  = PageTemplate(id="Body",  frames=[body_frame],  onPage=draw_body)

    FC = _make_canvas_factory(title, skip_first=True)

    doc = BaseDocTemplate(
        str(out_path),
        pagesize=A4,
        pageTemplates=[cover_tmpl, body_tmpl],
        leftMargin=L, rightMargin=R, topMargin=T, bottomMargin=B,
    )
    # fix metadata for determinism
    doc.author   = "sap-bdc-cdc accelerator"
    doc.title    = title
    doc.subject  = subtitle
    doc.creator  = "generate_collateral.py"

    usable_w = W - L - R
    story = []

    # ── cover ─────────────────────────────────────────────────────────────
    story.append(Spacer(1, 40 * mm))  # push below header strip

    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_LEFT

    cover_title_style = ParagraphStyle(
        "ctitle", fontName="Helvetica-Bold", fontSize=22,
        textColor=_hex(WHITE), leading=30, spaceAfter=6,
    )
    cover_sub_style = ParagraphStyle(
        "csub", fontName="Helvetica", fontSize=13,
        textColor=_hex(LIGHT), leading=18, spaceAfter=4,
    )
    cover_meta_style = ParagraphStyle(
        "cmeta", fontName="Helvetica", fontSize=9,
        textColor=_hex(MID), leading=13,
    )

    story.append(Paragraph(title, cover_title_style))
    story.append(Paragraph(subtitle, cover_sub_style))
    story.append(Spacer(1, 6 * mm))
    story.append(HRFlowable(width="100%", thickness=0.5, color=_hex(RULE)))
    story.append(Spacer(1, 4 * mm))
    story.append(Paragraph(BUILD_DATE, cover_meta_style))
    story.append(Paragraph(
        "Vendor-neutral community pattern  |  Apache 2.0  |  "
        "All measurements from live SAP BDC tenants",
        cover_meta_style,
    ))
    story.append(Spacer(1, 8 * mm))

    abstract_style = ParagraphStyle(
        "abstract", fontName="Helvetica", fontSize=10,
        textColor=_hex(SLATE), leading=15, spaceAfter=6, alignment=TA_LEFT,
    )
    story.append(Paragraph(abstract, abstract_style))

    # ── body pages ────────────────────────────────────────────────────────
    from reportlab.platypus import NextPageTemplate
    story.append(NextPageTemplate("Body"))
    story.append(PageBreak())

    for section in sections:
        story.extend(_section_to_flowables(section, usable_w, styles))

    # build with numbered canvas
    doc.build(story, canvasmaker=FC)
    _normalize_pdf(out_path)

    try:
        import re
        data = out_path.read_bytes()
        pages = len(re.findall(rb"/Type\s*/Page\b", data))
    except Exception:
        pages = -1
    return pages


# ═══════════════════════════════════════════════════════════════════════════
# Executive Summary generator
# ═══════════════════════════════════════════════════════════════════════════

def gen_exec_summary(out_path: Path) -> int:
    """Generate the executive summary PDF (target: 2 pages). Returns page count."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        BaseDocTemplate, Frame, PageTemplate, Paragraph,
        Spacer, PageBreak, HRFlowable, NextPageTemplate,
    )
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_LEFT, TA_CENTER

    W, H = A4
    L, R, T, B = 22 * mm, 22 * mm, 20 * mm, 20 * mm

    styles = _build_styles(body_size=10.5)

    def draw_page(canvas, doc):
        # header bar
        canvas.saveState()
        canvas.setFillColor(_hex(NAVY))
        canvas.rect(0, H - 20 * mm, W, 20 * mm, fill=1, stroke=0)
        canvas.setFont("Helvetica-Bold", 10)
        canvas.setFillColor(_hex(WHITE))
        canvas.drawString(L, H - 13 * mm, ES_TITLE)
        canvas.setFont("Helvetica", 9)
        canvas.setFillColor(_hex(LIGHT))
        canvas.drawRightString(W - R, H - 13 * mm, ES_SUBTITLE)
        canvas.restoreState()

    frame = Frame(L, B + 6 * mm, W - L - R, H - 20 * mm - B - 6 * mm, id="body")
    tmpl  = PageTemplate(id="Main", frames=[frame], onPage=draw_page)
    FC    = _make_canvas_factory(ES_TITLE, skip_first=False)

    doc = BaseDocTemplate(
        str(out_path), pagesize=A4,
        pageTemplates=[tmpl],
        leftMargin=L, rightMargin=R, topMargin=T, bottomMargin=B,
    )
    doc.title   = ES_TITLE
    doc.author  = "sap-bdc-cdc accelerator"
    doc.creator = "generate_collateral.py"

    usable_w = W - L - R
    story    = []

    h1 = ParagraphStyle(
        "es_h1", fontName="Helvetica-Bold", fontSize=13,
        textColor=_hex(NAVY), spaceBefore=12, spaceAfter=4, leading=17,
    )

    for section in ES_SECTIONS:
        story.append(Paragraph(section["heading"], h1))
        story.append(HRFlowable(width="100%", thickness=0.6, color=_hex(BLUE), spaceAfter=4))
        for kind, content in section["body"]:
            if kind == "para":
                story.append(Paragraph(content, styles["body"]))
            elif kind == "warn":
                story.append(_make_warn_block(content, usable_w, styles))
                story.append(Spacer(1, 3 * mm))
            elif kind == "table":
                td = content
                tbl = _make_table(
                    td["headers"], td["rows"], td["col_widths"], usable_w, styles
                )
                story.append(tbl)
                story.append(Spacer(1, 3 * mm))
        story.append(Spacer(1, 2 * mm))

    doc.build(story, canvasmaker=FC)
    _normalize_pdf(out_path)

    try:
        import re
        data = out_path.read_bytes()
        pages = len(re.findall(rb"/Type\s*/Page\b", data))
    except Exception:
        pages = -1
    return pages


# ═══════════════════════════════════════════════════════════════════════════
# Deck generator
# ═══════════════════════════════════════════════════════════════════════════

def _normalize_pdf(path: Path) -> None:
    """Replace variable PDF metadata (timestamps, file ID) with fixed values.
    Every substitution is the same byte length as the matched pattern, so
    byte offsets in the xref table remain valid."""
    import re
    data = path.read_bytes()
    # Fix creation / modification timestamps  (23 bytes each)
    fixed_ts = b"D:20260821000000+00'00'"
    data = re.sub(rb"D:\d{14}[+-]\d{2}'\d{2}'", fixed_ts, data)
    # Fix the file identifier in the trailer (32-hex MD5 pair, 76 bytes total)
    fixed_id = b"0" * 32
    data = re.sub(
        rb"/ID\s*\n?\[<[0-9a-fA-F]{32}><[0-9a-fA-F]{32}>\]",
        b"/ID \n[<" + fixed_id + b"><" + fixed_id + b">]",
        data,
    )
    path.write_bytes(data)


def _normalize_pptx(path: Path) -> None:
    """Re-write PPTX with fixed ZIP entry timestamps for bit-level determinism."""
    import zipfile, io
    buf = path.read_bytes()
    out = io.BytesIO()
    FIXED = (2026, 8, 21, 0, 0, 0)
    with zipfile.ZipFile(io.BytesIO(buf), "r") as zin:
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
            for item in sorted(zin.infolist(), key=lambda x: x.filename):
                data = zin.read(item.filename)
                info = zipfile.ZipInfo(item.filename, date_time=FIXED)
                info.compress_type = zipfile.ZIP_DEFLATED
                zout.writestr(info, data)
    path.write_bytes(out.getvalue())


def _rgb(hex_color: str):
    from pptx.dml.color import RGBColor
    h = hex_color.lstrip("#")
    return RGBColor(int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _add_rect(slide, left, top, width, height, fill_hex=None, line_hex=None):
    from pptx.util import Emu
    shape = slide.shapes.add_shape(
        __import__("pptx.enum.shapes", fromlist=["MSO_SHAPE_TYPE"]).MSO_SHAPE_TYPE.AUTO_SHAPE
        if False else 1,  # MSO_AUTO_SHAPE_TYPE.RECTANGLE = 1
        left, top, width, height,
    )
    if fill_hex:
        shape.fill.solid()
        shape.fill.fore_color.rgb = _rgb(fill_hex)
    else:
        shape.fill.background()
    if line_hex:
        shape.line.color.rgb = _rgb(line_hex)
        shape.line.width = __import__("pptx.util", fromlist=["Pt"]).Pt(0.75)
    else:
        shape.line.fill.background()
    return shape


def _add_text(slide, text, left, top, width, height,
              font_name="Calibri", font_size=18, bold=False,
              italic=False, color_hex=TEXT, align="left",
              word_wrap=True, v_anchor="top"):
    from pptx.util import Pt, Emu
    from pptx.enum.text import PP_ALIGN, MSO_ANCHOR

    txb = slide.shapes.add_textbox(left, top, width, height)
    tf  = txb.text_frame
    tf.word_wrap = word_wrap

    anchor_map = {"top": MSO_ANCHOR.TOP, "middle": MSO_ANCHOR.MIDDLE,
                  "bottom": MSO_ANCHOR.BOTTOM}
    tf.vertical_anchor = anchor_map.get(v_anchor, MSO_ANCHOR.TOP)

    align_map = {"left": PP_ALIGN.LEFT, "center": PP_ALIGN.CENTER,
                 "right": PP_ALIGN.RIGHT, "justify": PP_ALIGN.JUSTIFY}

    lines = text.split("\n")
    for i, line in enumerate(lines):
        para = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        para.alignment = align_map.get(align, PP_ALIGN.LEFT)
        run = para.add_run()
        run.text = line
        run.font.name  = font_name
        run.font.size  = Pt(font_size)
        run.font.bold  = bold
        run.font.color.rgb = _rgb(color_hex)
        if italic:
            run.font.italic = italic
    return txb


def _add_bullet_para(tf, text, font_name, font_size, color_hex,
                     indent_level=0, bold=False):
    from pptx.util import Pt
    from pptx.enum.text import PP_ALIGN
    from pptx.oxml.ns import qn
    import lxml.etree as etree

    para = tf.add_paragraph()
    para.alignment = PP_ALIGN.LEFT
    para.level = indent_level

    run = para.add_run()
    run.text = text
    run.font.name  = font_name
    run.font.size  = Pt(font_size)
    run.font.bold  = bold
    run.font.color.rgb = _rgb(color_hex)

    # set bullet character via pPr
    pPr = para._pPr
    if pPr is None:
        pPr = para._p.get_or_add_pPr()
    buNone = etree.SubElement(pPr, qn("a:buChar"))
    buNone.set("char", "-")
    buFont = etree.SubElement(pPr, qn("a:buFont"))
    buFont.set("typeface", font_name)
    # indent
    pPr.set("indent", "-228600")
    pPr.set("marL", str(228600 + indent_level * 228600))

    return para


def gen_deck(out_path: Path, slides: list | None = None) -> int:
    """Render a slide list to a deterministic PPTX. Returns slide count.

    Defaults to the solution overview. Passing a different list reuses the same
    slide renderers, which is why the executive deck needs no second builder.
    """
    from pptx import Presentation
    from pptx.util import Inches, Pt, Emu
    from pptx.enum.text import PP_ALIGN, MSO_ANCHOR

    prs = Presentation()
    prs.slide_width  = Inches(13.33)
    prs.slide_height = Inches(7.5)

    W = prs.slide_width
    H = prs.slide_height
    blank_layout = prs.slide_layouts[6]  # blank

    def _slide():
        return prs.slides.add_slide(blank_layout)

    def _title_bar(slide, title_text, light_bg=False):
        """Navy top bar with white title text."""
        bar_h = Inches(1.15)
        _add_rect(slide, 0, 0, W, bar_h, fill_hex=NAVY)
        _add_text(
            slide, title_text,
            Inches(0.45), Inches(0.20),
            W - Inches(0.9), bar_h - Inches(0.1),
            font_name="Calibri", font_size=24, bold=True,
            color_hex=WHITE, align="left", v_anchor="middle",
        )
        if light_bg:
            _add_rect(slide, 0, bar_h, W, H - bar_h, fill_hex=WHITE)

    def _footer(slide, text):
        _add_text(
            slide, text,
            Inches(0.45), H - Inches(0.35),
            W - Inches(0.9), Inches(0.3),
            font_name="Calibri", font_size=8,
            color_hex=MID, align="left",
        )

    # ── slide builders ────────────────────────────────────────────────────

    for s in (slides if slides is not None else SLIDES):
        stype = s["type"]

        if stype == "title":
            slide = _slide()
            _add_rect(slide, 0, 0, W, H, fill_hex=NAVY)
            # accent stripe
            _add_rect(slide, 0, Inches(4.2), W, Inches(0.06), fill_hex=BLUE)
            _add_text(
                slide, s["title"],
                Inches(0.9), Inches(1.5),
                W - Inches(1.8), Inches(2.5),
                font_name="Calibri", font_size=36, bold=True,
                color_hex=WHITE, align="left", v_anchor="bottom",
            )
            _add_text(
                slide, s["subtitle"],
                Inches(0.9), Inches(4.4),
                W - Inches(1.8), Inches(1.8),
                font_name="Calibri", font_size=14,
                color_hex=LIGHT, align="left",
            )

        elif stype in ("bullets", "bullets_detail"):
            slide = _slide()
            _add_rect(slide, 0, 0, W, H, fill_hex=WHITE)
            _title_bar(slide, s["title"])

            y_start = Inches(1.3)
            body_h  = H - Inches(1.7)

            if stype == "bullets_detail" and s.get("intro"):
                txb = slide.shapes.add_textbox(
                    Inches(0.5), y_start, W - Inches(1.0), Inches(0.9),
                )
                tf = txb.text_frame
                tf.word_wrap = True
                para = tf.paragraphs[0]
                para.alignment = PP_ALIGN.LEFT
                run = para.add_run()
                run.text = s["intro"]
                run.font.name  = "Calibri"
                run.font.size  = Pt(11)
                run.font.italic = True
                run.font.color.rgb = _rgb(SLATE)
                y_start = Inches(2.2)

            # bullets
            txb = slide.shapes.add_textbox(
                Inches(0.6), y_start,
                W - Inches(1.2), H - y_start - Inches(0.5),
            )
            tf = txb.text_frame
            tf.word_wrap = True
            first = True
            for b in s["bullets"]:
                if first:
                    para = tf.paragraphs[0]
                    first = False
                else:
                    para = tf.add_paragraph()
                para.space_before = Pt(3)
                para.alignment = PP_ALIGN.LEFT
                run = para.add_run()
                run.text = "- " + b
                run.font.name  = "Calibri"
                run.font.size  = Pt(13)
                run.font.color.rgb = _rgb(TEXT)

            if stype == "bullets_detail" and s.get("caveat"):
                _add_rect(slide, Inches(0.4), H - Inches(0.9),
                          W - Inches(0.8), Inches(0.55), fill_hex=WARNBG)
                _add_text(
                    slide, "Note: " + s["caveat"],
                    Inches(0.5), H - Inches(0.88),
                    W - Inches(1.0), Inches(0.52),
                    font_name="Calibri", font_size=10, italic=True,
                    color_hex=WARNFG, align="left", v_anchor="middle",
                )

            _footer(slide, "sap-bdc-cdc | community pattern | Apache 2.0")

        elif stype == "risk":
            slide = _slide()
            _add_rect(slide, 0, 0, W, H, fill_hex=WHITE)
            # red-amber title bar
            bar_h = Inches(1.15)
            _add_rect(slide, 0, 0, W, bar_h, fill_hex="#7B341E")
            _add_text(
                slide, s["title"],
                Inches(0.45), Inches(0.20),
                W - Inches(0.9), bar_h - Inches(0.1),
                font_name="Calibri", font_size=24, bold=True,
                color_hex=WHITE, align="left", v_anchor="middle",
            )
            # risk statement box
            _add_rect(slide, Inches(0.4), Inches(1.25),
                      W - Inches(0.8), Inches(0.75), fill_hex=WARNBG)
            _add_text(
                slide, s["risk_statement"],
                Inches(0.55), Inches(1.28),
                W - Inches(1.1), Inches(0.7),
                font_name="Calibri", font_size=13, bold=True,
                color_hex=WARNFG, align="left", v_anchor="middle",
            )
            # bullets
            txb = slide.shapes.add_textbox(
                Inches(0.6), Inches(2.15), W - Inches(1.2), Inches(3.2),
            )
            tf = txb.text_frame
            tf.word_wrap = True
            first = True
            for b in s["bullets"]:
                if first:
                    para = tf.paragraphs[0]
                    first = False
                else:
                    para = tf.add_paragraph()
                para.space_before = Pt(3)
                run = para.add_run()
                run.text = "- " + b
                run.font.name  = "Calibri"
                run.font.size  = Pt(12)
                run.font.color.rgb = _rgb(TEXT)
            # rule box
            _add_rect(slide, Inches(0.4), H - Inches(0.9),
                      W - Inches(0.8), Inches(0.55), fill_hex=LIGHT)
            _add_text(
                slide, s["rule"],
                Inches(0.55), H - Inches(0.88),
                W - Inches(1.1), Inches(0.52),
                font_name="Calibri", font_size=11, bold=True,
                color_hex=NAVY, align="left", v_anchor="middle",
            )

        elif stype == "figure":
            # A diagram gets its own slide rather than being squeezed alongside
            # bullets. One idea per slide is the rule for the rest of the deck and
            # a shrunk-to-fit diagram beside six lines of text is readable from
            # nowhere.
            slide = _slide()
            _add_rect(slide, 0, 0, W, H, fill_hex=WHITE)
            _title_bar(slide, s["title"])

            path = ASSETS_DIR / s["file"]
            if path.is_file():
                avail_w = W - Inches(1.2)
                avail_h = H - Inches(2.2)     # title bar above, caption below
                pic = slide.shapes.add_picture(
                    str(path), Inches(0.6), Inches(1.35), width=avail_w)
                if pic.height > avail_h:
                    # add_picture preserved the aspect ratio, so take it from the
                    # shape rather than re-reading the file.
                    ratio = pic.width / pic.height
                    pic.height = int(avail_h)
                    pic.width = int(avail_h * ratio)
                pic.left = int((W - pic.width) / 2)
                if s.get("caption"):
                    _add_text(
                        slide, s["caption"],
                        Inches(0.6), H - Inches(0.78),
                        W - Inches(1.2), Inches(0.5),
                        font_name="Calibri", font_size=10, italic=True,
                        color_hex=SLATE, align="center", v_anchor="middle",
                    )
            else:
                print(f"    note: diagram not found, slide left blank: {path.name}")

        elif stype == "table":
            slide = _slide()
            _add_rect(slide, 0, 0, W, H, fill_hex=WHITE)
            _title_bar(slide, s["title"])

            headers = s["headers"]
            rows    = s["rows"]
            col_frac = s["col_widths"]
            usable  = W - Inches(0.9)
            col_px  = [f * usable for f in col_frac]

            y = Inches(1.35)
            row_h = Inches(0.48)

            def _cell(slide, text, lft, tp, wd, ht, bg, fg, bold_=False):
                _add_rect(slide, lft, tp, wd, ht, fill_hex=bg, line_hex=RULE)
                _add_text(
                    slide, text,
                    lft + Inches(0.08), tp + Inches(0.04),
                    wd - Inches(0.16), ht - Inches(0.06),
                    font_name="Calibri", font_size=11, bold=bold_,
                    color_hex=fg, align="left", v_anchor="middle",
                )

            # header row
            x = Inches(0.45)
            for ci, (hdr, cw) in enumerate(zip(headers, col_px)):
                _cell(slide, hdr, x, y, cw, row_h, BLUE, WHITE, bold_=True)
                x += cw
            y += row_h

            for ri, row in enumerate(rows):
                bg = ALT if ri % 2 == 1 else WHITE
                x = Inches(0.45)
                for ci, (cell, cw) in enumerate(zip(row, col_px)):
                    _cell(slide, str(cell), x, y, cw, row_h, bg, TEXT)
                    x += cw
                y += row_h

            _footer(slide, "sap-bdc-cdc | community pattern | Apache 2.0")

        elif stype == "closing":
            slide = _slide()
            _add_rect(slide, 0, 0, W, H, fill_hex=WHITE)
            _title_bar(slide, s["title"])

            # left column: next steps
            _add_text(
                slide, "Next steps",
                Inches(0.5), Inches(1.3), Inches(5.5), Inches(0.4),
                font_name="Calibri", font_size=14, bold=True, color_hex=NAVY,
            )
            txb = slide.shapes.add_textbox(
                Inches(0.6), Inches(1.75), Inches(5.4), Inches(4.5),
            )
            tf = txb.text_frame
            tf.word_wrap = True
            first = True
            for i, item in enumerate(s["next_steps"], 1):
                para = tf.paragraphs[0] if first else tf.add_paragraph()
                first = False
                para.space_before = Pt(4)
                run = para.add_run()
                run.text = f"{i}.  {item}"
                run.font.name  = "Calibri"
                run.font.size  = Pt(12)
                run.font.color.rgb = _rgb(TEXT)

            # divider
            _add_rect(slide, Inches(6.4), Inches(1.3),
                      Inches(0.03), Inches(5.5), fill_hex=RULE)

            # right column: open asks
            _add_text(
                slide, "Open asks to SAP",
                Inches(6.6), Inches(1.3), Inches(6.3), Inches(0.4),
                font_name="Calibri", font_size=14, bold=True, color_hex=NAVY,
            )
            txb2 = slide.shapes.add_textbox(
                Inches(6.7), Inches(1.75), Inches(6.2), Inches(4.5),
            )
            tf2 = txb2.text_frame
            tf2.word_wrap = True
            first = True
            for item in s["open_asks"]:
                para = tf2.paragraphs[0] if first else tf2.add_paragraph()
                first = False
                para.space_before = Pt(4)
                run = para.add_run()
                run.text = "- " + item
                run.font.name  = "Calibri"
                run.font.size  = Pt(12)
                run.font.italic = True
                run.font.color.rgb = _rgb(SLATE)

        # end of slide loop

    prs.save(str(out_path))
    # fix PPTX core-properties timestamps and ZIP entry dates for determinism
    from datetime import datetime
    _normalize_pptx(out_path)
    return len(prs.slides)


# ═══════════════════════════════════════════════════════════════════════════
# main
# ═══════════════════════════════════════════════════════════════════════════

def main(argv=None):
    import importlib

    # dependency check
    missing = []
    for pkg in ("reportlab", "pptx"):
        try:
            importlib.import_module(pkg)
        except ImportError:
            missing.append(pkg if pkg != "pptx" else "python-pptx")
    if missing:
        print(f"ERROR: missing packages: {', '.join(missing)}")
        print("Install with: pip3 install --user " + " ".join(missing))
        return 1

    args = (argv or sys.argv)[1:]
    if not args:
        args = ["all"]
    if "all" in args:
        args = ["white-paper", "exec-summary", "deck", "recommendation",
                "exec-deck"]

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    for target in args:
        if target not in FILENAMES:
            print(f"Unknown target: {target}. "
                  f"Choose from: {', '.join(FILENAMES)} or all")
            continue

        out = OUT_DIR / FILENAMES[target]
        print(f"Generating {out.name} ...", end=" ", flush=True)

        if target == "white-paper":
            n = gen_white_paper(out)
            label = f"{n} pages"
        elif target == "exec-summary":
            n = gen_exec_summary(out)
            label = f"{n} pages"
        elif target == "deck":
            n = gen_deck(out)
            label = f"{n} slides"
        elif target == "recommendation":
            n = gen_recommendation(out)
            label = f"{n} pages"
        elif target == "exec-deck":
            n = gen_deck(out, EXEC_SLIDES)
            label = f"{n} slides"

        size_kb = out.stat().st_size // 1024
        print(f"done  ({label}, {size_kb} KB)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
