"""Stage 2 M2: SQLite tables and read/write functions."""

import sqlite3

import pytest

from provenclause import db
from provenclause.checklist import load_checklist
from provenclause.fsm import transition_review
from provenclause.models import Contract, Finding, ReviewConfig, Span
from provenclause.parse import annotate_contract, load_contractnli

CONFIG = ReviewConfig(name="A2-F", method="A2", agent_model="deepseek-flash")


def small_contract(contract_id: str = "c1", text_suffix: str = "") -> Contract:
    texts = ["1. Definitions", '"Data" means all data.', "See Section 1." + text_suffix]
    spans, pos = [], 0
    for i, text in enumerate(texts):
        spans.append(Span(span_id=i, start=pos, end=pos + len(text), text=text))
        pos += len(text) + 1
    return annotate_contract(Contract(
        contract_id=contract_id, source="contractnli", title="Small NDA",
        text="\n".join(texts), spans=spans,
    ))


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "pc.db")
    yield connection
    connection.close()


def count(conn: sqlite3.Connection, table: str) -> int:
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_schema_version_is_set(conn):
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION == 1
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert tables == {"contracts", "spans", "reviews", "review_items", "findings", "events"}


def test_other_schema_version_is_an_error(tmp_path):
    path = tmp_path / "old.db"
    raw = sqlite3.connect(path)
    raw.execute("PRAGMA user_version = 7")
    raw.close()
    with pytest.raises(RuntimeError, match="schema version 7"):
        db.connect(path)


def test_reopen_keeps_data(tmp_path):
    path = tmp_path / "pc.db"
    first = db.connect(path)
    db.save_contract(first, small_contract())
    first.close()
    second = db.connect(path)
    assert db.load_contract(second, "c1") == small_contract()
    second.close()


def test_wal_and_foreign_keys(conn):
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_memory_database():
    conn = db.connect(":memory:")
    db.save_contract(conn, small_contract())
    assert db.load_contract(conn, "c1") == small_contract()
    conn.close()


def test_save_and_load_are_equal(conn):
    contract = small_contract()
    assert contract.spans[1].defines == ["Data"] and contract.spans[2].refs == ["1"]
    db.save_contract(conn, contract)
    assert db.load_contract(conn, "c1") == contract


def test_save_and_load_dev_contract(conn, contractnli_available):
    contract = next(c for c in load_contractnli("dev") if c.contract_id == "64")
    db.save_contract(conn, contract)
    assert db.load_contract(conn, "64") == contract


def test_save_twice_is_a_no_op(conn):
    db.save_contract(conn, small_contract())
    db.save_contract(conn, small_contract())
    assert count(conn, "contracts") == 1
    assert count(conn, "spans") == 3


def test_save_with_other_text_is_an_error(conn):
    db.save_contract(conn, small_contract())
    with pytest.raises(ValueError, match="other text"):
        db.save_contract(conn, small_contract(text_suffix=" Changed."))
    assert db.load_contract(conn, "c1") == small_contract()


def test_missing_rows_raise_key_error(conn):
    with pytest.raises(KeyError, match="contract nope"):
        db.load_contract(conn, "nope")
    with pytest.raises(KeyError, match="review nope"):
        db.get_review(conn, "nope")
    with pytest.raises(KeyError, match="item nda-1"):
        db.get_item(conn, "nope", "nda-1")


def test_create_review(conn):
    db.save_contract(conn, small_contract())
    checklist = load_checklist("nda_17")
    review_id = db.create_review(conn, "c1", checklist, CONFIG)

    assert len(review_id) == 32
    review = db.get_review(conn, review_id)
    assert review.status == "created" and review.config == CONFIG and review.error is None
    items = db.get_items(conn, review_id)
    assert [i.item_id for i in items] == [item.id for item in checklist.items]
    assert len(items) == 17
    assert all(i.status == "pending" and i.attempt == 1 and i.retries == 0 for i in items)
    assert (count(conn, "reviews"), count(conn, "review_items"), count(conn, "events")) == (1, 17, 1)
    (event,) = db.list_events(conn, review_id)
    assert event.type == "review_created" and event.seq == 1
    assert event.payload == {"contract_id": "c1", "checklist_id": "nda_17", "config_name": "A2-F"}


def test_add_event_seq_counts_up_per_review(conn):
    db.save_contract(conn, small_contract())
    checklist = load_checklist("nda_17")
    r1 = db.create_review(conn, "c1", checklist, CONFIG)
    r2 = db.create_review(conn, "c1", checklist, CONFIG)
    assert db.add_event(conn, r1, "note", {"n": 1}) == 2
    assert db.add_event(conn, r1, "note", {"n": 2}, item_id="nda-1") == 3
    assert db.add_event(conn, r2, "note", {"n": 3}) == 2
    events = db.list_events(conn, r1)
    assert [e.seq for e in events] == [1, 2, 3]
    assert events[2].item_id == "nda-1" and events[2].payload == {"n": 2}
    assert not conn.in_transaction  # the RETURNING statement finished and committed


def test_review_needs_existing_contract(conn):
    with pytest.raises(sqlite3.IntegrityError):
        db.create_review(conn, "missing", load_checklist("nda_17"), CONFIG)
    assert (count(conn, "reviews"), count(conn, "review_items"), count(conn, "events")) == (0, 0, 0)


def test_transaction_rolls_back_on_error(conn):
    with pytest.raises(RuntimeError):
        with db.transaction(conn):
            db.save_contract(conn, small_contract())  # joins the open transaction
            raise RuntimeError("boom")
    assert count(conn, "contracts") == 0
    assert not conn.in_transaction


# --- stage 3 ----------------------------------------------------------------------


def test_set_finding_outcome(conn):
    db.save_contract(conn, small_contract())
    review_id = db.create_review(conn, "c1", load_checklist("nda_17"), CONFIG)
    op = f"{review_id}:nda-1:1"
    db.insert_finding(conn, op, review_id, 1, Finding(item_id="nda-1", label="NotMentioned", span_ids=[],
                                                      rationale="r"))
    assert db.get_finding(conn, op).finding.outcome is None

    db.set_finding_outcome(conn, op, "verified")
    assert db.get_finding(conn, op).finding.outcome == "verified"
    assert db.get_finding(conn, op).finding.checks == {}

    db.set_finding_outcome(conn, op, "violated", checks={"L1": "pass"}, support_score=40)
    stored = db.get_finding(conn, op).finding
    assert (stored.outcome, stored.checks, stored.support_score) == ("violated", {"L1": "pass"}, 40)

    with pytest.raises(KeyError, match="finding nope"):
        db.set_finding_outcome(conn, "nope", "verified")


def test_list_reviews_filters_and_sorts(conn):
    db.save_contract(conn, small_contract())
    checklist = load_checklist("nda_17")
    r1 = db.create_review(conn, "c1", checklist, CONFIG)
    r2 = db.create_review(conn, "c1", checklist, CONFIG)
    r3 = db.create_review(conn, "c1", checklist, CONFIG)
    transition_review(conn, r2, "created", "parsing")
    transition_review(conn, r3, "created", "failed", error="boom")

    assert [r.review_id for r in db.list_reviews(conn, ("created", "parsing", "reviewing"))] == [r1, r2]
    assert [r.review_id for r in db.list_reviews(conn, ["failed"])] == [r3]
    assert db.list_reviews(conn, ()) == []
