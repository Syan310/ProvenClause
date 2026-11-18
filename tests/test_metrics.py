"""Hand-worked examples for the metrics. Each expected value is computed in the comments."""

import pytest

from provenclause.eval.metrics import _latency_percentiles, compute_metrics, format_agent_metrics, format_table
from provenclause.models import AgentStats, Contract, Finding, ReviewResult, Span, Usage


def contract(cid, n_spans=5):
    spans = [Span(span_id=i, start=i, end=i + 1, text="x") for i in range(n_spans)]
    return Contract(contract_id=cid, source="contractnli", title="t", text="x" * n_spans, spans=spans)


def f(item_id, label, span_ids, reason=None):
    outcome = "abstained" if reason else "verified"
    return Finding(item_id=item_id, label=label, span_ids=span_ids, rationale="", reason=reason, outcome=outcome)


GOLD = {
    ("c1", "q1"): ("Entailment", [1, 2]),
    ("c1", "q2"): ("Contradiction", [3]),
    ("c1", "q3"): ("NotMentioned", []),
    ("c2", "q1"): ("Contradiction", [0]),
    ("c2", "q2"): ("Entailment", [4]),
    ("c2", "q3"): ("NotMentioned", []),
}

RESULTS = [
    ReviewResult(contract_id="c1", latency_s=10.0, findings=[
        f("q1", "Entailment", [1]),  # right
        f("q2", "Entailment", [3, 9]),  # wrong; span 9 does not exist
        f("q3", "NotMentioned", []),  # right
    ], usage=Usage(input_tokens=100, cache_read_input_tokens=300, output_tokens=50, reasoning_tokens=20,
                   n_calls=1, cost_usd=0.01)),
    ReviewResult(contract_id="c2", latency_s=20.0, findings=[
        f("q1", "Contradiction", [0, 2]),  # right
        f("q2", "Entailment", [4]),  # right
        # No answer. Its placeholder label equals the gold label, but it must count as wrong.
        f("q3", "NotMentioned", [], reason="refusal"),
    ], usage=Usage(input_tokens=500, cache_read_input_tokens=100, output_tokens=70, reasoning_tokens=30,
                   n_calls=1, cost_usd=0.03)),
]


@pytest.fixture(scope="module")
def m():
    return compute_metrics(RESULTS, GOLD, [contract("c1"), contract("c2")])


def test_counts_and_accuracy(m):
    assert m["n_contracts"] == 2 and m["n_items"] == 6
    assert m["accuracy"] == pytest.approx(4 / 6)


def test_per_label(m):
    # Entailment: tp 2, predicted 3, gold 2
    assert m["precision_entailment"] == pytest.approx(2 / 3)
    assert m["recall_entailment"] == pytest.approx(1.0)
    assert m["f1_entailment"] == pytest.approx(0.8)
    # Contradiction: tp 1, predicted 1, gold 2
    assert m["precision_contradiction"] == pytest.approx(1.0)
    assert m["recall_contradiction"] == pytest.approx(0.5)
    assert m["f1_contradiction"] == pytest.approx(2 / 3)
    # NotMentioned: tp 1 (the refusal does not count), predicted 1, gold 2
    assert m["recall_notmentioned"] == pytest.approx(0.5)
    assert m["f1_notmentioned"] == pytest.approx(2 / 3)


def test_paper_f1(m):
    # q1: F1 1.0; q2: no Contradiction predicted, F1 0; q3 has no gold Contradiction, left out
    assert m["f1_contradiction_paper_items"] == ["q1", "q2"]
    assert m["f1_contradiction_paper"] == pytest.approx(0.5)


def test_evidence(m):
    # E/C items: hits 1+1+1+1 = 4; predicted 1+2+2+1 = 6; gold 2+1+1+1 = 5
    assert m["evidence_precision"] == pytest.approx(4 / 6)
    assert m["evidence_recall"] == pytest.approx(4 / 5)


def test_verified_and_coverage(m):
    assert m["verified_precision"] == pytest.approx(4 / 5)
    assert m["coverage"] == pytest.approx(5 / 6)
    assert m["false_accepts"] == 1
    assert m["n_abstained_by_reason"] == {"refusal": 1}
    assert m["n_invalid_span_ids"] == 1


def test_cost_tokens_latency(m):
    assert m["cost_total_usd"] == pytest.approx(0.04)
    assert m["cost_per_contract_usd"] == pytest.approx(0.02)
    assert m["tokens"]["input_tokens"] == 600
    assert m["tokens"]["reasoning_tokens"] == 50
    assert m["cache_read_ratio"] == pytest.approx(400 / 1000)
    # two values 10 and 20: inclusive quantiles interpolate linearly
    assert m["latency_p50_s"] == pytest.approx(15.0)
    assert m["latency_p95_s"] == pytest.approx(19.5)


def test_latency_percentiles():
    assert _latency_percentiles([7.0]) == (7.0, 7.0)
    p50, p95 = _latency_percentiles([1.0, 2.0, 3.0, 4.0, 5.0])
    assert p50 == pytest.approx(3.0)
    assert p95 == pytest.approx(1 + 0.95 * 4)


def test_only_scores_contracts_in_results():
    m = compute_metrics(RESULTS[:1], GOLD, [contract("c1"), contract("c2")])
    assert m["n_items"] == 3
    assert m["accuracy"] == pytest.approx(2 / 3)
    assert m["latency_p50_s"] == m["latency_p95_s"] == 10.0


def test_format_table(m):
    table = format_table([("B0-F", m)])
    lines = table.splitlines()
    assert lines[0].startswith("| Config | Accuracy |")
    assert lines[2].startswith("| B0-F | 0.667 |")


# --- stage 3: Agent statistics ---------------------------------------------------


def test_agent_metrics():
    s1 = AgentStats(n_steps=2, tool_calls={"search_spans": 1, "record_finding": 1})
    s2 = AgentStats(n_steps=5, tool_calls={"search_spans": 2, "get_section": 1, "record_finding": 2},
                    n_tool_errors=1, n_rejected_records=1, n_nudges=1, n_length_retries=1)
    results = [ReviewResult(contract_id="c1", latency_s=1.0, usage=Usage(), findings=[
        f("q1", "Entailment", [1]).model_copy(update={"stats": s1}),
        f("q2", "Contradiction", [3]).model_copy(update={"stats": s2}),
        f("q3", "NotMentioned", []),  # no stats: not counted
    ])]
    agent = compute_metrics(results, GOLD, [contract("c1")])["agent"]
    assert agent["n_items_with_stats"] == 2
    assert agent["avg_steps"] == pytest.approx(3.5)
    assert agent["max_steps"] == 5
    assert agent["tool_calls_total"] == {"get_section": 1, "record_finding": 3, "search_spans": 3}
    assert agent["tool_calls_per_item"] == {"get_section": 0.5, "record_finding": 1.5, "search_spans": 1.5}
    assert (agent["n_tool_errors"], agent["n_rejected_records"], agent["n_nudges"], agent["n_length_retries"]) == (
        1, 1, 1, 1)
    assert format_agent_metrics(agent).startswith("agent: 2 items, steps avg 3.50 max 5")


def test_no_agent_part_for_b0(m):
    assert "agent" not in m


def test_budget_exceeded_is_no_answer():
    # q3's placeholder label equals the gold label, but it must count as wrong
    results = [ReviewResult(contract_id="c1", latency_s=1.0, usage=Usage(), findings=[
        f("q3", "NotMentioned", [], reason="budget_exceeded"),
    ])]
    m = compute_metrics(results, GOLD, [contract("c1")])
    assert m["accuracy"] == 0.0
    assert m["n_abstained_by_reason"] == {"budget_exceeded": 1}
