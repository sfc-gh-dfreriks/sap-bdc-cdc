# Change Data Capture from SAP BDC catalog-linked shares

Incremental capture from SAP Business Data Cloud data products shared into Snowflake as
catalog-linked (zero-copy) databases — without copying the share, and without building an
engine.

**The short version:** it works, and the mechanism is a platform feature rather than
something you write. Dynamic tables refresh incrementally over a catalog-linked share and
detect no-change in a few hundred milliseconds. A replication watermark is available for the
cases dynamic tables cannot serve. Streams, `CHANGES()` and change tracking are all refused,
for one reason that explains most of this repository: the objects are Iceberg tables on an
external catalog.

![Where capture sits](assets/diagrams/D3_architecture.png)

---

## Is this you?

You have one or more SAP BDC data products linked into a Snowflake account, and:

- consumers complain the data is hours or a day behind
- your Gold layer re-reads the whole share on a schedule because nothing tells it what changed
- you are about to write a custom CDC framework, and want to know whether you need to

If so, start with the readiness assessment. It takes a minute and answers the question for
*your* tenant rather than in general.

```bash
cd probes
pip install -r requirements.txt
python3 cdc_readiness.py --connection <your_connection> --report readiness.md
```

Add `--with-scratch-tests` to prove the mechanisms rather than infer them. That creates a
stream and a dynamic table in a scratch database and drops them; everything else is read-only.

Sample output:

```
[  ok  ] share.present: 4 catalog-linked database(s)
[  ..  ] share.inventory: 23 objects, 1825 columns
[  ok  ] signal.watermark: Replication timestamp present on all 23 objects (column: ZZLASTCHGDATE)
[  ok  ] signal.merge_or_reload: 3 write days, 99.9997% of rows on the initial load
         and small deltas since. The connector merges, so a watermark is viable.
[  ok  ] cost.pruning: Watermark predicate scans 0.00 MB against 7,035.80 MB for a
         full read - 0.0000%. Near-complete file elimination.
[  ok  ] capability.dynamic_table: Incremental refresh accepted, and a no-change refresh
         returned NO_DATA in 348 ms

VERDICT: READY
```

---

## Why this is not obvious

A catalog-linked database reports `kind = TABLE` and `is_external = N` on the objects inside
it, which suggests ordinary tables and ordinary CDC options. They are not ordinary tables, and
Snowflake's own error messages are the clearest evidence:

| You try | Snowflake says |
| --- | --- |
| `CREATE STREAM ... ON TABLE <share>` | *Streams on External Tables or Iceberg tables with an external catalog must have INSERT_ONLY set to true.* |
| `SELECT ... CHANGES(INFORMATION => DEFAULT)` | *CHANGES clause is not supported on external tables or Iceberg tables with an external catalog.* |
| `ALTER TABLE ... SET CHANGE_TRACKING = TRUE` | *change_tracking cannot be set for Iceberg tables unless they use Snowflake catalog.* |

![What the platform permits](assets/diagrams/D2_capability.png)

So the usual first three answers are all unavailable. What remains is better than the
workaround most teams reach for.

---

## The finding that makes it work

The BDC connector **merges**; it does not reload. Historical rows keep their original
replication timestamp while new data lands in new Parquet files. That is the precondition for
any incremental design, and it is worth verifying on your own tenant because it is a property
of the pipeline rather than a guarantee.

![Connector write pattern](assets/diagrams/D4_connector_writes.png)

Because the connector writes that timestamp in monotonic batches, per-file Iceberg statistics
eliminate every file that cannot contain a match. On a reference tenant, a watermark predicate
returned the changed rows while scanning **0 MB** of a 7 GB object. The business-date predicate
most teams reach for first scanned **4.7 GB** for the same answer, because business dates are
scattered across every file.

![Pruning](assets/diagrams/D5_cost.png)

---

## Choosing a mechanism

![Decision](assets/diagrams/D6_decision.png)

|  | Dynamic table | Watermark + MERGE | INSERT_ONLY stream |
| --- | --- | --- | --- |
| Inserts | yes | yes | yes |
| Updates | yes | yes | yes |
| **Deletes** | **yes** — diffs snapshots | **no** — no change-mode column | **no** — by definition |
| Cheap no-change detection | yes, `NO_DATA` | yes, 0 MB scanned | yes |
| Code required | none | task + control table + MERGE | task + MERGE |
| Can feed outside Snowflake | no | yes | yes |
| Goes stale unread | no | no | yes, 14 days |
| Blocked by non-deterministic SQL | yes | no | no |

**Use dynamic tables.** They are the only mechanism that captures deletes, and they need no
code. Reserve the watermark for the two cases they cannot serve: a transformation containing a
non-deterministic expression such as `CURRENT_DATE()`, and a target outside Snowflake.

---

## Deploy it

```
sql/00_discover.sql                  find the share, the watermark, and prove it prunes
sql/01_capture_control.sql           control schema: watermark state, refresh audit, baseline
sql/02_dynamic_table_engine.sql      the engine — four verified query shapes
sql/03_monitoring.sql                reload detection, exceptions, alert   ← deploy this
sql/04_watermark_exception_path.sql  the exception path, with delete reconciliation
```

Two configuration choices carry all the weight:

1. **State `REFRESH_MODE = INCREMENTAL` explicitly.** `AUTO` silently falls back to `FULL`
   when it cannot support incremental, with no error and nothing in the object's behaviour to
   point at — just a larger bill and a longer lag. Stating it converts a silent cost regression
   into a deployment-time error.
2. **Set `TARGET_LAG` per object class.** Transactional objects change continuously; master
   data dimensions in a typical share may not have been written for days.

Then deploy `03_monitoring.sql` whatever else you do. The pattern rests on the connector
merging, and if that ever changes, incremental refresh stays *correct* while becoming as
expensive as a full rebuild. It will not raise an error. `VW_RELOAD_DETECTION` is what tells
you.

---

## What capture does not solve

![Gaps](assets/diagrams/D7_gaps.png)

Capture makes data current. It does not make it correct, and it cannot create fields that were
never shared. This distinction is worth making explicitly to stakeholders, because the two
complaints usually arrive together and only one of them is a platform problem:

- *"the data is a day behind"* — solvable, and this repository is how
- *"the ledger has no amounts"* / *"there is no customer master"* — not solvable at any
  refresh frequency. Delivering fifteen-minute freshness on a ledger that carries no amount
  columns answers the wrong question convincingly.

See [docs/gaps-and-asks.md](docs/gaps-and-asks.md) for the full list, including the platform
gaps worth raising with Snowflake product management.

---

## Documentation

| Document | Contents |
| --- | --- |
| [docs/how-it-works.md](docs/how-it-works.md) | The mechanism, the evidence, and why the alternatives are refused |
| [docs/deployment.md](docs/deployment.md) | Step-by-step, with the checks to run at each stage |
| [docs/gaps-and-asks.md](docs/gaps-and-asks.md) | Limitations, open questions, and product asks |

## Repository layout

```
probes/       readiness assessment — tenant-agnostic, discovers the share by kind
sql/          deployable SQL, in the order you run it
docs/         the reasoning behind the SQL
assets/       diagrams, reusable in your own decks
examples/     sample assessment output from a reference tenant
```

## Provenance and caveats

Every claim here was measured against a live SAP BDC share on a Snowflake account, over a
five-day observation window, using the probes in `probes/`. Where a measurement is from that
reference tenant it is labelled as such — row counts and megabytes will differ on yours, but
the *shape* is what generalises.

Two things were not observable in that window and are stated as open rather than settled:

1. What a dynamic table refresh does when the source has **genuinely changed**. Initial refresh
   was `INCREMENTAL` and a no-change refresh was `NO_DATA`; a changed-source refresh was not
   seen. Read `VW_REFRESH_AUDIT` after your next connector write to confirm.
2. Whether an `INSERT_ONLY` stream yields rows on a source change. It was created successfully
   and correctly returned zero rows at creation. This only matters if you adopt the stream
   path, which is not the recommendation.

Neither affects the recommendation: if a changed-source refresh were to fall back to `FULL`,
the fallback is the watermark path, which is specified in `sql/04` and whose cost is measured.

This is a community pattern, not a Snowflake product commitment. Verify on your own tenant
before relying on it.

## Licence

Apache 2.0. See [LICENSE](LICENSE).
