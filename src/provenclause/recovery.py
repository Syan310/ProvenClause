"""M6 recovery: continue reviews that stopped because the process died.

The state of each checklist item is in the review_items table, so recovery works
per item (decision record 3.6 D3). recover() only changes the database: items
that were "drafting" or "verifying" go back to "pending" with attempt + 1, so
their next finding gets a new operation_id and cannot clash with a finding of
the old attempt. Finished items are not touched. resume_review() then runs only
the "pending" items.

The evaluation runner does not use M6 (D4): it reviews a half-done contract
again from the start, so cost and latency stay correct. The stage 5 API calls
recover() when it starts.
"""

import sqlite3

from provenclause import agent, db
from provenclause.checklist import load_checklist
from provenclause.fsm import transition_item, transition_review
from provenclause.llm import LLM
from provenclause.models import Checklist, Finding, Usage

UNFINISHED_REVIEWS = ("created", "parsing", "reviewing")
UNFINISHED_ITEMS = ("drafting", "verifying")


def recover(conn: sqlite3.Connection) -> list[str]:
    """Reset the unfinished items of every unfinished review; return those review ids, oldest first.

    It calls no model. A second call changes nothing and writes no event.
    """
    review_ids = []
    for review in db.list_reviews(conn, UNFINISHED_REVIEWS):
        reset = []
        with db.transaction(conn):
            for item in db.get_items(conn, review.review_id):
                if item.status in UNFINISHED_ITEMS:
                    transition_item(conn, review.review_id, item.item_id, item.status, "pending")
                    reset.append(item.item_id)
            if reset:
                db.add_event(conn, review.review_id, "review_recovered", {"reset_items": reset})
        review_ids.append(review.review_id)
    return review_ids


async def resume_review(
    conn: sqlite3.Connection, review_id: str, llm: LLM, *, checklist: Checklist | None = None,
) -> tuple[list[Finding], Usage]:
    """Run the pending items of a recovered review and complete it.

    The usage counts only the calls after recovery. Items that ended before the
    crash have stats None (AgentStats are not stored).
    """
    review = db.get_review(conn, review_id)
    if review.config.method != "A1":  # A2 comes in stage 4; B0 writes no reviews
        raise NotImplementedError(f"resume_review() does not support method {review.config.method} yet")
    contract = db.load_contract(conn, review.contract_id)
    checklist = checklist or load_checklist(review.checklist_id)

    status = review.status
    if status == "created":
        transition_review(conn, review_id, "created", "parsing")
        status = "parsing"
    if status == "parsing":
        transition_review(conn, review_id, "parsing", "reviewing")
    return await agent.run_review(conn, review_id, contract, checklist, review.config, llm)
