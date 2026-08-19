#!/usr/bin/env python3
"""CDC readiness assessment for an SAP BDC catalog-linked share.

Point this at any Snowflake account that has SAP BDC data products linked into it and
it answers one question: can change data capture be built on this share, and by which
mechanism.

    python3 cdc_readiness.py --connection my_account
    python3 cdc_readiness.py --connection my_account --with-scratch-tests
    python3 cdc_readiness.py --connection my_account --report readiness.md

Read-only by default. `--with-scratch-tests` additionally creates a stream and a
dynamic table in a scratch database to prove the mechanisms work end to end, and drops
them; use it when you want proof rather than inference.

The five questions it answers, in the order they matter:

  1  Is there a change signal at all?          a replication timestamp on each object
  2  Does the connector merge, or reload?      if it reloads, no watermark can work
  3  Does a change predicate prune?            if not, an incremental cycle costs a full scan
  4  Which native mechanisms are permitted?    stream, CHANGES, change tracking, time travel
  5  Can a dynamic table refresh incrementally? the recommended engine
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone

from conn import Session, fqn, q
from discover import Obj, catalog_linked_databases, find_watermark, inventory

SCRATCH = "CDC_READINESS_SCRATCH"


class Assessment:
    def __init__(self, s: Session, verbose: bool = True):
        self.s = s
        self.v = verbose
        self.findings: list[dict] = []
        self.started = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # ------------------------------------------------------------------ plumbing
    def log(self, msg: str = "") -> None:
        if self.v:
            print(msg, flush=True)

    def record(self, key: str, status: str, detail: str, evidence=None) -> dict:
        f = {"check": key, "status": status, "detail": detail, "evidence": evidence}
        self.findings.append(f)
        mark = {"PASS": "  ok  ", "FAIL": " FAIL ", "WARN": " warn ", "INFO": "  ..  "}[status]
        self.log(f"[{mark}] {key}: {detail}")
        return f

    def status_of(self, key: str) -> str | None:
        return next((f["status"] for f in self.findings if f["check"] == key), None)

    # ---------------------------------------------------------------- 1 discovery
    def discover(self) -> list[Obj]:
        dbs = catalog_linked_databases(self.s)
        if not dbs:
            self.record("share.present", "FAIL",
                        "No catalog-linked databases found. Either BDC Connect has not "
                        "linked anything into this account, or the current role cannot "
                        "see it.")
            return []
        self.record("share.present", "PASS",
                    f"{len(dbs)} catalog-linked database(s): {', '.join(dbs)}",
                    evidence={"databases": dbs})
        objs = inventory(self.s)
        cols = sum(len(o.columns) for o in objs)
        self.record("share.inventory", "INFO",
                    f"{len(objs)} objects, {cols} columns",
                    evidence={"objects": [f"{o.database}.{o.schema}.{o.name}" for o in objs]})
        return objs

    # -------------------------------------------------------- 2 the change signal
    def change_signal(self, objs: list[Obj]) -> dict[str, str]:
        found: dict[str, str] = {}
        missing: list[str] = []
        for o in objs:
            wm = find_watermark(o)
            if wm:
                found[f"{o.database}.{o.schema}.{o.name}"] = wm.name
            else:
                missing.append(o.name)
        if not found:
            self.record("signal.watermark", "FAIL",
                        "No replication timestamp found on any object. Without one, a "
                        "watermark engine is impossible and only dynamic tables remain "
                        "viable. Ask the BDC pipeline owner to add a load timestamp.",
                        evidence={"objects_without": missing})
        elif missing:
            self.record("signal.watermark", "WARN",
                        f"Replication timestamp on {len(found)} of {len(objs)} objects. "
                        f"The remainder must rely on dynamic tables.",
                        evidence={"found": found, "objects_without": missing})
        else:
            names = sorted(set(found.values()))
            self.record("signal.watermark", "PASS",
                        f"Replication timestamp present on all {len(objs)} objects "
                        f"(column: {', '.join(names)})",
                        evidence={"found": found})
        return found

    # ------------------------------------------- 3 does the connector merge or reload
    def merge_or_reload(self, objs: list[Obj], wms: dict[str, str]) -> None:
        """The decisive test.

        A connector that reloads stamps every row with the latest write time, so a
        watermark cannot separate changed from unchanged. A connector that merges leaves
        historical rows with their original stamp. The signature is the number of
        distinct write days: one means a single load so far or a reload; several with an
        uneven row distribution means a merge.
        """
        target = self._largest_with_watermark(objs, wms)
        if not target:
            self.record("signal.merge_or_reload", "WARN",
                        "Skipped: no object with both a watermark and rows.")
            return
        o, wm = target
        rows = self.s.rows(f"""
SELECT TO_CHAR({q(wm)}, 'YYYY-MM-DD') AS WRITE_DAY, COUNT(*) AS ROWS_WRITTEN
FROM {o.fqn} GROUP BY 1 ORDER BY 1""")
        rows = [r for r in rows if r.get("WRITE_DAY")]
        if not rows:
            self.record("signal.merge_or_reload", "WARN",
                        f"{o.name}: watermark column is empty on every row.")
            return
        total = sum(r["ROWS_WRITTEN"] for r in rows)
        biggest = max(r["ROWS_WRITTEN"] for r in rows)
        concentration = biggest / total if total else 1.0
        ev = {"object": o.name, "write_days": rows, "days": len(rows),
              "largest_day_share": round(concentration, 6)}
        if len(rows) == 1:
            self.record("signal.merge_or_reload", "WARN",
                        f"{o.name}: all {total:,} rows carry a single write day. Either the "
                        "share was loaded once and has not changed, or the connector "
                        "reloads. Re-run after the next refresh to tell these apart.",
                        evidence=ev)
        elif concentration > 0.98:
            self.record("signal.merge_or_reload", "PASS",
                        f"{o.name}: {len(rows)} write days, {concentration:.4%} of rows on "
                        "the initial load and small deltas since. The connector merges, so "
                        "a watermark is viable.", evidence=ev)
        else:
            self.record("signal.merge_or_reload", "WARN",
                        f"{o.name}: {len(rows)} write days but rows are spread evenly "
                        f"(largest day {concentration:.1%}). Confirm this is genuine change "
                        "volume and not a periodic reload.", evidence=ev)

    # ------------------------------------------------------------------ 4 pruning
    def pruning(self, objs: list[Obj], wms: dict[str, str]) -> None:
        """Whether a watermark predicate reads only the changed data.

        Both queries project a data column deliberately. COUNT(*) alone can be answered
        from Iceberg manifest statistics without reading data, which flatters the result
        and hides the real extraction cost.
        """
        target = self._largest_with_watermark(objs, wms)
        if not target:
            self.record("cost.pruning", "WARN", "Skipped: no suitable object.")
            return
        o, wm = target
        candidates = [c.name for c in o.columns
                      if c.is_varchar and c.name.lower() != wm.lower()][:6]
        if not candidates:
            self.record("cost.pruning", "WARN", f"{o.name}: no varchar column to project.")
            return

        # SUM(LENGTH(col)) rather than COUNT(*) or COUNT(DISTINCT col): both of those can
        # be answered from Iceberg manifest statistics without reading data. Even with a
        # computed expression, a single-valued column still scans zero bytes - which looks
        # like a broken measurement but is correct - so try several columns and keep the
        # first that forces a genuine read. Otherwise the comparison has no baseline.
        self.s.execute("ALTER SESSION SET USE_CACHED_RESULT = FALSE")
        # Query history needs a real database as context: a catalog-linked database has no
        # usable INFORMATION_SCHEMA, so reading history from inside one returns nothing.
        self.s.execute("USE DATABASE SNOWFLAKE")
        probe_col, full, tried = None, {}, []
        try:
            for cand in candidates:
                self.s.rows(f"SELECT SUM(LENGTH({q(cand)})) AS N FROM {o.fqn}")
                st = self.s.last_query_bytes()
                tried.append({"column": cand,
                              "mb": round(float(st.get("BYTES_SCANNED") or 0) / 1048576, 2)})
                if float(st.get("BYTES_SCANNED") or 0) > 0:
                    probe_col, full = cand, st
                    break
            if probe_col is None:
                self.record("cost.pruning", "WARN",
                            "No probe column produced a measurable full scan - every candidate "
                            "was answerable from Iceberg statistics alone. Pruning cannot be "
                            "compared. This is not a fault in the share.",
                            evidence={"object": o.name, "columns_tried": tried})
                return
            self.s.rows(f"""SELECT SUM(LENGTH({q(probe_col)})) AS N FROM {o.fqn}
WHERE {q(wm)} > DATEADD('hour', -36, CURRENT_TIMESTAMP())""")
            delta = self.s.last_query_bytes()
        finally:
            self.s.execute("ALTER SESSION UNSET USE_CACHED_RESULT")

        fb = float(full.get("BYTES_SCANNED") or 0)
        db_ = float(delta.get("BYTES_SCANNED") or 0)
        ratio = (db_ / fb) if fb else None
        ev = {"object": o.name, "watermark": wm, "projected_column": probe_col,
              "columns_tried": tried,
              "full_scan_mb": round(fb / 1048576, 2),
              "watermark_scan_mb": round(db_ / 1048576, 2),
              "ratio": round(ratio, 6) if ratio is not None else None}
        if fb == 0:
            self.record("cost.pruning", "WARN",
                        "Could not measure a full scan; bytes scanned reported zero.",
                        evidence=ev)
        elif ratio is not None and ratio < 0.02:
            self.record("cost.pruning", "PASS",
                        f"Watermark predicate scans {db_/1048576:,.2f} MB against "
                        f"{fb/1048576:,.2f} MB for a full read - {ratio:.4%}. Near-complete "
                        "file elimination.", evidence=ev)
        elif ratio is not None and ratio < 0.35:
            self.record("cost.pruning", "WARN",
                        f"Watermark predicate scans {ratio:.1%} of a full read. Pruning "
                        "works but is partial; incremental cycles will not be free.",
                        evidence=ev)
        else:
            self.record("cost.pruning", "FAIL",
                        f"Watermark predicate scans {ratio:.1%} of a full read. The column "
                        "is not correlated with file layout, so it cannot prune. Prefer "
                        "dynamic tables, whose refresh does not depend on this.",
                        evidence=ev)

    # -------------------------------------------------------------- 5 capability
    def capability(self, objs: list[Obj]) -> None:
        o = self._largest(objs)
        if not o:
            return
        # Streams. A refusal naming INSERT_ONLY is the expected and useful outcome.
        ok, err = self.s.try_execute(
            f"CREATE OR REPLACE STREAM {SCRATCH}.PROBE.S_CAP ON TABLE {o.fqn}")
        if ok:
            self.record("capability.stream_standard", "PASS",
                        "A standard stream was accepted, which implies this is not an "
                        "externally catalogued Iceberg table. Unusual for BDC; verify.")
        elif err and "INSERT_ONLY" in err.upper():
            self.record("capability.stream_standard", "INFO",
                        "Standard stream refused; INSERT_ONLY required. Expected for an "
                        "Iceberg table on an external catalog.", evidence={"error": err})
        else:
            self.record("capability.stream_standard", "INFO",
                        "Standard stream refused.", evidence={"error": err})

        ok, err = self.s.try_execute(f"""SELECT COUNT(*) AS N FROM {o.fqn}
CHANGES(INFORMATION => DEFAULT) AT(TIMESTAMP => DATEADD('hour', -1, CURRENT_TIMESTAMP()))""")
        self.record("capability.changes_clause", "PASS" if ok else "INFO",
                    "CHANGES clause available." if ok else
                    "CHANGES clause unsupported, as expected on an external catalog.",
                    evidence={"error": err})

        ok, err = self.s.try_execute(f"SELECT METADATA$ACTION FROM {o.fqn} LIMIT 1")
        self.record("capability.stream_metadata", "PASS" if ok else "INFO",
                    "Stream metadata columns resolve." if ok else
                    "Stream metadata columns do not resolve; change tracking is not active.",
                    evidence={"error": err})

        ok, err = self.s.try_execute(f"SELECT METADATA$FILENAME AS F FROM {o.fqn} LIMIT 1")
        self.record("capability.file_metadata", "PASS" if ok else "WARN",
                    "METADATA$FILENAME available, so file-level change detection is possible."
                    if ok else "METADATA$FILENAME unavailable.", evidence={"error": err})

        # Time travel reach, bisected coarsely.
        reach = None
        for hours in (1, 6, 24, 48, 168):
            ok, _ = self.s.try_execute(
                f"SELECT COUNT(*) AS N FROM {o.fqn} AT(OFFSET => -{hours * 3600})")
            if ok:
                reach = hours
            else:
                break
        self.record("capability.time_travel", "INFO",
                    f"Time travel reaches about {reach} hour(s)." if reach else
                    "Time travel unavailable.",
                    evidence={"reach_hours": reach})

    # ---------------------------------------------- 6 dynamic table incrementality
    def dynamic_table(self, objs: list[Obj], wms: dict[str, str]) -> None:
        """The recommended engine. Requested explicitly as INCREMENTAL so Snowflake
        must honour it or refuse - AUTO would silently downgrade to FULL and hide the
        answer."""
        target = self._largest_with_watermark(objs, wms)
        o, wm = target if target else (self._largest(objs), None)
        if not o:
            return
        cols = [c.name for c in o.columns][:6]
        proj = ", ".join(f"{q(c)} AS {_safe(c)}" for c in cols)
        wh = self.s.one("SELECT CURRENT_WAREHOUSE() AS W").get("W")
        if not wh:
            self.record("capability.dynamic_table", "WARN",
                        "No current warehouse, so a dynamic table cannot be tested.")
            return
        dt = f"{SCRATCH}.PROBE.DT_CAP"
        ok, err = self.s.try_execute(f"""CREATE OR REPLACE DYNAMIC TABLE {dt}
  TARGET_LAG = '1 hour' WAREHOUSE = {wh} REFRESH_MODE = INCREMENTAL
AS SELECT {proj} FROM {o.fqn}""")
        if not ok:
            self.record("capability.dynamic_table", "FAIL",
                        "A dynamic table over the share was refused with REFRESH_MODE = "
                        "INCREMENTAL. The watermark path becomes the only option.",
                        evidence={"error": err})
            return
        import time
        time.sleep(3)
        self.s.try_execute(f"ALTER DYNAMIC TABLE {dt} REFRESH")
        time.sleep(5)
        hist = self.s.rows(f"""
SELECT STATE, REFRESH_ACTION, REFRESH_TRIGGER,
       DATEDIFF('millisecond', REFRESH_START_TIME, REFRESH_END_TIME) AS DURATION_MS
FROM TABLE({fqn(SCRATCH)}.INFORMATION_SCHEMA.DYNAMIC_TABLE_REFRESH_HISTORY(
       NAME => '{dt}')) ORDER BY REFRESH_START_TIME""")
        actions = [h.get("REFRESH_ACTION") for h in hist]
        no_data = [h for h in hist if h.get("REFRESH_ACTION") == "NO_DATA"]
        ev = {"object": o.name, "refresh_history": hist}
        if no_data:
            ms = no_data[0].get("DURATION_MS")
            self.record("capability.dynamic_table", "PASS",
                        f"Incremental refresh accepted, and a no-change refresh returned "
                        f"NO_DATA in {ms:.0f} ms - Snowflake established that nothing "
                        "changed without recomputing the source.", evidence=ev)
        elif "INCREMENTAL" in actions:
            self.record("capability.dynamic_table", "WARN",
                        "Incremental refresh accepted, but a second refresh did not report "
                        "NO_DATA. Re-check after a source change.", evidence=ev)
        else:
            self.record("capability.dynamic_table", "WARN",
                        f"Refresh actions seen: {actions}.", evidence=ev)

    # ------------------------------------------------------------------- verdict
    def verdict(self) -> dict:
        wm = self.status_of("signal.watermark")
        merge = self.status_of("signal.merge_or_reload")
        prune = self.status_of("cost.pruning")
        dt = self.status_of("capability.dynamic_table")
        present = self.status_of("share.present")

        if present != "PASS":
            v, why = "NOT ASSESSABLE", ["No catalog-linked share is visible to this role."]
        elif dt == "PASS":
            v = "READY"
            why = ["Dynamic tables refresh incrementally over the share and detect "
                   "no-change cheaply. This is the recommended engine and needs no code."]
            if wm == "PASS" and prune == "PASS":
                why.append("A pruning watermark is also available for the exception path - "
                           "non-deterministic transformations and external consumers.")
            elif wm != "PASS":
                why.append("No usable watermark, so the exception path is unavailable. "
                           "Everything must be expressible as a dynamic table.")
        elif dt == "FAIL" and wm == "PASS" and prune in ("PASS", "WARN"):
            v = "READY WITH CAVEATS"
            why = ["Dynamic tables were refused, so capture must be built on the "
                   "watermark with a MERGE per object.",
                   "That path cannot detect physical deletes, because the share carries no "
                   "change-mode column. Reconcile row counts on a schedule."]
        elif wm == "FAIL" and dt != "PASS":
            v = "NOT READY"
            why = ["No replication timestamp and no incremental dynamic table. There is no "
                   "change signal to build on.",
                   "Ask the BDC pipeline owner to add a load timestamp to each object."]
        elif dt == "INFO":
            v = "READY WITH CAVEATS"
            why = ["The change signal is present and usable, but the recommended mechanism was "
                   "not proven: re-run with --with-scratch-tests to confirm that a dynamic "
                   "table refreshes incrementally over this share rather than inferring it."]
            if prune == "PASS":
                why.append("The watermark prunes, so the exception path is available regardless.")
            elif prune == "WARN":
                why.append("Pruning could not be measured cleanly; see the finding below.")
        else:
            v = "READY WITH CAVEATS"
            why = ["Some checks were inconclusive. Read the findings below and re-run "
                   "after the next connector refresh."]

        if merge == "WARN":
            why.append("The merge-versus-reload test was inconclusive on a single write "
                       "day. Re-run after the next refresh; if the connector reloads, the "
                       "watermark path becomes as expensive as a full rebuild.")
        return {"verdict": v, "reasons": why}

    # ------------------------------------------------------------------- helpers
    def _largest(self, objs: list[Obj]) -> Obj | None:
        with_rows = [o for o in objs if o.rows]
        if with_rows:
            return max(with_rows, key=lambda o: o.rows or 0)
        return max(objs, key=lambda o: len(o.columns)) if objs else None

    def _largest_with_watermark(self, objs, wms) -> tuple[Obj, str] | None:
        cands = [(o, wms.get(f"{o.database}.{o.schema}.{o.name}")) for o in objs]
        cands = [(o, w) for o, w in cands if w]
        if not cands:
            return None
        return max(cands, key=lambda t: (t[0].rows or 0, len(t[0].columns)))

    def count_rows(self, objs: list[Obj]) -> None:
        for o in objs:
            try:
                o.rows = int(self.s.one(f"SELECT COUNT(*) AS N FROM {o.fqn}")["N"])
            except Exception:
                o.rows = None


def _safe(name: str) -> str:
    out = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in name).upper()
    return out if out and not out[0].isdigit() else f"C_{out}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--connection", help="named entry in ~/.snowflake/connections.toml")
    ap.add_argument("--with-scratch-tests", action="store_true",
                    help="also create a stream and a dynamic table in a scratch database "
                         "to prove the mechanisms, then drop them")
    ap.add_argument("--report", help="write a markdown report to this path")
    ap.add_argument("--json", help="write the raw findings to this path")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()

    s = Session(a.connection)
    asm = Assessment(s, verbose=not a.quiet)

    asm.log("SAP BDC change data capture readiness")
    asm.log("=" * 72)
    objs = asm.discover()
    if not objs:
        _emit(asm, a)
        return 2

    asm.count_rows(objs)
    wms = asm.change_signal(objs)
    asm.merge_or_reload(objs, wms)
    asm.pruning(objs, wms)

    if a.with_scratch_tests:
        s.execute(f"CREATE DATABASE IF NOT EXISTS {SCRATCH}")
        s.execute(f"CREATE SCHEMA IF NOT EXISTS {SCRATCH}.PROBE")
        try:
            asm.capability(objs)
            asm.dynamic_table(objs, wms)
        finally:
            for stmt in (f"DROP DYNAMIC TABLE IF EXISTS {SCRATCH}.PROBE.DT_CAP",
                         f"DROP STREAM IF EXISTS {SCRATCH}.PROBE.S_CAP",
                         "USE DATABASE SNOWFLAKE",
                         f"DROP DATABASE IF EXISTS {SCRATCH}"):
                s.try_execute(stmt)
            asm.log("\nscratch objects dropped")
    else:
        asm.record("capability.dynamic_table", "INFO",
                   "Not tested. Re-run with --with-scratch-tests to prove the recommended "
                   "mechanism rather than infer it.")

    _emit(asm, a)
    return 0 if asm.verdict()["verdict"].startswith("READY") else 1


def _emit(asm: Assessment, a) -> None:
    v = asm.verdict()
    asm.log("\n" + "=" * 72)
    asm.log(f"VERDICT: {v['verdict']}")
    for r in v["reasons"]:
        asm.log(f"  - {r}")
    payload = {"generated_at": asm.started, "verdict": v, "findings": asm.findings}
    if a.json:
        with open(a.json, "w") as fh:
            json.dump(payload, fh, indent=1, default=str)
        asm.log(f"\nfindings -> {a.json}")
    if a.report:
        with open(a.report, "w") as fh:
            fh.write(_markdown(payload))
        asm.log(f"report   -> {a.report}")


def _markdown(p: dict) -> str:
    v = p["verdict"]
    out = ["# SAP BDC change data capture readiness", "",
           f"**Verdict: {v['verdict']}**", ""]
    out += [f"- {r}" for r in v["reasons"]]
    out += ["", f"Assessed {p['generated_at']}.", "", "## Findings", "",
            "| Check | Status | Detail |", "| --- | --- | --- |"]
    for f in p["findings"]:
        out.append(f"| `{f['check']}` | {f['status']} | {f['detail']} |")
    out += ["", "## What to do next", "",
            "If the verdict is READY, configure your Gold layer dynamic tables with",
            "`REFRESH_MODE = INCREMENTAL` stated explicitly and set `TARGET_LAG` per object",
            "class. See `docs/04-operating-it.md` and `sql/`.", "",
            "Whatever the verdict, deploy `sql/04_monitoring.sql`. It detects the single",
            "event that invalidates the whole pattern: the connector switching from",
            "incremental merge to full reload.", ""]
    return "\n".join(out)


if __name__ == "__main__":
    sys.exit(main())
