"""M7 metrics (detailed design section 8.1). Pure functions: no model calls, no files.

Only the contracts in the results are scored. A finding whose reason is in
NO_ANSWER_REASONS is "no answer": a prediction that matches no gold label.
"""

import statistics
from collections import Counter, defaultdict
from typing import Any

from provenclause.eval.dataset import Gold
from provenclause.models import NO_ANSWER_REASONS, Contract, Label, ReviewResult

LABELS: tuple[Label, ...] = ("Entailment", "Contradiction", "NotMentioned")


def _ratio(num: float, den: float) -> float:
    return num / den if den else 0.0


def _prf(pairs: list[tuple[str | None, str]], label: str) -> tuple[float, float, float]:
    """Precision, recall and F1 of one label over (predicted, gold) pairs."""
    tp = sum(1 for p, g in pairs if p == label and g == label)
    n_pred = sum(1 for p, _ in pairs if p == label)
    n_gold = sum(1 for _, g in pairs if g == label)
    precision, recall = _ratio(tp, n_pred), _ratio(tp, n_gold)
    return precision, recall, _ratio(2 * precision * recall, precision + recall)


def _latency_percentiles(latencies: list[float]) -> tuple[float, float]:
    if len(latencies) == 1:
        return latencies[0], latencies[0]
    cuts = statistics.quantiles(latencies, n=100, method="inclusive")
    return cuts[49], cuts[94]


def compute_metrics(results: list[ReviewResult], gold: Gold, contracts: list[Contract]) -> dict[str, Any]:
    span_ids = {c.contract_id: {s.span_id for s in c.spans} for c in contracts}

    pairs: list[tuple[str | None, str]] = []  # (prediction or None for no answer, gold label)
    pairs_by_item: dict[str, list[tuple[str | None, str]]] = defaultdict(list)
    n_verified = n_verified_correct = 0
    evidence_hit = evidence_pred = evidence_gold = 0
    n_invalid_span_ids = 0
    reasons: Counter[str] = Counter()

    for result in results:
        valid_ids = span_ids[result.contract_id]
        for f in result.findings:
            gold_label, gold_spans = gold[(result.contract_id, f.item_id)]
            pred = None if f.reason in NO_ANSWER_REASONS else f.label
            pairs.append((pred, gold_label))
            pairs_by_item[f.item_id].append((pred, gold_label))

            if f.outcome == "verified":
                n_verified += 1
                n_verified_correct += pred == gold_label
            if f.reason is not None:
                reasons[f.reason] += 1
            n_invalid_span_ids += sum(1 for s in f.span_ids if s not in valid_ids)

            if gold_label != "NotMentioned":
                pred_set, gold_set = set(f.span_ids), set(gold_spans)
                evidence_hit += len(pred_set & gold_set)
                evidence_pred += len(pred_set)
                evidence_gold += len(gold_set)

    n_items = len(pairs)
    metrics: dict[str, Any] = {
        "n_contracts": len(results),
        "n_items": n_items,
        "accuracy": _ratio(sum(1 for p, g in pairs if p == g), n_items),
    }
    for label in LABELS:
        precision, recall, f1 = _prf(pairs, label)
        key = label.lower()
        metrics[f"precision_{key}"] = precision
        metrics[f"recall_{key}"] = recall
        metrics[f"f1_{key}"] = f1

    # Paper style: F1 per item, then the mean over items that have a gold Contradiction.
    paper_items = sorted(i for i, ps in pairs_by_item.items() if any(g == "Contradiction" for _, g in ps))
    metrics["f1_contradiction_paper"] = _ratio(
        sum(_prf(pairs_by_item[i], "Contradiction")[2] for i in paper_items), len(paper_items)
    )
    metrics["f1_contradiction_paper_items"] = paper_items

    metrics["evidence_precision"] = _ratio(evidence_hit, evidence_pred)
    metrics["evidence_recall"] = _ratio(evidence_hit, evidence_gold)
    metrics["verified_precision"] = _ratio(n_verified_correct, n_verified)
    metrics["coverage"] = _ratio(n_verified, n_items)
    metrics["false_accepts"] = n_verified - n_verified_correct
    metrics["n_abstained_by_reason"] = dict(sorted(reasons.items()))
    metrics["n_invalid_span_ids"] = n_invalid_span_ids

    cost = sum(r.usage.cost_usd for r in results)
    metrics["cost_total_usd"] = cost
    metrics["cost_per_contract_usd"] = _ratio(cost, len(results))
    p50, p95 = _latency_percentiles([r.latency_s for r in results]) if results else (0.0, 0.0)
    metrics["latency_p50_s"] = p50
    metrics["latency_p95_s"] = p95

    tokens = {
        key: sum(getattr(r.usage, key) for r in results)
        for key in ("input_tokens", "output_tokens", "cache_read_input_tokens",
                    "cache_creation_input_tokens", "reasoning_tokens")
    }
    metrics["tokens"] = tokens
    all_input = tokens["input_tokens"] + tokens["cache_read_input_tokens"] + tokens["cache_creation_input_tokens"]
    metrics["cache_read_ratio"] = _ratio(tokens["cache_read_input_tokens"], all_input)
    metrics["n_calls"] = sum(r.usage.n_calls for r in results)

    agent = agent_metrics(results)
    if agent is not None:
        metrics["agent"] = agent
    return metrics


def agent_metrics(results: list[ReviewResult]) -> dict[str, Any] | None:
    """What the Agent loops did (decision record 3.6 C7); None when no finding has stats (B0)."""
    stats = [f.stats for r in results for f in r.findings if f.stats is not None]
    if not stats:
        return None
    tool_calls: Counter[str] = Counter()
    for s in stats:
        tool_calls.update(s.tool_calls)
    return {
        "n_items_with_stats": len(stats),
        "avg_steps": statistics.fmean(s.n_steps for s in stats),
        "max_steps": max(s.n_steps for s in stats),
        "tool_calls_total": dict(sorted(tool_calls.items())),
        "tool_calls_per_item": {name: n / len(stats) for name, n in sorted(tool_calls.items())},
        "n_tool_errors": sum(s.n_tool_errors for s in stats),
        "n_rejected_records": sum(s.n_rejected_records for s in stats),
        "n_nudges": sum(s.n_nudges for s in stats),
        "n_length_retries": sum(s.n_length_retries for s in stats),
    }


def format_agent_metrics(agent: dict[str, Any]) -> str:
    """The agent part of the metrics as a few lines of text, printed after the table."""
    per_item = ", ".join(f"{name} {n:.2f}" for name, n in agent["tool_calls_per_item"].items())
    return "\n".join([
        f"agent: {agent['n_items_with_stats']} items, steps avg {agent['avg_steps']:.2f} max {agent['max_steps']}",
        f"tool calls per item: {per_item}",
        f"tool errors {agent['n_tool_errors']}, rejected records {agent['n_rejected_records']}, "
        f"nudges {agent['n_nudges']}, length retries {agent['n_length_retries']}",
    ])


TABLE_COLUMNS = [
    ("Accuracy", "accuracy", "{:.3f}"),
    ("F1 E", "f1_entailment", "{:.3f}"),
    ("F1 C", "f1_contradiction", "{:.3f}"),
    ("F1 N", "f1_notmentioned", "{:.3f}"),
    ("F1 C (paper)", "f1_contradiction_paper", "{:.3f}"),
    ("Evidence P", "evidence_precision", "{:.3f}"),
    ("Evidence R", "evidence_recall", "{:.3f}"),
    ("Verified P", "verified_precision", "{:.3f}"),
    ("Coverage", "coverage", "{:.3f}"),
    ("$/contract", "cost_per_contract_usd", "{:.4f}"),
    ("P50 s", "latency_p50_s", "{:.1f}"),
    ("P95 s", "latency_p95_s", "{:.1f}"),
]


def format_table(rows: list[tuple[str, dict[str, Any]]]) -> str:
    """A Markdown table with one row per (config name, metrics)."""
    header = ["Config", *(title for title, _, _ in TABLE_COLUMNS)]
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    for name, m in rows:
        cells = [name, *(fmt.format(m[key]) for _, key, fmt in TABLE_COLUMNS)]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)
