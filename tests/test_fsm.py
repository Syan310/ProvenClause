"""Stage 2 M3: transition tables, compare-and-set, events and counters."""

import pytest

from provenclause import db
from provenclause.checklist import load_checklist
from provenclause.fsm import (
    ITEM_TRANSITIONS,
    REVIEW_TRANSITIONS,
    InvalidTransition,
    transition_item,
    transition_review,
)
from provenclause.models import Contract, ReviewConfig, Span

CONFIG = ReviewConfig(name="A2-F", method="A2", agent_model="deepseek-flash")
ITEM = "nda-1"

# Paths from "created" / "pending" to each status, through allowed edges.
REVIEW_PATH = {
    "created": [], "parsing": ["parsing"], "reviewing": ["parsing", "reviewing"],
    "completed": ["parsing", "reviewing", "completed"], "failed": ["failed"],
}
ITEM_PATH = {
    "pending": [], "drafting": ["drafting"], "verifying": ["drafting", "verifying"],
    "verified": ["drafting", "verifying", "verified"],
    "violated": ["drafting", "verifying", "violated"], "abstained": ["drafting", "abstained"],
}


@pytest.fixture
def conn():
    connection = db.connect(":memory:")
    db.save_contract(connection, Contract(
        contract_id="c1", source="contractnli", title="t", text="x",
        spans=[Span(span_id=0, start=0, end=1, text="x")],
    ))
    yield connection
    connection.close()


@pytest.fixture
def review_id(conn):
    return db.create_review(conn, "c1", load_checklist("nda_17"), CONFIG)


def walk_review(conn, review_id, path):
    status = "created"
    for to in path:
        transition_review(conn, review_id, status, to)
        status = to


def walk_item(conn, review_id, path):
    status = "pending"
    for to in path:
        transition_item(conn, review_id, ITEM, status, to)
        status = to


def n_events(conn, review_id):
    return len(db.list_events(conn, review_id))


# --- reviews -----------------------------------------------------------------


def test_review_happy_path(conn, review_id):
    walk_review(conn, review_id, ["parsing", "reviewing", "completed"])
    assert db.get_review(conn, review_id).status == "completed"
    events = [e for e in db.list_events(conn, review_id) if e.type == "review_status"]
    assert [(e.payload["from"], e.payload["to"]) for e in events] == [
        ("created", "parsing"), ("parsing", "reviewing"), ("reviewing", "completed"),
    ]


@pytest.mark.parametrize("start", ["created", "parsing", "reviewing"])
def test_review_can_fail_from_any_open_status(conn, review_id, start):
    walk_review(conn, review_id, REVIEW_PATH[start])
    transition_review(conn, review_id, start, "failed", error="API timeout")
    review = db.get_review(conn, review_id)
    assert review.status == "failed" and review.error == "API timeout"
    assert db.list_events(conn, review_id)[-1].payload == {
        "from": start, "to": "failed", "error": "API timeout",
    }


def test_error_only_for_failed(conn, review_id):
    with pytest.raises(ValueError, match="only allowed"):
        transition_review(conn, review_id, "created", "parsing", error="x")
    assert db.get_review(conn, review_id).status == "created"


ALL_REVIEW = list(REVIEW_TRANSITIONS)
REVIEW_EDGES = [(a, b) for a in ALL_REVIEW for b in ALL_REVIEW if b in REVIEW_TRANSITIONS[a]]
REVIEW_NON_EDGES = [(a, b) for a in ALL_REVIEW for b in ALL_REVIEW if b not in REVIEW_TRANSITIONS[a]]


@pytest.mark.parametrize(("from_", "to"), REVIEW_EDGES)
def test_every_review_edge_works(conn, review_id, from_, to):
    walk_review(conn, review_id, REVIEW_PATH[from_])
    before = n_events(conn, review_id)
    transition_review(conn, review_id, from_, to)
    assert db.get_review(conn, review_id).status == to
    assert n_events(conn, review_id) == before + 1


@pytest.mark.parametrize(("from_", "to"), REVIEW_NON_EDGES)
def test_every_review_non_edge_is_refused(conn, review_id, from_, to):
    walk_review(conn, review_id, REVIEW_PATH[from_])
    before = n_events(conn, review_id)
    with pytest.raises(InvalidTransition, match=f"{from_} -> {to}"):
        transition_review(conn, review_id, from_, to)
    assert db.get_review(conn, review_id).status == from_
    assert n_events(conn, review_id) == before


def test_terminal_review_status_is_final(conn, review_id):
    walk_review(conn, review_id, REVIEW_PATH["completed"])
    for to in ALL_REVIEW:
        with pytest.raises(InvalidTransition):
            transition_review(conn, review_id, "completed", to)


def test_review_stale_from_is_refused(conn, review_id):
    walk_review(conn, review_id, ["parsing"])
    before = n_events(conn, review_id)
    with pytest.raises(InvalidTransition, match="expected created, actual parsing"):
        transition_review(conn, review_id, "created", "failed", error="late")
    review = db.get_review(conn, review_id)
    assert review.status == "parsing" and review.error is None
    assert n_events(conn, review_id) == before
    assert not conn.in_transaction


def test_missing_review(conn):
    with pytest.raises(KeyError):
        transition_review(conn, "nope", "created", "parsing")


# --- items ---------------------------------------------------------------------


ALL_ITEM = list(ITEM_TRANSITIONS)
ITEM_EDGES = [(a, b) for a in ALL_ITEM for b in ALL_ITEM if b in ITEM_TRANSITIONS[a]]
ITEM_NON_EDGES = [
    ("pending", "verifying"), ("pending", "verified"), ("pending", "abstained"),
    ("drafting", "verified"), ("drafting", "violated"), ("drafting", "drafting"),
    ("verifying", "verifying"), ("verified", "drafting"), ("violated", "pending"),
    ("abstained", "drafting"),
]


@pytest.mark.parametrize(("from_", "to"), ITEM_EDGES)
def test_every_item_edge_works(conn, review_id, from_, to):
    walk_item(conn, review_id, ITEM_PATH[from_])
    before = n_events(conn, review_id)
    row = transition_item(conn, review_id, ITEM, from_, to)
    assert row.status == to == db.get_item(conn, review_id, ITEM).status
    assert n_events(conn, review_id) == before + 1
    event = db.list_events(conn, review_id)[-1]
    assert event.type == "item_status" and event.item_id == ITEM
    assert event.payload == {"from": from_, "to": to, "attempt": row.attempt, "retries": row.retries}


@pytest.mark.parametrize(("from_", "to"), ITEM_NON_EDGES)
def test_item_non_edges_are_refused(conn, review_id, from_, to):
    walk_item(conn, review_id, ITEM_PATH[from_])
    before = n_events(conn, review_id)
    with pytest.raises(InvalidTransition, match=f"{from_} -> {to}"):
        transition_item(conn, review_id, ITEM, from_, to)
    assert db.get_item(conn, review_id, ITEM).status == from_
    assert n_events(conn, review_id) == before


def test_retry_adds_attempt_and_retries(conn, review_id):
    walk_item(conn, review_id, ["drafting", "verifying"])
    row = transition_item(conn, review_id, ITEM, "verifying", "drafting")
    assert (row.attempt, row.retries) == (2, 1)


@pytest.mark.parametrize("from_", ["drafting", "verifying"])
def test_recovery_adds_only_attempt(conn, review_id, from_):
    walk_item(conn, review_id, ITEM_PATH[from_])
    row = transition_item(conn, review_id, ITEM, from_, "pending")
    assert (row.attempt, row.retries) == (2, 0)


def test_other_edges_keep_counters(conn, review_id):
    walk_item(conn, review_id, ["drafting", "verifying", "verified"])
    row = db.get_item(conn, review_id, ITEM)
    assert (row.attempt, row.retries) == (1, 0)


def test_drafting_to_abstained_is_allowed(conn, review_id):
    walk_item(conn, review_id, ["drafting"])
    assert transition_item(conn, review_id, ITEM, "drafting", "abstained").status == "abstained"


def test_item_stale_from_is_refused(conn, review_id):
    walk_item(conn, review_id, ["drafting"])
    before = n_events(conn, review_id)
    with pytest.raises(InvalidTransition, match="expected pending, actual drafting"):
        transition_item(conn, review_id, ITEM, "pending", "drafting")
    row = db.get_item(conn, review_id, ITEM)
    assert (row.status, row.attempt) == ("drafting", 1)
    assert n_events(conn, review_id) == before


def test_other_items_do_not_change(conn, review_id):
    walk_item(conn, review_id, ["drafting"])
    assert db.get_item(conn, review_id, "nda-2").status == "pending"


def test_missing_item(conn, review_id):
    with pytest.raises(KeyError):
        transition_item(conn, review_id, "nda-99", "pending", "drafting")
