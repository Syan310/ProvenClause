"""Stage 2 N3: finding rules, idempotent recording, events."""

import pytest

from provenclause import db
from provenclause.checklist import load_checklist
from provenclause.fsm import transition_item
from provenclause.ledger import operation_id, record_finding, validate_finding
from provenclause.models import Contract, Finding, ReviewConfig, Span

CONFIG = ReviewConfig(name="A2-F", method="A2", agent_model="deepseek-flash")
ITEM = "nda-1"


def make_contract(n_spans: int = 77) -> Contract:
    spans = [Span(span_id=i, start=2 * i, end=2 * i + 1, text="x") for i in range(n_spans)]
    return Contract(
        contract_id="c1", source="contractnli", title="t", text="x\n" * n_spans, spans=spans,
    )


def finding(label="Entailment", span_ids=(3,), rationale="r", **kwargs) -> Finding:
    return Finding(item_id=ITEM, label=label, span_ids=list(span_ids), rationale=rationale, **kwargs)


@pytest.fixture
def contract():
    return make_contract()


@pytest.fixture
def conn(contract):
    connection = db.connect(":memory:")
    db.save_contract(connection, contract)
    yield connection
    connection.close()


@pytest.fixture
def review_id(conn):
    return db.create_review(conn, "c1", load_checklist("nda_17"), CONFIG)


def events_of(conn, review_id, type_):
    return [e for e in db.list_events(conn, review_id) if e.type == type_]


# --- validate_finding ----------------------------------------------------------


def test_valid_finding_has_no_errors(contract):
    assert validate_finding(finding(), contract) == []
    assert validate_finding(finding("NotMentioned", []), contract) == []
    assert validate_finding(finding(self_confidence=0), contract) == []
    assert validate_finding(finding(self_confidence=100), contract) == []


@pytest.mark.parametrize("label", ["Entailment", "Contradiction"])
def test_support_needs_evidence(contract, label):
    assert validate_finding(finding(label, []), contract) == [
        f"label {label} requires at least one span_id"
    ]


def test_not_mentioned_has_no_evidence(contract):
    assert validate_finding(finding("NotMentioned", [3, 7]), contract) == [
        "label NotMentioned must have no span_ids, got [3, 7]"
    ]


def test_span_ids_must_exist(contract):
    assert validate_finding(finding(span_ids=[2, 99, 120, 99, -1]), contract) == [
        "span_ids [99, 120, -1] do not exist; valid ids are 0-76",
        "span_ids contain duplicates: [99]",
    ]


def test_span_ids_without_duplicates(contract):
    assert validate_finding(finding(span_ids=[5, 6, 5]), contract) == [
        "span_ids contain duplicates: [5]"
    ]


@pytest.mark.parametrize("value", [140, -1])
def test_self_confidence_range(contract, value):
    assert validate_finding(finding(self_confidence=value), contract) == [
        f"self_confidence must be 0-100, got {value}"
    ]


def test_two_problems_give_two_errors(contract):
    errors = validate_finding(finding("NotMentioned", [99], self_confidence=140), contract)
    assert errors == [
        "label NotMentioned must have no span_ids, got [99]",
        "span_ids [99] do not exist; valid ids are 0-76",
        "self_confidence must be 0-100, got 140",
    ]


# --- record_finding --------------------------------------------------------------


def test_operation_id_is_readable():
    assert operation_id("abc", "nda-1", 2) == "abc:nda-1:2"


def test_record_once(conn, contract, review_id):
    result = record_finding(conn, contract, review_id, finding())
    assert result.ok and not result.duplicate and result.errors == []
    assert result.operation_id == f"{review_id}:{ITEM}:1"
    assert result.stored.finding == finding() and result.stored.attempt == 1
    assert db.list_findings(conn, review_id) == [result.stored]


def test_same_operation_id_twice_keeps_the_first(conn, contract, review_id):
    first = record_finding(conn, contract, review_id, finding(span_ids=[3]))
    second = record_finding(conn, contract, review_id, finding(span_ids=[4, 5], rationale="other"))
    assert second.ok and second.duplicate
    assert second.operation_id == first.operation_id
    assert second.stored == first.stored  # the first content, not overwritten
    assert second.stored.finding.span_ids == [3]
    assert len(db.list_findings(conn, review_id)) == 1
    recorded = events_of(conn, review_id, "finding_recorded")
    assert [e.payload["duplicate"] for e in recorded] == [False, True]
    assert recorded[1].payload["span_ids"] == [3]


def test_rejected_then_fixed_in_same_attempt(conn, contract, review_id):
    rejected = record_finding(conn, contract, review_id, finding(span_ids=[]))
    assert not rejected.ok and rejected.operation_id is None and rejected.stored is None
    assert rejected.errors == ["label Entailment requires at least one span_id"]
    assert db.list_findings(conn, review_id) == []

    fixed = record_finding(conn, contract, review_id, finding(span_ids=[3]))
    assert fixed.ok and not fixed.duplicate
    assert fixed.operation_id == f"{review_id}:{ITEM}:1"  # the rejection did not use it up


def test_retry_gives_a_new_operation_id(conn, contract, review_id):
    first = record_finding(conn, contract, review_id, finding(span_ids=[3]))
    transition_item(conn, review_id, ITEM, "pending", "drafting")
    transition_item(conn, review_id, ITEM, "drafting", "verifying")
    transition_item(conn, review_id, ITEM, "verifying", "drafting")  # attempt 2
    second = record_finding(conn, contract, review_id, finding(span_ids=[4]))
    assert second.ok and not second.duplicate
    assert second.operation_id == f"{review_id}:{ITEM}:2" != first.operation_id
    assert [f.attempt for f in db.list_findings(conn, review_id, ITEM)] == [1, 2]


def test_one_event_per_call(conn, contract, review_id):
    record_finding(conn, contract, review_id, finding(span_ids=[]))
    record_finding(conn, contract, review_id, finding())
    (rejected,) = events_of(conn, review_id, "finding_rejected")
    (recorded,) = events_of(conn, review_id, "finding_recorded")
    assert rejected.item_id == recorded.item_id == ITEM
    assert rejected.payload == {
        "attempt": 1, "errors": ["label Entailment requires at least one span_id"],
    }
    assert recorded.payload == {
        "operation_id": f"{review_id}:{ITEM}:1", "label": "Entailment", "span_ids": [3],
        "duplicate": False,
    }


def test_record_keeps_all_fields(conn, contract, review_id):
    full = finding(
        "Contradiction", [1, 2], reason=None, self_confidence=80, outcome="verified",
        support_score=4, checks={"L1": "pass", "L2": "pass"},
    )
    stored = record_finding(conn, contract, review_id, full).stored
    assert stored.finding == full
    assert db.get_finding(conn, stored.operation_id) == stored


def test_record_does_not_change_item_status(conn, contract, review_id):
    record_finding(conn, contract, review_id, finding())
    assert db.get_item(conn, review_id, ITEM).status == "pending"


def test_unknown_item(conn, contract, review_id):
    with pytest.raises(KeyError):
        record_finding(conn, contract, review_id, finding().model_copy(update={"item_id": "nda-99"}))


def test_findings_filter_by_item(conn, contract, review_id):
    record_finding(conn, contract, review_id, finding())
    other = finding().model_copy(update={"item_id": "nda-2"})
    record_finding(conn, contract, review_id, other)
    assert [f.finding.item_id for f in db.list_findings(conn, review_id)] == [ITEM, "nda-2"]
    assert [f.finding.item_id for f in db.list_findings(conn, review_id, "nda-2")] == ["nda-2"]
