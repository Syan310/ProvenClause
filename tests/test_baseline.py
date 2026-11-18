import logging

import pytest

from provenclause import baseline
from provenclause.baseline import B0Item, B0Output, build_user_message, prompt_hash
from provenclause.checklist import load_checklist
from provenclause.llm import FakeLLM, fake_stopped
from provenclause.models import Contract, ReviewConfig, Span
from provenclause.review import review_contract

CHECKLIST = load_checklist("nda_17")
IDS = [item.id for item in CHECKLIST.items]
CONFIG = ReviewConfig(name="B0-F", method="B0", agent_model="deepseek-flash", effort="high")


def make_contract(contract_id="1", sentences=("Recipient shall keep it secret.", "This lasts 3 years.")):
    spans, text = [], ""
    for i, s in enumerate(sentences):
        start = len(text)
        text += s + " "
        spans.append(Span(span_id=i, start=start, end=start + len(s), text=s))
    return Contract(contract_id=contract_id, source="contractnli", title="t", text=text, spans=spans)


def answer(item_id, label="Entailment", span_ids=(0,)):
    return B0Item(item_id=item_id, label=label, span_ids=list(span_ids), rationale="r")


async def review(output, contract=None):
    fake = FakeLLM([output])
    result = await review_contract(contract or make_contract(), CHECKLIST, CONFIG, fake)
    return result, fake


async def test_seventeen_findings():
    result, fake = await review(B0Output(findings=[answer(i) for i in reversed(IDS)]))
    assert [f.item_id for f in result.findings] == IDS  # checklist order
    assert all(f.outcome == "verified" and f.reason is None for f in result.findings)
    assert result.usage.n_calls == 1
    assert result.usage.input_tokens == 1000
    assert result.usage.cost_usd > 0
    assert result.latency_s >= 0
    assert fake.calls[0]["model"] == "deepseek-flash"
    assert fake.calls[0]["effort"] == "high"


async def test_missing_item():
    result, _ = await review(B0Output(findings=[answer(i) for i in IDS[1:]]))
    first = result.findings[0]
    assert (first.item_id, first.reason, first.outcome) == (IDS[0], "missing", "abstained")
    assert first.label == "NotMentioned" and first.span_ids == []


async def test_duplicate_keeps_first_and_unknown_dropped(caplog):
    answers = [answer(i) for i in IDS] + [answer(IDS[0], "Contradiction"), answer("nda-99")]
    with caplog.at_level(logging.WARNING):
        result, _ = await review(B0Output(findings=answers))
    assert len(result.findings) == 17
    assert result.findings[0].label == "Entailment"
    assert "1 unknown and 1 duplicate" in caplog.text


async def test_invalid_span_id_kept():
    answers = [answer(IDS[0], span_ids=[0, 99])] + [answer(i) for i in IDS[1:]]
    result, _ = await review(B0Output(findings=answers))
    assert result.findings[0].span_ids == [0, 99]


@pytest.mark.parametrize("reason", ["refusal", "parse_error"])
async def test_model_stopped_abstains_all(reason):
    result, fake = await review(fake_stopped(reason, prompt_tokens=1000, completion_tokens=400))
    assert len(fake.calls) == 1  # not retried
    assert len(result.findings) == 17
    assert all(f.outcome == "abstained" and f.reason == reason for f in result.findings)
    assert result.usage.n_calls == 1
    assert result.usage.output_tokens == 400
    assert result.usage.cost_usd > 0


async def test_truncated_twice_abstains_all():
    cut = fake_stopped("max_tokens", prompt_tokens=1000, completion_tokens=400)
    fake = FakeLLM([cut, fake_stopped("max_tokens", prompt_tokens=1000, completion_tokens=800)])
    result = await review_contract(make_contract(), CHECKLIST, CONFIG, fake)
    assert [c["max_tokens"] for c in fake.calls] == [CONFIG.max_tokens, 2 * CONFIG.max_tokens]
    assert all(f.outcome == "abstained" and f.reason == "max_tokens" for f in result.findings)
    assert result.usage.n_calls == 2
    assert result.usage.output_tokens == 1200


async def test_truncated_once_then_ok():
    cut = fake_stopped("max_tokens", prompt_tokens=1000, completion_tokens=400)
    fake = FakeLLM([cut, B0Output(findings=[answer(i) for i in IDS])], prompt_tokens=1000, completion_tokens=200)
    result = await review_contract(make_contract(), CHECKLIST, CONFIG, fake)
    assert [c["max_tokens"] for c in fake.calls] == [CONFIG.max_tokens, 2 * CONFIG.max_tokens]
    assert fake.calls[0]["messages"] == fake.calls[1]["messages"]
    assert len(result.findings) == 17
    assert all(f.outcome == "verified" and f.reason is None for f in result.findings)
    assert result.usage.n_calls == 2
    assert result.usage.output_tokens == 600
    assert result.usage.input_tokens == 2000


def test_prompt_hash_unchanged():
    assert prompt_hash() == "50fb0745f4aa"


async def test_prompt_content():
    _, fake = await review(B0Output(findings=[]))
    user = fake.calls[0]["messages"][0]["content"]
    assert "[S0] Recipient shall keep it secret." in user
    assert "[S1] This lasts 3 years." in user
    for item in CHECKLIST.items:
        assert f"id: {item.id}" in user and item.hypothesis in user
    assert "JSON" in fake.calls[0]["system"]


def test_prompt_hash_ignores_contract_but_tracks_prompt(monkeypatch):
    h = prompt_hash()
    assert len(h) == 12
    assert build_user_message(make_contract(), CHECKLIST) != build_user_message(
        make_contract(sentences=("Other.",)), CHECKLIST)
    assert prompt_hash() == h  # the hash has no contract input at all
    monkeypatch.setattr(baseline, "SYSTEM", baseline.SYSTEM + " ")
    assert prompt_hash() != h


async def test_a2_not_implemented():
    config = CONFIG.model_copy(update={"method": "A2"})
    with pytest.raises(NotImplementedError):
        await review_contract(make_contract(), CHECKLIST, config, FakeLLM([]))
