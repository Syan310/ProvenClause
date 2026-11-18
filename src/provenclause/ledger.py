"""N3 finding ledger: check a finding, then record it at most once per attempt.

validate_finding() is a pure function; stage 4's L1 check calls it too. The
error messages are in English with the exact values, because stage 3 hands
them back to the model so it can fix the finding.

record_finding() keys each finding by operation_id = "{review_id}:{item_id}:{attempt}".
The same operation_id written twice keeps only the first row. A rejected
finding writes no row, so a fixed finding in the same attempt can still be recorded.
"""

import sqlite3

from pydantic import BaseModel

from provenclause import db
from provenclause.db import StoredFinding
from provenclause.models import Contract, Finding


class RecordResult(BaseModel):
    ok: bool
    errors: list[str] = []
    operation_id: str | None = None
    duplicate: bool = False
    stored: StoredFinding | None = None


def operation_id(review_id: str, item_id: str, attempt: int) -> str:
    return f"{review_id}:{item_id}:{attempt}"


def validate_finding(finding: Finding, contract: Contract) -> list[str]:
    """All problems of the finding against the contract; [] when there are none."""
    errors: list[str] = []
    ids = finding.span_ids
    if finding.label in ("Entailment", "Contradiction") and not ids:
        errors.append(f"label {finding.label} requires at least one span_id")
    if finding.label == "NotMentioned" and ids:
        errors.append(f"label NotMentioned must have no span_ids, got {ids}")

    valid = {s.span_id for s in contract.spans}
    missing = list(dict.fromkeys(i for i in ids if i not in valid))
    if missing:
        valid_text = f"{min(valid)}-{max(valid)}" if valid else "none"
        errors.append(f"span_ids {missing} do not exist; valid ids are {valid_text}")
    duplicates = list(dict.fromkeys(i for i in ids if ids.count(i) > 1))
    if duplicates:
        errors.append(f"span_ids contain duplicates: {duplicates}")

    if finding.self_confidence is not None and not 0 <= finding.self_confidence <= 100:
        errors.append(f"self_confidence must be 0-100, got {finding.self_confidence}")
    return errors


def record_finding(
    conn: sqlite3.Connection, contract: Contract, review_id: str, finding: Finding
) -> RecordResult:
    """Check and record one finding for the item's current attempt.

    It does not change the item's status; the caller (the stage 3 Agent) does.
    """
    item = db.get_item(conn, review_id, finding.item_id)  # KeyError when the item is missing
    errors = validate_finding(finding, contract)
    if errors:
        db.add_event(conn, review_id, "finding_rejected",
                     {"attempt": item.attempt, "errors": errors}, item_id=item.item_id)
        return RecordResult(ok=False, errors=errors)

    op = operation_id(review_id, item.item_id, item.attempt)
    with db.transaction(conn):
        inserted = db.insert_finding(conn, op, review_id, item.attempt, finding)
        stored = db.get_finding(conn, op)  # on a duplicate: the first row, not this finding
        db.add_event(conn, review_id, "finding_recorded", {
            "operation_id": op, "label": stored.finding.label,
            "span_ids": stored.finding.span_ids, "duplicate": not inserted,
        }, item_id=item.item_id)
    return RecordResult(ok=True, operation_id=op, duplicate=not inserted, stored=stored)
