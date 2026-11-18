"""M3 state machine: two hand-written transition tables and compare-and-set updates.

A transition first checks the table. It then runs UPDATE ... WHERE status = :from_
and writes one event, both in one transaction. When the UPDATE changes 0 rows,
the status in the database is not from_; nothing is written.
"""

import sqlite3

from provenclause import db
from provenclause.db import ItemRow
from provenclause.models import ItemStatus, ReviewStatus

REVIEW_TRANSITIONS: dict[ReviewStatus, frozenset[ReviewStatus]] = {
    "created":   frozenset({"parsing", "failed"}),
    "parsing":   frozenset({"reviewing", "failed"}),
    "reviewing": frozenset({"completed", "failed"}),
    "completed": frozenset(),
    "failed":    frozenset(),
}

ITEM_TRANSITIONS: dict[ItemStatus, frozenset[ItemStatus]] = {
    "pending":   frozenset({"drafting"}),
    # abstained: over budget or refusal before a finding is recorded; pending: M6 recovery
    "drafting":  frozenset({"verifying", "abstained", "pending"}),
    # drafting: retry after a failed check; pending: M6 recovery
    "verifying": frozenset({"verified", "violated", "abstained", "drafting", "pending"}),
    "verified":  frozenset(),
    "violated":  frozenset(),
    "abstained": frozenset(),
}

# (from, to) -> (attempt +, retries +). A new attempt gives the next finding a new operation_id.
_COUNTERS: dict[tuple[ItemStatus, ItemStatus], tuple[int, int]] = {
    ("verifying", "drafting"): (1, 1),  # retry; "at most 2 retries" is checked by the caller
    ("drafting", "pending"): (1, 0),  # recovery does not count as a retry
    ("verifying", "pending"): (1, 0),
}


class InvalidTransition(ValueError):
    pass


def transition_review(
    conn: sqlite3.Connection, review_id: str, from_: ReviewStatus, to: ReviewStatus,
    *, error: str | None = None,
) -> None:
    if to not in REVIEW_TRANSITIONS[from_]:
        raise InvalidTransition(f"review {review_id}: {from_} -> {to} is not allowed")
    if error is not None and to != "failed":
        raise ValueError(f"review {review_id}: error is only allowed for -> failed, not -> {to}")

    with db.transaction(conn):
        if to == "failed":
            cur = conn.execute(
                "UPDATE reviews SET status = ?, updated_at = ?, error = ?"
                " WHERE review_id = ? AND status = ?",
                (to, db.now(), error, review_id, from_),
            )
        else:
            cur = conn.execute(
                "UPDATE reviews SET status = ?, updated_at = ? WHERE review_id = ? AND status = ?",
                (to, db.now(), review_id, from_),
            )
        if cur.rowcount == 0:
            actual = db.get_review(conn, review_id).status  # KeyError when the review is missing
            raise InvalidTransition(f"review {review_id}: expected {from_}, actual {actual}")
        payload = {"from": from_, "to": to} | ({"error": error} if to == "failed" else {})
        db.add_event(conn, review_id, "review_status", payload)


def transition_item(
    conn: sqlite3.Connection, review_id: str, item_id: str, from_: ItemStatus, to: ItemStatus
) -> ItemRow:
    """Move one checklist item and return the row after the move."""
    if to not in ITEM_TRANSITIONS[from_]:
        raise InvalidTransition(f"item {item_id} of review {review_id}: {from_} -> {to} is not allowed")
    d_attempt, d_retries = _COUNTERS.get((from_, to), (0, 0))

    with db.transaction(conn):
        rows = conn.execute(
            "UPDATE review_items SET status = ?, attempt = attempt + ?, retries = retries + ?,"
            " updated_at = ? WHERE review_id = ? AND item_id = ? AND status = ?"
            " RETURNING attempt, retries",
            (to, d_attempt, d_retries, db.now(), review_id, item_id, from_),
        ).fetchall()  # fetchall() runs the statement to its end
        if not rows:
            actual = db.get_item(conn, review_id, item_id).status  # KeyError when the item is missing
            raise InvalidTransition(
                f"item {item_id} of review {review_id}: expected {from_}, actual {actual}"
            )
        db.add_event(conn, review_id, "item_status", {
            "from": from_, "to": to, "attempt": rows[0]["attempt"], "retries": rows[0]["retries"],
        }, item_id=item_id)
    return db.get_item(conn, review_id, item_id)
