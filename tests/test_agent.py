"""Stage 3 M4: the Agent loop for one item and for a whole review (FakeLLM, no cost)."""

import json
from collections import Counter

import pytest

from conftest import FAKE_EMAIL, FAKE_PHONE, item_of, record, routed_llm
from provenclause import agent, db
from provenclause.agent import NUDGE, prompt_hash, review_a1
from provenclause.checklist import load_checklist
from provenclause.llm import FakeLLM, ModelInterrupted, make_completion, text_reply, tool_call_reply
from provenclause.models import AgentStats, Checklist, ReviewConfig
from provenclause.review import review_contract

CHECKLIST = load_checklist("nda_17")
IDS = [item.id for item in CHECKLIST.items]
ONE = Checklist(checklist_id="nda_17", items=CHECKLIST.items[:1])
CONFIG = ReviewConfig(name="A1-F", method="A1", agent_model="deepseek-flash", effort="high",
                      max_tokens=1000, max_steps=8, concurrency=4)


@pytest.fixture
def conn():
    connection = db.connect(":memory:")
    yield connection
    connection.close()


def search(query="return or destroy", **usage):
    return tool_call_reply(("search_spans", {"query": query}), **usage)


def truncated():
    return make_completion(None, finish_reason="length")


def only_review_id(conn):
    (row,) = conn.execute("SELECT review_id FROM reviews").fetchall()
    return row["review_id"]


def events(conn, type_=None, item_id=None):
    return [e for e in db.list_events(conn, only_review_id(conn))
            if (type_ is None or e.type == type_) and (item_id is None or e.item_id == item_id)]


async def review_one(conn, contract, script, config=CONFIG):
    fake = FakeLLM(script)
    findings, usage = await review_a1(conn, contract, ONE, config, fake)
    return findings[0], usage, fake


# --- one item -----------------------------------------------------------------------


async def test_search_then_record(conn, small_nda):
    finding, usage, fake = await review_one(conn, small_nda, [search(), record("Entailment", [6])])
    assert (finding.label, finding.span_ids, finding.outcome, finding.reason) == ("Entailment", [6], "verified", None)
    assert finding.self_confidence == 80
    assert finding.stats == AgentStats(n_steps=2, tool_calls={"search_spans": 1, "record_finding": 1})
    assert usage.n_calls == 2
    review_id = only_review_id(conn)
    (stored,) = db.list_findings(conn, review_id)
    assert stored.finding.outcome == "verified" and stored.attempt == 1
    assert db.get_item(conn, review_id, IDS[0]).status == "verified"
    assert db.get_review(conn, review_id).status == "completed"
    # the search result went back to the model as a tool message
    tool_message = fake.calls[1]["messages"][2]
    assert tool_message["role"] == "tool" and tool_message["tool_call_id"] == "call_1"
    assert tool_message["content"].startswith("[S6] (§2)")


async def test_prompt_parts(conn, small_nda):
    _, _, fake = await review_one(conn, small_nda, [record()])
    request = fake.calls[0]
    assert request["system"].startswith(agent.SYSTEM + "\n\nContract:\n[S0] MUTUAL")
    assert request["messages"][0]["content"].startswith(f"Checklist item\nid: {IDS[0]}\nhypothesis: ")
    assert request["tools"] == agent.TOOL_SCHEMAS
    assert (request["max_tokens"], request["effort"], request["model"]) == (1000, "high", "deepseek-flash")


async def test_text_reply_gets_nudge(conn, small_nda):
    finding, _, fake = await review_one(conn, small_nda, [text_reply("It is NotMentioned."), record()])
    assert finding.outcome == "verified"
    assert finding.stats.n_nudges == 1 and finding.stats.n_steps == 2
    second = fake.calls[1]["messages"]
    assert [m["role"] for m in second] == ["user", "assistant", "user"]
    assert second[1]["content"] == "It is NotMentioned." and second[2]["content"] == NUDGE
    assert len(events(conn, "nudge")) == 1


async def test_rejected_record_then_fixed(conn, small_nda):
    finding, _, fake = await review_one(conn, small_nda, [record("Entailment", []), record("Entailment", [5])])
    assert (finding.label, finding.span_ids, finding.outcome) == ("Entailment", [5], "verified")
    assert finding.stats.n_rejected_records == 1 and finding.stats.n_tool_errors == 0
    assert "requires at least one span_id" in fake.calls[1]["messages"][2]["content"]
    (stored,) = db.list_findings(conn, only_review_id(conn))
    assert stored.attempt == 1  # the rejected record did not use up the operation_id


async def test_over_max_steps_abstains(conn, small_nda):
    finding, usage, fake = await review_one(conn, small_nda, [search()] * 8)
    assert len(fake.calls) == 8 and usage.n_calls == 8
    assert (finding.outcome, finding.reason, finding.label, finding.span_ids) == (
        "abstained", "budget_exceeded", "NotMentioned", [])
    assert finding.stats.n_steps == 8
    (stored,) = db.list_findings(conn, only_review_id(conn))
    assert stored.finding.reason == "budget_exceeded" and stored.finding.outcome == "abstained"
    (event,) = events(conn, "budget_exceeded")
    assert event.payload["kind"] == "steps" and event.payload["step"] == 8
    assert db.get_item(conn, only_review_id(conn), IDS[0]).status == "abstained"


async def test_review_token_budget(conn, small_nda):
    config = CONFIG.model_copy(update={"max_review_output_tokens": 300})
    fake = routed_llm({IDS[0]: [record(completion_tokens=400)]})
    findings, usage = await review_a1(conn, small_nda, CHECKLIST, config, fake)
    assert len(fake.calls) == 1 and usage.output_tokens == 400
    assert findings[0].outcome == "verified"
    assert all(f.outcome == "abstained" and f.reason == "budget_exceeded" for f in findings[1:])
    assert all(f.stats.n_steps == 0 for f in findings[1:])
    kinds = Counter(e.payload["kind"] for e in events(conn, "budget_exceeded"))
    assert kinds == {"review_tokens": 16}
    assert all(e.payload["review_output_tokens"] == 400 for e in events(conn, "budget_exceeded"))


async def test_length_retry_then_ok(conn, small_nda):
    finding, usage, fake = await review_one(conn, small_nda, [truncated(), record()])
    assert [c["max_tokens"] for c in fake.calls] == [1000, 2000]
    assert fake.calls[0]["messages"] == fake.calls[1]["messages"]  # the cut-off reply is not in the history
    assert finding.outcome == "verified"
    assert finding.stats.n_length_retries == 1 and finding.stats.n_steps == 1
    assert usage.n_calls == 2
    (retry,) = events(conn, "length_retry")
    assert (retry.payload["max_tokens_from"], retry.payload["max_tokens_to"]) == (1000, 2000)
    assert [e.payload["finish_reason"] for e in events(conn, "model_call")] == ["length", "tool_calls"]


async def test_truncated_twice_abstains(conn, small_nda):
    finding, usage, _ = await review_one(conn, small_nda, [truncated(), truncated()])
    assert (finding.outcome, finding.reason) == ("abstained", "max_tokens")
    assert usage.n_calls == 2 and len(events(conn, "model_call")) == 2


async def test_no_length_retry_without_budget(conn, small_nda):
    config = CONFIG.model_copy(update={"max_review_output_tokens": 100})
    cut = make_completion(None, finish_reason="length", completion_tokens=150)
    finding, usage, fake = await review_one(conn, small_nda, [cut], config)
    assert len(fake.calls) == 1 and usage.n_calls == 1  # billed once, not twice
    assert (finding.outcome, finding.reason) == ("abstained", "max_tokens")
    assert finding.stats.n_length_retries == 0 and events(conn, "length_retry") == []


async def test_refusal_abstains(conn, small_nda):
    finding, usage, _ = await review_one(conn, small_nda, [make_completion(None, finish_reason="content_filter")])
    assert (finding.outcome, finding.reason) == ("abstained", "refusal")
    assert usage.n_calls == 1 and finding.stats.n_steps == 1


async def test_server_interrupt_fails_review(conn, small_nda):
    with pytest.raises(ModelInterrupted):
        await review_one(conn, small_nda, [make_completion(None, finish_reason="aborted")])
    review = db.get_review(conn, only_review_id(conn))
    assert review.status == "failed" and review.error.startswith("ModelInterrupted")


async def test_interrupt_in_task_group_raises_real_error(conn, small_nda):
    fake = routed_llm({IDS[3]: [make_completion(None, finish_reason="insufficient_system_resource")]})
    with pytest.raises(ModelInterrupted):  # not ExceptionGroup
        await review_a1(conn, small_nda, CHECKLIST, CONFIG, fake)
    assert db.get_review(conn, only_review_id(conn)).status == "failed"


async def test_two_tool_calls_in_one_reply(conn, small_nda):
    both = tool_call_reply(("search_spans", {"query": "term"}), ("read_spans", {"span_ids": [8]}))
    record_then_search = tool_call_reply(
        ("record_finding", {"label": "NotMentioned", "span_ids": [], "rationale": "r", "self_confidence": 60}),
        ("search_spans", {"query": "never runs"}),
    )
    finding, _, fake = await review_one(conn, small_nda, [both, record_then_search])
    tool_messages = [m for m in fake.calls[1]["messages"] if m["role"] == "tool"]
    assert [m["tool_call_id"] for m in tool_messages] == ["call_1", "call_2"]
    assert finding.stats.tool_calls == {"search_spans": 1, "read_spans": 1, "record_finding": 1}
    assert finding.stats.n_steps == 2
    assert [e.payload["name"] for e in events(conn, "tool_call")] == ["search_spans", "read_spans", "record_finding"]


async def test_reasoning_content_sent_back(conn, small_nda):
    _, _, fake = await review_one(conn, small_nda, [search(), text_reply("hm"), record()])
    second, third = fake.calls[1]["messages"], fake.calls[2]["messages"]
    assert second[1]["role"] == "assistant" and second[1]["reasoning_content"] == "..."
    assert second[1]["tool_calls"][0]["function"]["name"] == "search_spans"
    assert [m.get("reasoning_content") for m in third if m["role"] == "assistant"] == ["...", "..."]


# --- a whole review -------------------------------------------------------------------


async def test_seventeen_items(conn, small_nda):
    fake = routed_llm({IDS[0]: [search(), record("Entailment", [6])], IDS[5]: [search(), record()]})
    findings, usage = await review_a1(conn, small_nda, CHECKLIST, CONFIG, fake)
    assert [f.item_id for f in findings] == IDS
    assert all(f.outcome == "verified" and f.stats is not None for f in findings)
    assert usage.n_calls == len(fake.calls) == 19

    # C1: the same system prompt for every call; C5: item 1 runs alone first
    assert len({c["system"] for c in fake.calls}) == 1
    assert [item_of(c) for c in fake.calls[:2]] == [IDS[0], IDS[0]]
    assert IDS[0] not in [item_of(c) for c in fake.calls[2:]]

    review_id = only_review_id(conn)
    rows = db.list_findings(conn, review_id)
    assert len(rows) == 17 and len({r.operation_id for r in rows}) == 17
    assert sorted(r.finding.item_id for r in rows) == sorted(IDS)
    assert all(i.status == "verified" for i in db.get_items(conn, review_id))
    assert db.get_review(conn, review_id).status == "completed"


async def test_review_contract_a1_without_conn(small_nda):
    result = await review_contract(small_nda, CHECKLIST, CONFIG, routed_llm({}))
    assert len(result.findings) == 17 and result.usage.n_calls == 17 and result.latency_s >= 0


async def test_review_contract_a1_with_conn(conn, small_nda):
    await review_contract(small_nda, CHECKLIST, CONFIG, routed_llm({}), conn=conn)
    assert len(db.list_findings(conn, only_review_id(conn))) == 17


async def test_review_contract_a2_not_implemented(small_nda):
    with pytest.raises(NotImplementedError):
        await review_contract(small_nda, CHECKLIST, CONFIG.model_copy(update={"method": "A2"}), FakeLLM([]))


# --- events ---------------------------------------------------------------------------


async def test_events_are_redacted(conn, small_nda):
    read_notice = tool_call_reply(("read_spans", {"span_ids": [9], "window": 0}),
                                  content=f"Contact {FAKE_EMAIL} or {FAKE_PHONE}.")
    _, _, fake = await review_one(conn, small_nda, [read_notice, record()])
    assert FAKE_EMAIL in fake.calls[1]["messages"][2]["content"]  # the model sees the original
    dumped = json.dumps([e.payload for e in db.list_events(conn, only_review_id(conn))], ensure_ascii=False)
    assert FAKE_EMAIL not in dumped and FAKE_PHONE not in dumped
    (tool_event,) = [e for e in events(conn, "tool_call") if e.payload["name"] == "read_spans"]
    assert tool_event.payload["result"] == "[S9] (§3) Notices go to [EMAIL] or [PHONE]."
    assert events(conn, "model_call")[0].payload["content"] == "Contact [EMAIL] or [PHONE]."


def stats_from_events(conn, item_id) -> AgentStats:
    calls = events(conn, "tool_call", item_id)
    return AgentStats(
        n_steps=max(e.payload["step"] for e in events(conn, "model_call", item_id)),
        tool_calls=dict(Counter(e.payload["name"] for e in calls)),
        n_tool_errors=sum(1 for e in calls if not e.payload["ok"] and e.payload["error_kind"] != "rejected"),
        n_rejected_records=sum(1 for e in calls if e.payload["error_kind"] == "rejected"),
        n_nudges=len(events(conn, "nudge", item_id)),
        n_length_retries=len(events(conn, "length_retry", item_id)),
    )


async def test_stats_match_events(conn, small_nda):
    script = [
        text_reply("thinking aloud"),
        tool_call_reply(("search_spans", {"query": "x", "top_k": 99}), ("lookup_definition", {"term": "Purpose"})),
        truncated(),
        tool_call_reply(("get_section", "{bad json"), ("no_such_tool", {})),
        record("Entailment", [99]),
        record("Contradiction", [5]),
    ]
    finding, _, _ = await review_one(conn, small_nda, script)
    assert finding.stats == AgentStats(
        n_steps=5, tool_calls={"search_spans": 1, "lookup_definition": 1, "get_section": 1,
                               "no_such_tool": 1, "record_finding": 2},
        n_tool_errors=3, n_rejected_records=1, n_nudges=1, n_length_retries=1,
    )
    assert stats_from_events(conn, IDS[0]) == finding.stats
    model_calls = events(conn, "model_call")
    assert len(model_calls) == 6
    assert set(model_calls[0].payload) == {"attempt", "step", "max_tokens", "finish_reason", "content",
                                           "reasoning_content", "tool_calls", "usage", "cost_usd"}
    assert set(model_calls[0].payload["usage"]) == {"input", "cache_hit", "output", "reasoning"}


def test_prompt_hash(monkeypatch):
    h = prompt_hash()
    assert len(h) == 12
    monkeypatch.setattr(agent, "NUDGE", agent.NUDGE + " ")
    assert prompt_hash() != h
