import re
from typing import Any

import pytest
from openai.types.chat import ChatCompletion

from provenclause.llm import FakeLLM, tool_call_reply
from provenclause.models import Contract, Span
from provenclause.parse import CONTRACTNLI_DIR, annotate_contract

# A hand-written NDA for stage 3: definitions, a "Section 2" reference, an
# "except" clause, a fake e-mail address and a fake phone number.
SMALL_NDA = [
    "MUTUAL NON-DISCLOSURE AGREEMENT",
    "1. Definitions",
    '"Confidential Information" means all non-public information disclosed by either party.',
    '"Representatives" means the employees and advisors of a party.',
    "2. Obligations",
    "The Recipient shall not disclose Confidential Information to any third party, "
    "except to its Representatives who need to know it.",
    "The Recipient shall return or destroy all Confidential Information within 30 days after a written request.",
    "3. Term",
    "The obligations in Section 2 survive for 3 years after termination.",
    "Notices go to jane.doe@acme-corp.com or +1 (212) 555-0100.",
    "This Agreement is governed by the laws of New York.",
]
FAKE_EMAIL = "jane.doe@acme-corp.com"
FAKE_PHONE = "+1 (212) 555-0100"


def make_contract(sentences: list[str], contract_id: str = "nda-small") -> Contract:
    spans, pos = [], 0
    for i, text in enumerate(sentences):
        spans.append(Span(span_id=i, start=pos, end=pos + len(text), text=text))
        pos += len(text) + 1
    return annotate_contract(Contract(
        contract_id=contract_id, source="contractnli", title="Small NDA",
        text="\n".join(sentences), spans=spans,
    ))


@pytest.fixture
def small_nda() -> Contract:
    return make_contract(SMALL_NDA)


# --- FakeLLM scripts for the Agent ------------------------------------------------


def record(label: str = "NotMentioned", span_ids=(), confidence: int = 80, **usage: Any) -> ChatCompletion:
    """A reply that calls record_finding once."""
    return tool_call_reply(("record_finding", {
        "label": label, "span_ids": list(span_ids), "rationale": "r", "self_confidence": confidence,
    }), **usage)


def item_of(request: dict[str, Any]) -> str:
    """The checklist item id of an Agent request, from its first user message."""
    return re.search(r"^id: (\S+)$", request["messages"][0]["content"], re.MULTILINE).group(1)


def routed_llm(by_item: dict[str, list], default: Any = None) -> FakeLLM:
    """A FakeLLM whose replies depend on the checklist item, not on the call order.

    Items run concurrently, so the order of calls is not fixed. by_item maps an
    item id to its replies in order; every call for another item gets default,
    which is record() when not given.
    """
    queues = {item_id: list(steps) for item_id, steps in by_item.items()}

    def route(request: dict[str, Any]):
        item_id = item_of(request)
        if item_id not in queues:
            return default if default is not None else record()
        if not queues[item_id]:
            raise AssertionError(f"no reply left for {item_id}")
        step = queues[item_id].pop(0)
        return step(request) if callable(step) and not isinstance(step, (ChatCompletion, BaseException)) else step

    return FakeLLM([route] * 1000)


@pytest.fixture(scope="session")
def contractnli_available() -> None:
    if not (CONTRACTNLI_DIR / "test.json").exists():
        pytest.skip("ContractNLI data not downloaded (see README)")
