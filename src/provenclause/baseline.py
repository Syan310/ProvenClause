"""B0 baseline: one model call reviews the whole contract against all items.

No tools and no verifier. Every finding the model gives counts as "verified"
(implementation route section 4 #1).
"""

import hashlib
import json
import logging
from collections.abc import Awaitable

from openai.types.chat import ChatCompletion, ParsedChatCompletion
from pydantic import BaseModel

from provenclause.llm import LLM, ModelStopped, add_usage, call_with_length_retry, completion_time
from provenclause.models import Checklist, ChecklistItem, Contract, Finding, Label, ReviewConfig, Usage
from provenclause.parse import format_contract

logger = logging.getLogger(__name__)


class B0Item(BaseModel):
    item_id: str  # str, not Literal: unknown ids are dropped in post-processing
    label: Label
    span_ids: list[int]
    rationale: str


class B0Output(BaseModel):
    findings: list[B0Item]


SCHEMA_JSON = json.dumps(B0Output.model_json_schema(), sort_keys=True)

SYSTEM = f"""\
You review a non-disclosure agreement (NDA) against a checklist. For each checklist item, \
decide how the contract relates to the item's hypothesis.

Labels:
- Entailment: the contract supports the hypothesis.
- Contradiction: the contract states the opposite of the hypothesis.
- NotMentioned: the contract does not address the hypothesis.

Evidence rules:
- The contract is given one sentence per line. Each line starts with a sentence id, such as [S12].
- For Entailment and Contradiction, cite at least one sentence that the label depends on.
- For NotMentioned, cite no sentences: span_ids is an empty list.
- Cite only ids that appear in the contract. Write each id as an integer without the "S" \
(for [S12], write 12).

Output:
- Give exactly one finding for each checklist item. Use the item's id as item_id.
- rationale is one sentence.
- Reply with one JSON object that follows this JSON schema:
{SCHEMA_JSON}"""

USER_TEMPLATE = """\
Contract:
{contract}

Checklist items:
{items}"""

ITEM_TEMPLATE = """\
id: {id}
hypothesis: {hypothesis}
guidance: {guidance}"""


def prompt_hash() -> str:
    """First 12 hex digits of SHA-256 over the fixed prompt parts. It does not
    depend on the contract or the checklist, and it changes when the prompt does."""
    fixed = "\n\0\n".join([SYSTEM, USER_TEMPLATE, ITEM_TEMPLATE, SCHEMA_JSON])
    return hashlib.sha256(fixed.encode("utf-8")).hexdigest()[:12]


def build_user_message(contract: Contract, checklist: Checklist) -> str:
    items = "\n\n".join(
        ITEM_TEMPLATE.format(id=item.id, hypothesis=item.hypothesis, guidance=item.guidance.strip())
        for item in checklist.items
    )
    return USER_TEMPLATE.format(contract=format_contract(contract), items=items)


def no_answer(item: ChecklistItem, reason: str) -> Finding:
    """The finding for an item the model did not answer. label is a placeholder."""
    return Finding(
        item_id=item.id, label="NotMentioned", span_ids=[], rationale="",
        reason=reason, outcome="abstained",
    )


def to_findings(output: B0Output, checklist: Checklist) -> list[Finding]:
    """Keep the first answer per known item, fill missing items, keep checklist order.

    span_ids that do not exist in the contract are kept as-is; the metrics count them.
    """
    known = {item.id for item in checklist.items}
    by_id: dict[str, B0Item] = {}
    n_unknown = n_duplicate = 0
    for answer in output.findings:
        if answer.item_id not in known:
            n_unknown += 1
        elif answer.item_id in by_id:
            n_duplicate += 1
        else:
            by_id[answer.item_id] = answer
    if n_unknown or n_duplicate:
        logger.warning("B0 output: dropped %d unknown and %d duplicate findings", n_unknown, n_duplicate)

    findings = []
    for item in checklist.items:
        answer = by_id.get(item.id)
        if answer is None:
            findings.append(no_answer(item, "missing"))
        else:
            findings.append(Finding(
                item_id=item.id, label=answer.label, span_ids=answer.span_ids,
                rationale=answer.rationale, outcome="verified",
            ))
    return findings


async def review_b0(
    contract: Contract, checklist: Checklist, config: ReviewConfig, llm: LLM,
) -> tuple[list[Finding], Usage]:
    """API errors are not caught here; the runner handles them.

    A reply cut off at max_tokens is retried once with 2x max_tokens (C6). Both
    calls are billed. Refusals and parse errors are not retried.
    """
    messages = [{"role": "user", "content": build_user_message(contract, checklist)}]
    usage = Usage()

    def bill(completion: ChatCompletion) -> None:
        nonlocal usage
        usage = add_usage(usage, config.agent_model, completion.usage, completion_time(completion))

    def call(max_tokens: int) -> Awaitable[ParsedChatCompletion[B0Output]]:
        return llm.parse(
            model=config.agent_model, system=SYSTEM, messages=messages,
            output_format=B0Output, max_tokens=max_tokens, effort=config.effort,
        )

    try:
        response = await call_with_length_retry(call, config.max_tokens, on_truncated=bill)
    except ModelStopped as exc:
        bill(exc.completion)
        return [no_answer(item, exc.reason) for item in checklist.items], usage

    bill(response)
    return to_findings(response.choices[0].message.parsed, checklist), usage
