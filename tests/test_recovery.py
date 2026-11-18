"""Stage 3 M6: a crash in the middle of a review, then recover() and resume_review()."""

import pytest

from conftest import record, routed_llm
from provenclause import agent, db
from provenclause.agent import review_a1
from provenclause.checklist import load_checklist
from provenclause.fsm import transition_review
from provenclause.models import ReviewConfig
from provenclause.recovery import recover, resume_review

CHECKLIST = load_checklist("nda_17")
IDS = [item.id for item in CHECKLIST.items]
CONFIG = ReviewConfig(name="A1-F", method="A1", agent_model="deepseek-flash", effort="high",
                      max_tokens=1000, concurrency=1)


class Crash(BaseException):
    """Stands for a killed process: not an Exception, so the review is not marked failed."""


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "reviews.db")
    yield connection
    connection.close()


def only_review_id(conn):
    (row,) = conn.execute("SELECT review_id FROM reviews").fetchall()
    return row["review_id"]


def statuses(conn, review_id):
    return {i.item_id: i.status for i in db.get_items(conn, review_id)}


def assert_complete(conn, review_id, findings):
    assert [f.item_id for f in findings] == IDS
    items = db.get_items(conn, review_id)
    assert all(i.status == "verified" for i in items)
    assert db.get_review(conn, review_id).status == "completed"
    rows = db.list_findings(conn, review_id)
    assert len({r.operation_id for r in rows}) == len(rows)  # no duplicate
    attempts = {i.item_id: i.attempt for i in items}
    current = [r for r in rows if r.attempt == attempts[r.finding.item_id]]
    assert sorted(r.finding.item_id for r in current) == sorted(IDS)  # one finding per item and attempt


async def test_crash_after_five_items(conn, small_nda):
    fake = routed_llm({IDS[5]: [Crash()]})
    with pytest.raises(BaseException) as info:  # a BaseExceptionGroup from the TaskGroup
        await review_a1(conn, small_nda, CHECKLIST, CONFIG, fake)
    assert info.group_contains(Crash) if isinstance(info.value, BaseExceptionGroup) else info.type is Crash
    review_id = only_review_id(conn)

    state = statuses(conn, review_id)
    assert [state[i] for i in IDS[:5]] == ["verified"] * 5
    assert state[IDS[5]] == "drafting"
    # Item 7 took the semaphore when item 6 stopped, then the TaskGroup cancelled it
    # during its model call. A killed process can leave the same state.
    assert state[IDS[6]] == "drafting"
    assert [state[i] for i in IDS[7:]] == ["pending"] * 10
    assert db.get_review(conn, review_id).status == "reviewing"
    before = {r.finding.item_id: r for r in db.list_findings(conn, review_id)}
    assert sorted(before) == sorted(IDS[:5])

    assert recover(conn) == [review_id]
    for item_id in IDS[5:7]:
        item = db.get_item(conn, review_id, item_id)
        assert (item.status, item.attempt, item.retries) == ("pending", 2, 0)
    (event,) = [e for e in db.list_events(conn, review_id) if e.type == "review_recovered"]
    assert event.payload == {"reset_items": IDS[5:7]}

    fake2 = routed_llm({})
    findings, usage = await resume_review(conn, review_id, fake2)
    assert len(fake2.calls) == usage.n_calls == 12  # only the items that were not finished
    assert_complete(conn, review_id, findings)
    assert all(f.stats is None for f in findings[:5])  # ended before the crash
    assert all(f.stats is not None for f in findings[5:])
    after = {r.finding.item_id: r for r in db.list_findings(conn, review_id)}
    assert all(after[i] == before[i] for i in IDS[:5])  # not written again
    assert db.get_item(conn, review_id, IDS[5]).attempt == 2


async def test_crash_after_record_before_status(conn, small_nda, monkeypatch):
    def crash(ctx, item_id, record_result):
        raise Crash()

    monkeypatch.setattr(agent, "finish_verified", crash)
    with pytest.raises(Crash):  # item 1 runs alone, outside the TaskGroup
        await review_a1(conn, small_nda, CHECKLIST, CONFIG, routed_llm({IDS[0]: [record("Entailment", [5])]}))
    monkeypatch.undo()
    review_id = only_review_id(conn)
    assert db.get_item(conn, review_id, IDS[0]).status == "drafting"
    (old,) = db.list_findings(conn, review_id, IDS[0])
    assert (old.attempt, old.finding.label, old.finding.outcome) == (1, "Entailment", None)

    recover(conn)
    findings, _ = await resume_review(conn, review_id, routed_llm({IDS[0]: [record("NotMentioned")]}))
    assert_complete(conn, review_id, findings)
    rows = db.list_findings(conn, review_id, IDS[0])
    assert [(r.attempt, r.finding.label) for r in rows] == [(1, "Entailment"), (2, "NotMentioned")]
    assert (findings[0].label, findings[0].outcome) == ("NotMentioned", "verified")


async def test_created_review_is_recovered(conn, small_nda):
    db.save_contract(conn, small_nda)
    review_id = db.create_review(conn, small_nda.contract_id, CHECKLIST, CONFIG)
    assert recover(conn) == [review_id]
    assert not [e for e in db.list_events(conn, review_id) if e.type == "review_recovered"]  # nothing reset

    findings, usage = await resume_review(conn, review_id, routed_llm({}))
    assert usage.n_calls == 17
    assert_complete(conn, review_id, findings)
    moves = [(e.payload["from"], e.payload["to"]) for e in db.list_events(conn, review_id)
             if e.type == "review_status"]
    assert moves == [("created", "parsing"), ("parsing", "reviewing"), ("reviewing", "completed")]


async def test_recover_twice_changes_nothing(conn, small_nda):
    with pytest.raises(BaseException):
        await review_a1(conn, small_nda, CHECKLIST, CONFIG, routed_llm({IDS[2]: [Crash()]}))
    review_id = only_review_id(conn)
    assert recover(conn) == [review_id]
    items = db.get_items(conn, review_id)
    n_events = len(db.list_events(conn, review_id))

    assert recover(conn) == [review_id]
    assert db.get_items(conn, review_id) == items
    assert len(db.list_events(conn, review_id)) == n_events


async def test_finished_reviews_are_left_alone(conn, small_nda):
    await review_a1(conn, small_nda, CHECKLIST, CONFIG, routed_llm({}))
    db.save_contract(conn, small_nda)
    failed = db.create_review(conn, small_nda.contract_id, CHECKLIST, CONFIG)
    transition_review(conn, failed, "created", "failed", error="x")
    assert recover(conn) == []


async def test_resume_a2_not_implemented(conn, small_nda):
    db.save_contract(conn, small_nda)
    review_id = db.create_review(conn, small_nda.contract_id, CHECKLIST,
                                 CONFIG.model_copy(update={"method": "A2"}))
    with pytest.raises(NotImplementedError):
        await resume_review(conn, review_id, routed_llm({}))
    assert db.get_review(conn, review_id).status == "created"
