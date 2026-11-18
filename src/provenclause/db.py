"""M2 database: standard-library sqlite3, synchronous (exception to F4, decision record 3.5).

One connection, used from the event loop's thread. connect() opens it in
autocommit mode: each statement commits by itself. Statements that must commit
together run inside transaction(). Every function takes the connection as its
first argument; this module keeps no global connection.
"""

import json
import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel

from provenclause.models import (
    Checklist,
    Contract,
    Finding,
    ItemStatus,
    Outcome,
    ReviewConfig,
    ReviewStatus,
    Span,
)

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS contracts (
  contract_id TEXT PRIMARY KEY,
  source      TEXT NOT NULL,
  title       TEXT NOT NULL,
  text        TEXT NOT NULL,
  n_spans     INTEGER NOT NULL,
  created_at  TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS spans (
  contract_id   TEXT NOT NULL REFERENCES contracts(contract_id),
  span_id       INTEGER NOT NULL,
  char_start    INTEGER NOT NULL,
  char_end      INTEGER NOT NULL,
  text          TEXT NOT NULL,
  section       TEXT,
  is_definition INTEGER NOT NULL,
  defines       TEXT NOT NULL,
  refs          TEXT NOT NULL,
  PRIMARY KEY (contract_id, span_id)
) STRICT;

CREATE TABLE IF NOT EXISTS reviews (
  review_id    TEXT PRIMARY KEY,
  contract_id  TEXT NOT NULL REFERENCES contracts(contract_id),
  checklist_id TEXT NOT NULL,
  model_config TEXT NOT NULL,
  status       TEXT NOT NULL,
  created_at   TEXT NOT NULL,
  updated_at   TEXT NOT NULL,
  error        TEXT
) STRICT;

CREATE TABLE IF NOT EXISTS review_items (
  review_id  TEXT NOT NULL REFERENCES reviews(review_id),
  item_id    TEXT NOT NULL,
  status     TEXT NOT NULL,
  attempt    INTEGER NOT NULL,
  retries    INTEGER NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (review_id, item_id)
) STRICT;

CREATE TABLE IF NOT EXISTS findings (
  finding_id      INTEGER PRIMARY KEY,
  operation_id    TEXT NOT NULL UNIQUE,
  review_id       TEXT NOT NULL REFERENCES reviews(review_id),
  item_id         TEXT NOT NULL,
  attempt         INTEGER NOT NULL,
  label           TEXT NOT NULL,
  span_ids        TEXT NOT NULL,
  rationale       TEXT NOT NULL,
  reason          TEXT,
  self_confidence INTEGER,
  checks          TEXT NOT NULL,
  support_score   INTEGER,
  outcome         TEXT,
  created_at      TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS events (
  review_id TEXT NOT NULL REFERENCES reviews(review_id),
  seq       INTEGER NOT NULL,
  ts        TEXT NOT NULL,
  type      TEXT NOT NULL,
  item_id   TEXT,
  payload   TEXT NOT NULL,
  PRIMARY KEY (review_id, seq)
) STRICT;
"""


class ReviewRow(BaseModel):
    review_id: str
    contract_id: str
    checklist_id: str
    config: ReviewConfig  # column model_config; that name is reserved in Pydantic
    status: ReviewStatus
    created_at: str
    updated_at: str
    error: str | None = None


class ItemRow(BaseModel):
    review_id: str
    item_id: str
    status: ItemStatus
    attempt: int  # starts at 1
    retries: int  # starts at 0; +1 only on a retry after a failed check
    updated_at: str


class StoredFinding(BaseModel):
    finding_id: int
    operation_id: str
    review_id: str
    attempt: int
    created_at: str
    finding: Finding


class EventRow(BaseModel):
    review_id: str
    seq: int
    ts: str
    type: str
    item_id: str | None = None
    payload: dict[str, Any]


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def connect(path: str | Path) -> sqlite3.Connection:
    """Open the database, create the tables, and check the schema version."""
    conn = sqlite3.connect(path, autocommit=True)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        if str(path) != ":memory:":
            conn.execute("PRAGMA journal_mode = WAL")
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, SCHEMA_VERSION):  # checked before CREATE, so a newer file is not touched
            raise RuntimeError(f"{path}: schema version {version}, expected {SCHEMA_VERSION}")
        conn.executescript(SCHEMA)
        if version == 0:
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    except Exception:
        conn.close()
        raise
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """BEGIN IMMEDIATE ... COMMIT; ROLLBACK and re-raise on error.

    In autocommit mode conn.commit() does nothing, so COMMIT and ROLLBACK are
    SQL statements. Inside a transaction that is already open, this joins it:
    the outer transaction() commits or rolls back.
    """
    if conn.in_transaction:
        yield conn
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


# --- contracts -----------------------------------------------------------------


def save_contract(conn: sqlite3.Connection, contract: Contract) -> None:
    """Write the contract and all its spans. Same id and same text again: no change."""
    with transaction(conn):
        row = conn.execute(
            "SELECT text FROM contracts WHERE contract_id = ?", (contract.contract_id,)
        ).fetchone()
        if row is not None:
            if row["text"] != contract.text:
                raise ValueError(f"contract {contract.contract_id} is already stored with other text")
            return
        conn.execute(
            "INSERT INTO contracts VALUES (?, ?, ?, ?, ?, ?)",
            (contract.contract_id, contract.source, contract.title, contract.text,
             len(contract.spans), now()),
        )
        conn.executemany(
            "INSERT INTO spans VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (contract.contract_id, s.span_id, s.start, s.end, s.text, s.section,
                 int(s.is_definition), _json(s.defines), _json(s.refs))
                for s in contract.spans
            ],
        )


def load_contract(conn: sqlite3.Connection, contract_id: str) -> Contract:
    row = conn.execute("SELECT * FROM contracts WHERE contract_id = ?", (contract_id,)).fetchone()
    if row is None:
        raise KeyError(f"contract {contract_id} not found")
    spans = [
        Span(
            span_id=s["span_id"], start=s["char_start"], end=s["char_end"], text=s["text"],
            section=s["section"], is_definition=bool(s["is_definition"]),
            refs=json.loads(s["refs"]), defines=json.loads(s["defines"]),
        )
        for s in conn.execute(
            "SELECT * FROM spans WHERE contract_id = ? ORDER BY span_id", (contract_id,)
        )
    ]
    return Contract(
        contract_id=row["contract_id"], source=row["source"], title=row["title"],
        text=row["text"], spans=spans,
    )


# --- reviews and items -----------------------------------------------------------


def create_review(
    conn: sqlite3.Connection, contract_id: str, checklist: Checklist, config: ReviewConfig
) -> str:
    """Write the review ("created"), one "pending" item per checklist item, and 1 event."""
    review_id = uuid4().hex
    ts = now()
    with transaction(conn):
        conn.execute(
            "INSERT INTO reviews VALUES (?, ?, ?, ?, 'created', ?, ?, NULL)",
            (review_id, contract_id, checklist.checklist_id, _json(config.model_dump()), ts, ts),
        )
        conn.executemany(
            "INSERT INTO review_items VALUES (?, ?, 'pending', 1, 0, ?)",
            [(review_id, item.id, ts) for item in checklist.items],
        )
        add_event(conn, review_id, "review_created", {
            "contract_id": contract_id, "checklist_id": checklist.checklist_id,
            "config_name": config.name,
        })
    return review_id


def get_review(conn: sqlite3.Connection, review_id: str) -> ReviewRow:
    row = conn.execute("SELECT * FROM reviews WHERE review_id = ?", (review_id,)).fetchone()
    if row is None:
        raise KeyError(f"review {review_id} not found")
    data = dict(row)
    data["config"] = json.loads(data.pop("model_config"))
    return ReviewRow.model_validate(data)


def list_reviews(conn: sqlite3.Connection, statuses: Iterable[ReviewStatus]) -> list[ReviewRow]:
    """Reviews whose status is one of statuses, oldest first. M6 uses it to find unfinished reviews."""
    wanted = list(statuses)
    if not wanted:
        return []
    marks = ", ".join("?" * len(wanted))
    rows = conn.execute(
        f"SELECT review_id FROM reviews WHERE status IN ({marks}) ORDER BY created_at, rowid", wanted
    ).fetchall()
    return [get_review(conn, r["review_id"]) for r in rows]


def get_items(conn: sqlite3.Connection, review_id: str) -> list[ItemRow]:
    """Items in checklist order (the order create_review() wrote them)."""
    rows = conn.execute(
        "SELECT * FROM review_items WHERE review_id = ? ORDER BY rowid", (review_id,)
    ).fetchall()
    return [ItemRow.model_validate(dict(r)) for r in rows]


def get_item(conn: sqlite3.Connection, review_id: str, item_id: str) -> ItemRow:
    row = conn.execute(
        "SELECT * FROM review_items WHERE review_id = ? AND item_id = ?", (review_id, item_id)
    ).fetchone()
    if row is None:
        raise KeyError(f"item {item_id} of review {review_id} not found")
    return ItemRow.model_validate(dict(row))


# --- findings --------------------------------------------------------------------


def _to_stored(row: sqlite3.Row) -> StoredFinding:
    finding = Finding(
        item_id=row["item_id"], label=row["label"], span_ids=json.loads(row["span_ids"]),
        rationale=row["rationale"], reason=row["reason"], self_confidence=row["self_confidence"],
        outcome=row["outcome"], support_score=row["support_score"], checks=json.loads(row["checks"]),
    )
    return StoredFinding(
        finding_id=row["finding_id"], operation_id=row["operation_id"], review_id=row["review_id"],
        attempt=row["attempt"], created_at=row["created_at"], finding=finding,
    )


def insert_finding(
    conn: sqlite3.Connection, operation_id: str, review_id: str, attempt: int, finding: Finding
) -> bool:
    """INSERT ... ON CONFLICT(operation_id) DO NOTHING. True when a new row was written."""
    cur = conn.execute(
        "INSERT INTO findings (operation_id, review_id, item_id, attempt, label, span_ids, rationale,"
        " reason, self_confidence, checks, support_score, outcome, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
        " ON CONFLICT(operation_id) DO NOTHING",
        (operation_id, review_id, finding.item_id, attempt, finding.label, _json(finding.span_ids),
         finding.rationale, finding.reason, finding.self_confidence, _json(finding.checks),
         finding.support_score, finding.outcome, now()),
    )
    return cur.rowcount == 1


def set_finding_outcome(
    conn: sqlite3.Connection, operation_id: str, outcome: Outcome, *,
    checks: dict[str, str] | None = None, support_score: int | None = None,
) -> None:
    """Set the outcome of a recorded finding; checks and support_score only when given.

    A1 sets "verified" right after recording; stage 4's verifier also sets checks
    and support_score. KeyError when the finding is missing.
    """
    sets, values = ["outcome = ?"], [outcome]
    if checks is not None:
        sets.append("checks = ?")
        values.append(_json(checks))
    if support_score is not None:
        sets.append("support_score = ?")
        values.append(support_score)
    cur = conn.execute(
        f"UPDATE findings SET {', '.join(sets)} WHERE operation_id = ?", (*values, operation_id)
    )
    if cur.rowcount == 0:
        raise KeyError(f"finding {operation_id} not found")


def get_finding(conn: sqlite3.Connection, operation_id: str) -> StoredFinding:
    row = conn.execute("SELECT * FROM findings WHERE operation_id = ?", (operation_id,)).fetchone()
    if row is None:
        raise KeyError(f"finding {operation_id} not found")
    return _to_stored(row)


def list_findings(
    conn: sqlite3.Connection, review_id: str, item_id: str | None = None
) -> list[StoredFinding]:
    """Findings in the order they were written."""
    if item_id is None:
        rows = conn.execute(
            "SELECT * FROM findings WHERE review_id = ? ORDER BY finding_id", (review_id,)
        )
    else:
        rows = conn.execute(
            "SELECT * FROM findings WHERE review_id = ? AND item_id = ? ORDER BY finding_id",
            (review_id, item_id),
        )
    return [_to_stored(r) for r in rows]


# --- events ----------------------------------------------------------------------


def add_event(
    conn: sqlite3.Connection, review_id: str, type: str, payload: dict[str, Any],
    item_id: str | None = None,
) -> int:
    """Append an event with seq = the review's largest seq + 1, and return seq.

    It opens no transaction; the caller puts it inside one. All calls run in one
    thread and are synchronous, so two calls never compute the same seq.
    """
    # fetchall(), not fetchone(): the statement must run to its end, or in
    # autocommit mode SQLite keeps the write open.
    (row,) = conn.execute(
        "INSERT INTO events (review_id, seq, ts, type, item_id, payload)"
        " SELECT ?, COALESCE(MAX(seq), 0) + 1, ?, ?, ?, ? FROM events WHERE review_id = ?"
        " RETURNING seq",
        (review_id, now(), type, item_id, _json(payload), review_id),
    ).fetchall()
    return row["seq"]


def list_events(conn: sqlite3.Connection, review_id: str) -> list[EventRow]:
    rows = conn.execute("SELECT * FROM events WHERE review_id = ? ORDER BY seq", (review_id,))
    return [EventRow.model_validate({**dict(r), "payload": json.loads(r["payload"])}) for r in rows]
