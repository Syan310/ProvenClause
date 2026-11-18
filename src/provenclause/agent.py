"""M4 Agent loop (A1): one Agent loop per checklist item, with the M5 tools.

The system prompt is the general instructions plus the whole contract, the same
for all 17 items, so DeepSeek's prefix cache serves items 2-17 (C1). The first
item runs alone to fill the cache; the others then run with at most
config.concurrency at a time (C5).

One step is one model call (C3). A reply may call several tools; they run in
order. A recorded finding ends the item; tool calls after it in the same reply
do not run. A plain-text reply gets the NUDGE message and costs a step (C2),
because tool_choice "required" is not allowed in thinking mode. A cut-off reply
is retried once with 2x max_tokens in the same step (C6).

Two budgets: config.max_steps per item, and config.max_review_output_tokens over
the whole review (C4). Both are checked before each call; over either one, the
item is abstained with reason "budget_exceeded".

A refusal or a second cut-off abstains the item. A server interruption or an API
error is not the model's answer: the review becomes "failed" and the error goes
to the caller. Only Exception counts as failure; a BaseException (Ctrl+C,
cancellation, a killed process in tests) leaves the review in "reviewing" for M6.

Every model call and tool call is written to events, redacted (B4, B5).
"""

import asyncio
import hashlib
import json
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from openai.types.chat import ChatCompletion, ChatCompletionMessage

from provenclause import db
from provenclause.fsm import transition_item, transition_review
from provenclause.ledger import RecordResult, operation_id
from provenclause.llm import (
    LENGTH_RETRY_FACTOR, LLM, ModelInterrupted, ModelStopped, add_usage, call_cost, call_with_length_retry,
    completion_time, token_counts,
)
from provenclause.models import AgentStats, Checklist, ChecklistItem, Contract, Finding, ReviewConfig, Usage
from provenclause.parse import format_contract
from provenclause.tools import TOOL_SCHEMAS, ContractTools, redact_obj, run_tool

SYSTEM = """\
You review a non-disclosure agreement (NDA) against one checklist item at a time. Decide how the \
contract relates to the item's hypothesis.

Labels:
- Entailment: the contract supports the hypothesis.
- Contradiction: the contract states the opposite of the hypothesis.
- NotMentioned: the contract does not address the hypothesis.

Evidence rules:
- The contract is given below, one sentence per line. Each line starts with a sentence id, such as [S12].
- For Entailment and Contradiction, cite at least one sentence that the label depends on.
- For NotMentioned, cite no sentences: span_ids is an empty list.
- Cite only ids that appear in the contract. Write each id as an integer without the "S" \
(for [S12], write 12).

Tools:
- When a sentence uses a capitalized defined term, call lookup_definition to read its definition.
- When a sentence refers to another part of the contract, such as "Section 5", call get_section to read it.
- Look for exceptions and conditions, such as "except", "unless", "provided that" and "notwithstanding". \
They can change the label.
- Before you choose NotMentioned, call search_spans to make sure that no sentence addresses the hypothesis.
- read_spans returns sentences by id, with the sentences around them.

Answer:
- Record your answer with the record_finding tool. A plain-text answer is not recorded.
- rationale is one sentence.
- self_confidence is an integer from 0 to 100.
- If record_finding returns an error, fix the finding and call record_finding again."""

USER_TEMPLATE = """\
Checklist item
id: {id}
hypothesis: {hypothesis}
guidance: {guidance}

Decide the label for this item, then call record_finding."""

NUDGE = ("You have not recorded a finding. Call the record_finding tool now; "
         "a plain-text answer is not recorded.")


def prompt_hash() -> str:
    """First 12 hex digits of SHA-256 over the fixed prompt parts and the tool definitions."""
    fixed = "\n\0\n".join([SYSTEM, USER_TEMPLATE, NUDGE, json.dumps(TOOL_SCHEMAS, sort_keys=True)])
    return hashlib.sha256(fixed.encode("utf-8")).hexdigest()[:12]


def build_system(contract: Contract) -> str:
    """No time stamp, review id or item id: the 17 items share this exact prefix (C1)."""
    return SYSTEM + "\n\nContract:\n" + format_contract(contract)


def build_user(item: ChecklistItem) -> str:
    return USER_TEMPLATE.format(id=item.id, hypothesis=item.hypothesis, guidance=item.guidance.strip())


def assistant_dict(message: ChatCompletionMessage) -> dict[str, Any]:
    """The reply as a message to send back, with reasoning_content and tool_calls.

    DeepSeek returns 400 when reasoning_content is missing from an earlier turn
    of a conversation with tools. The SDK keeps it as an extra field, and
    model_dump() keeps extra fields (test_llm checks this).
    """
    message_dict = message.model_dump(exclude_none=True)
    if "tool_calls" not in message_dict:
        message_dict.setdefault("content", "")
    return message_dict


# --- shared state of one review --------------------------------------------------


@dataclass
class ReviewContext:
    conn: sqlite3.Connection
    review_id: str
    contract: Contract
    config: ReviewConfig
    llm: LLM
    tools: ContractTools
    system: str
    usage: Usage = field(default_factory=Usage)  # every call of this review, cut-off calls included

    def bill(self, completion: ChatCompletion) -> None:
        # All items run in the event loop's thread, so no lock is needed.
        self.usage = add_usage(self.usage, self.config.agent_model, completion.usage, completion_time(completion))

    def budget_left(self) -> bool:
        """C4. Checked before a call; a call already running may go a little over."""
        return self.usage.output_tokens < self.config.max_review_output_tokens


def log_event(ctx: ReviewContext, type: str, payload: dict[str, Any], item_id: str | None = None) -> None:
    """The only way the Agent writes events: redacted first (B4)."""
    db.add_event(ctx.conn, ctx.review_id, type, redact_obj(payload), item_id=item_id)


def _model_call_payload(
    ctx: ReviewContext, completion: ChatCompletion, attempt: int, step: int, max_tokens: int,
) -> dict[str, Any]:
    choice = completion.choices[0]
    message = choice.message
    miss, hit, output, reasoning = token_counts(completion.usage)
    return {
        "attempt": attempt, "step": step, "max_tokens": max_tokens,
        "finish_reason": choice.finish_reason,
        "content": message.content,
        "reasoning_content": getattr(message, "reasoning_content", None),
        "tool_calls": [
            {"id": c.id, "name": c.function.name, "arguments": c.function.arguments}
            for c in message.tool_calls or []
        ],
        "usage": {"input": miss, "cache_hit": hit, "output": output, "reasoning": reasoning},
        "cost_usd": call_cost(ctx.config.agent_model, completion.usage, completion_time(completion)),
    }


# --- ending an item ------------------------------------------------------------------


def abstain(
    ctx: ReviewContext, item_id: str, attempt: int, reason: str, *, step: int, kind: str | None = None,
) -> None:
    """Write a placeholder finding with the reason and move drafting -> abstained, in one transaction."""
    finding = Finding(item_id=item_id, label="NotMentioned", span_ids=[], rationale="",
                      reason=reason, outcome="abstained")
    with db.transaction(ctx.conn):
        db.insert_finding(ctx.conn, operation_id(ctx.review_id, item_id, attempt), ctx.review_id, attempt, finding)
        transition_item(ctx.conn, ctx.review_id, item_id, "drafting", "abstained")
        if reason == "budget_exceeded":
            log_event(ctx, "budget_exceeded", {
                "attempt": attempt, "step": step, "kind": kind,
                "review_output_tokens": ctx.usage.output_tokens,
            }, item_id)


def finish_verified(ctx: ReviewContext, item_id: str, record: RecordResult) -> None:
    """A1 has no verifier: drafting -> verifying -> verified, in one transaction.
    Stage 4's A2 puts the verifier at "verifying"."""
    with db.transaction(ctx.conn):
        transition_item(ctx.conn, ctx.review_id, item_id, "drafting", "verifying")
        transition_item(ctx.conn, ctx.review_id, item_id, "verifying", "verified")
        db.set_finding_outcome(ctx.conn, record.operation_id, "verified")


# --- one item ------------------------------------------------------------------------


async def run_item(ctx: ReviewContext, item: ChecklistItem) -> AgentStats:
    config = ctx.config
    attempt = transition_item(ctx.conn, ctx.review_id, item.id, "pending", "drafting").attempt
    messages: list[dict[str, Any]] = [{"role": "user", "content": build_user(item)}]
    stats = AgentStats()
    tool_counts: Counter[str] = Counter()

    for step in range(1, config.max_steps + 1):
        if not ctx.budget_left():
            abstain(ctx, item.id, attempt, "budget_exceeded", step=step, kind="review_tokens")
            return stats
        stats.n_steps = step
        sent_max_tokens = config.max_tokens
        billed: list[ChatCompletion] = []

        def call(max_tokens: int):
            nonlocal sent_max_tokens
            sent_max_tokens = max_tokens
            return ctx.llm.create(model=config.agent_model, system=ctx.system, messages=messages,
                                  tools=TOOL_SCHEMAS, max_tokens=max_tokens, effort=config.effort)

        def on_truncated(completion: ChatCompletion) -> None:
            ctx.bill(completion)
            billed.append(completion)
            log_event(ctx, "model_call", _model_call_payload(ctx, completion, attempt, step, sent_max_tokens), item.id)
            if ctx.budget_left():  # the same check as allow_retry: the retry happens
                stats.n_length_retries += 1
                log_event(ctx, "length_retry", {
                    "attempt": attempt, "step": step, "max_tokens_from": sent_max_tokens,
                    "max_tokens_to": sent_max_tokens * LENGTH_RETRY_FACTOR,
                }, item.id)

        try:
            completion = await call_with_length_retry(
                call, config.max_tokens, on_truncated=on_truncated, allow_retry=ctx.budget_left,
            )
        except (ModelStopped, ModelInterrupted) as exc:
            if not any(exc.completion is c for c in billed):  # not billed yet by on_truncated
                ctx.bill(exc.completion)
                log_event(ctx, "model_call",
                          _model_call_payload(ctx, exc.completion, attempt, step, sent_max_tokens), item.id)
            if isinstance(exc, ModelInterrupted):
                raise  # a server problem, not the model's answer: the review fails
            abstain(ctx, item.id, attempt, exc.reason, step=step)
            return stats

        ctx.bill(completion)
        log_event(ctx, "model_call", _model_call_payload(ctx, completion, attempt, step, sent_max_tokens), item.id)
        message = completion.choices[0].message
        messages.append(assistant_dict(message))

        if not message.tool_calls:  # C2
            messages.append({"role": "user", "content": NUDGE})
            stats.n_nudges += 1
            log_event(ctx, "nudge", {"attempt": attempt, "step": step}, item.id)
            continue

        for tool_call in message.tool_calls:
            name, arguments = tool_call.function.name, tool_call.function.arguments
            outcome = await run_tool(ctx.tools, ctx.conn, ctx.review_id, item.id, name, arguments)
            tool_counts[name] += 1
            stats.tool_calls = dict(tool_counts)
            if outcome.error_kind == "rejected":
                stats.n_rejected_records += 1
            elif not outcome.ok:
                stats.n_tool_errors += 1
            log_event(ctx, "tool_call", {
                "attempt": attempt, "step": step, "tool_call_id": tool_call.id, "name": name,
                "arguments": arguments, "ok": outcome.ok, "error_kind": outcome.error_kind,
                "result": outcome.text, "duration_ms": outcome.duration_ms,
            }, item.id)
            if outcome.ok and outcome.record is not None:
                finish_verified(ctx, item.id, outcome.record)
                return stats  # later tool calls in this reply do not run
            messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": outcome.text})

    abstain(ctx, item.id, attempt, "budget_exceeded", step=config.max_steps, kind="steps")
    return stats


# --- one review ----------------------------------------------------------------------


def _first_leaf(exc: BaseException) -> BaseException:
    while isinstance(exc, BaseExceptionGroup):
        exc = exc.exceptions[0]
    return exc


async def run_review(
    conn: sqlite3.Connection, review_id: str, contract: Contract, checklist: Checklist,
    config: ReviewConfig, llm: LLM,
) -> tuple[list[Finding], Usage]:
    """Run the items that are "pending", then complete the review. M6's resume_review() calls it too.

    Returns one finding per checklist item, in checklist order: the finding of
    the item's current attempt. Items that ended before this call have stats None.
    """
    ctx = ReviewContext(conn=conn, review_id=review_id, contract=contract, config=config, llm=llm,
                        tools=ContractTools(contract), system=build_system(contract))
    pending = {row.item_id for row in db.get_items(conn, review_id) if row.status == "pending"}
    items = [item for item in checklist.items if item.id in pending]
    stats: dict[str, AgentStats] = {}

    async def run_limited(item: ChecklistItem, semaphore: asyncio.Semaphore) -> None:
        async with semaphore:
            stats[item.id] = await run_item(ctx, item)

    try:
        if items:
            stats[items[0].id] = await run_item(ctx, items[0])  # C5: fills the prefix cache first
            semaphore = asyncio.Semaphore(config.concurrency)
            async with asyncio.TaskGroup() as group:
                for item in items[1:]:
                    group.create_task(run_limited(item, semaphore))
        transition_review(conn, review_id, "reviewing", "completed")
    except Exception as exc:
        # The TaskGroup cancelled the other items; they stay in "drafting".
        first = _first_leaf(exc)
        transition_review(conn, review_id, "reviewing", "failed", error=f"{type(first).__name__}: {first}")
        if first is exc:
            raise
        raise first from exc  # the runner logs the type of the real error, not ExceptionGroup

    findings = []
    for item in checklist.items:
        row = db.get_item(conn, review_id, item.id)
        stored = db.get_finding(conn, operation_id(review_id, item.id, row.attempt))
        findings.append(stored.finding.model_copy(update={"stats": stats.get(item.id)}))
    return findings, ctx.usage


async def review_a1(
    conn: sqlite3.Connection, contract: Contract, checklist: Checklist, config: ReviewConfig, llm: LLM,
) -> tuple[list[Finding], Usage]:
    """save_contract -> create_review -> created -> parsing -> reviewing -> run_review().

    "parsing" does nothing here: load_contractnli() has already annotated the
    contract. Stage 5 parses PDFs at this step. Latency is measured by the caller.
    """
    db.save_contract(conn, contract)
    review_id = db.create_review(conn, contract.contract_id, checklist, config)
    transition_review(conn, review_id, "created", "parsing")
    transition_review(conn, review_id, "parsing", "reviewing")
    return await run_review(conn, review_id, contract, checklist, config, llm)
