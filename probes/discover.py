"""Discovery.

Finds the SAP BDC catalog-linked databases in whatever account it is pointed at,
without any hardcoded names. The marker is the database kind that BDC Connect sets,
which is stable across tenants and naming conventions:

    SHOW DATABASES  ->  kind = 'CATALOG-LINKED DATABASE'

Two behaviours of these objects shape everything downstream and are worth knowing
before reading the code:

  * INFORMATION_SCHEMA.COLUMNS returns no rows for a catalog-linked database, so the
    column list can only be obtained with DESCRIBE TABLE, one statement per object.
  * Schema names contain a colon and object names are mixed case, so every reference
    must be quoted.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from conn import Session, fqn


@dataclass
class Column:
    name: str
    type: str

    @property
    def is_varchar(self) -> bool:
        return self.type.upper().startswith(("VARCHAR", "STRING", "TEXT"))

    @property
    def is_numeric(self) -> bool:
        return self.type.upper().startswith(("NUMBER", "FLOAT", "DOUBLE", "DECIMAL", "INT"))

    @property
    def is_temporal(self) -> bool:
        return self.type.upper().startswith(("DATE", "TIMESTAMP", "TIME"))


@dataclass
class Obj:
    database: str
    schema: str
    name: str
    columns: list[Column] = field(default_factory=list)
    rows: int | None = None

    @property
    def fqn(self) -> str:
        return fqn(self.database, self.schema, self.name)

    def column(self, name: str) -> Column | None:
        low = name.lower()
        return next((c for c in self.columns if c.name.lower() == low), None)

    def find(self, *candidates: str) -> Column | None:
        """First column matching any candidate name, case-insensitively."""
        for cand in candidates:
            c = self.column(cand)
            if c:
                return c
        return None


# Candidate names for the replication write timestamp. SAP BDC pipelines built with
# SLT commonly add a customer extension field; the name varies by implementation, so
# several spellings are tried before falling back to shape-based detection.
WATERMARK_CANDIDATES = (
    "ZZLASTCHGDATE", "ZZ_LAST_CHG_DATE", "ZZLASTCHANGEDATE", "ZZUPDATED_AT",
    "ZZLOADTS", "ZZ_LOAD_TS", "_LOAD_TS", "LAST_REPLICATED_AT", "REPLICATION_TS",
)


def catalog_linked_databases(s: Session) -> list[str]:
    """Databases that BDC Connect created, found by kind rather than by name."""
    out = []
    for r in s.rows("SHOW DATABASES"):
        kind = str(r.get("kind") or r.get("KIND") or "").upper()
        if "CATALOG-LINKED" in kind or "CATALOG LINKED" in kind:
            out.append(str(r.get("name") or r.get("NAME")))
    return sorted(out)


def objects_in(s: Session, database: str) -> list[Obj]:
    out: list[Obj] = []
    for r in s.rows(f"SHOW TABLES IN DATABASE {fqn(database)}"):
        out.append(Obj(database=database,
                       schema=str(r.get("schema_name") or r.get("SCHEMA_NAME")),
                       name=str(r.get("name") or r.get("NAME"))))
    return out


def describe(s: Session, o: Obj) -> Obj:
    """Populate the column list. DESCRIBE is the only route on these objects."""
    o.columns = [Column(name=str(r.get("name")), type=str(r.get("type")))
                 for r in s.rows(f"DESCRIBE TABLE {o.fqn}")]
    return o


def find_watermark(o: Obj) -> Column | None:
    """The replication write timestamp, if the share carries one.

    Preference order: a known extension-field name, then any TIMESTAMP column whose
    name suggests load or change rather than a business event. A business date such as
    PostingDate is deliberately never returned - it is not a watermark, and using one
    as though it were is the most common and most expensive mistake in this pattern.
    """
    c = o.find(*WATERMARK_CANDIDATES)
    if c and c.is_temporal:
        return c
    if c:
        return c   # present but not typed as a timestamp; the caller should warn
    hints = ("lastchg", "lastchange", "loadts", "load_ts", "replicat", "extract",
             "ingest", "_ts", "updated")
    cands = [x for x in o.columns
             if x.is_temporal and any(h in x.name.lower() for h in hints)]
    return cands[0] if cands else None


def inventory(s: Session, describe_all: bool = True) -> list[Obj]:
    """Every object in every catalog-linked database, optionally with columns."""
    objs: list[Obj] = []
    for db in catalog_linked_databases(s):
        for o in objects_in(s, db):
            objs.append(describe(s, o) if describe_all else o)
    return objs
