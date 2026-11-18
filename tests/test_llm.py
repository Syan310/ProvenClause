from datetime import UTC, datetime

import pytest
from pydantic import BaseModel

from provenclause.llm import (
    DeepSeekLLM, FakeLLM, ModelInterrupted, ModelStopped, add_usage, call_cost, call_with_length_retry,
    check_tool_completion, completion_time, is_peak, make_completion, text_reply, tool_call_reply,
)
from provenclause.models import Usage

SATURDAY_NOON = datetime(2026, 10, 24, 12, tzinfo=UTC)  # off-peak
MONDAY_2AM = datetime(2026, 10, 26, 2, tzinfo=UTC)  # peak


class Answer(BaseModel):
    value: int


def usage(**kw):
    return make_completion("{}", **kw).usage


def test_call_cost_off_peak():
    # 800 miss, 200 hit, 300 output on deepseek-flash at off-peak prices
    u = usage(prompt_tokens=1000, cache_hit_tokens=200, completion_tokens=300)
    expected = (800 * 0.15 + 200 * 0.003 + 300 * 0.60) / 1e6
    assert call_cost("deepseek-flash", u, SATURDAY_NOON) == pytest.approx(expected)


def test_call_cost_peak_pro():
    u = usage(prompt_tokens=1000, cache_hit_tokens=200, completion_tokens=300)
    expected = (800 * 1.32 + 200 * 0.044 + 300 * 3.96) / 1e6
    assert call_cost("deepseek-v4-pro", u, MONDAY_2AM) == pytest.approx(expected)


@pytest.mark.parametrize(("at", "peak"), [
    (datetime(2026, 10, 26, 0, 59, tzinfo=UTC), False),
    (datetime(2026, 10, 26, 1, 0, tzinfo=UTC), True),
    (datetime(2026, 10, 26, 3, 59, tzinfo=UTC), True),
    (datetime(2026, 10, 26, 4, 0, tzinfo=UTC), False),
    (datetime(2026, 10, 26, 6, 0, tzinfo=UTC), True),
    (datetime(2026, 10, 26, 10, 0, tzinfo=UTC), False),
    (datetime(2026, 10, 24, 2, 0, tzinfo=UTC), False),  # Saturday
    (datetime(2026, 10, 26, 10, 30, tzinfo=UTC), False),
])
def test_is_peak(at, peak):
    assert is_peak(at) is peak


def test_add_usage_sums():
    total = add_usage(Usage(), "deepseek-flash",
                      usage(prompt_tokens=1000, cache_hit_tokens=100, completion_tokens=50, reasoning_tokens=20),
                      SATURDAY_NOON)
    total = add_usage(total, "deepseek-flash", usage(prompt_tokens=500, completion_tokens=10), SATURDAY_NOON)
    assert total.input_tokens == 900 + 500
    assert total.cache_read_input_tokens == 100
    assert total.output_tokens == 60
    assert total.reasoning_tokens == 20
    assert total.cache_creation_input_tokens == 0
    assert total.n_calls == 2
    assert total.cost_usd == pytest.approx((1400 * 0.15 + 100 * 0.003 + 60 * 0.60) / 1e6)


def test_unknown_model_raises():
    with pytest.raises(ValueError, match="no price"):
        call_cost("claude-haiku-5-5", usage(), SATURDAY_NOON)


def test_completion_time():
    assert completion_time(make_completion("{}")) == SATURDAY_NOON


class FakeCompletions:
    def __init__(self, completion):
        self.completion = completion
        self.kwargs = None

    async def create(self, **kwargs):
        self.kwargs = kwargs
        return self.completion


class FakeClient:
    def __init__(self, completion):
        self.chat = type("Chat", (), {})()
        self.chat.completions = FakeCompletions(completion)


async def call(completion):
    client = FakeClient(completion)
    llm = DeepSeekLLM(client)
    result = await llm.parse(model="deepseek-flash", system="sys", messages=[{"role": "user", "content": "hi"}],
                             output_format=Answer, max_tokens=100, effort="high")
    return result, client.chat.completions.kwargs


async def test_deepseek_parse_ok():
    result, kwargs = await call(make_completion('{"value": 3}'))
    assert result.choices[0].message.parsed == Answer(value=3)
    assert result.usage.prompt_tokens == 1000
    assert kwargs["messages"][0] == {"role": "system", "content": "sys"}
    assert kwargs["response_format"] == {"type": "json_object"}
    assert kwargs["reasoning_effort"] == "high"
    assert "thinking" not in kwargs


@pytest.mark.parametrize(("completion", "reason"), [
    (make_completion(None, finish_reason="content_filter"), "refusal"),
    (make_completion('{"val', finish_reason="length"), "max_tokens"),
    (make_completion('{"other": 1}'), "parse_error"),
    (make_completion("not json"), "parse_error"),
    (make_completion(""), "parse_error"),
])
async def test_deepseek_model_stopped(completion, reason):
    with pytest.raises(ModelStopped) as info:
        await call(completion)
    assert info.value.reason == reason
    assert info.value.completion.usage.prompt_tokens == 1000  # kept for billing


async def test_deepseek_server_interrupt():
    with pytest.raises(ModelInterrupted):
        await call(make_completion(None, finish_reason="insufficient_system_resource"))


async def test_fake_llm_script():
    stop = ModelStopped("refusal", make_completion(None, finish_reason="content_filter"))
    fake = FakeLLM([Answer(value=1), stop, lambda req: Answer(value=len(req["system"]))], prompt_tokens=50)
    kw = dict(model="deepseek-flash", messages=[], output_format=Answer, max_tokens=10, effort="high")

    first = await fake.parse(system="s", **kw)
    assert first.choices[0].message.parsed.value == 1
    assert first.usage.prompt_tokens == 50
    with pytest.raises(ModelStopped):
        await fake.parse(system="s", **kw)
    third = await fake.parse(system="four", **kw)
    assert third.choices[0].message.parsed.value == 4
    assert [c["system"] for c in fake.calls] == ["s", "s", "four"]
    with pytest.raises(AssertionError, match="used up"):
        await fake.parse(system="s", **kw)


# ---------------------------------------------------------------- stage 3: create() and C6


@pytest.mark.parametrize("finish", ["stop", "tool_calls"])
def test_check_tool_completion_returns(finish):
    completion = make_completion("x", finish_reason=finish)
    assert check_tool_completion(completion) is completion


@pytest.mark.parametrize(("finish", "reason"), [("content_filter", "refusal"), ("length", "max_tokens")])
def test_check_tool_completion_stops(finish, reason):
    with pytest.raises(ModelStopped) as info:
        check_tool_completion(make_completion(None, finish_reason=finish))
    assert info.value.reason == reason


@pytest.mark.parametrize("finish", ["insufficient_system_resource", "aborted", "something_new"])
def test_check_tool_completion_interrupted(finish):
    with pytest.raises(ModelInterrupted):
        check_tool_completion(make_completion(None, finish_reason=finish))


async def test_deepseek_create_request():
    client = FakeClient(tool_call_reply(("search_spans", {"query": "term"})))
    llm = DeepSeekLLM(client)
    tools = [{"type": "function", "function": {"name": "search_spans", "parameters": {}}}]
    result = await llm.create(model="deepseek-flash", system="sys", messages=[{"role": "user", "content": "hi"}],
                              tools=tools, max_tokens=100, effort="high")
    assert result.choices[0].message.tool_calls[0].function.name == "search_spans"
    kwargs = client.chat.completions.kwargs
    assert kwargs["messages"][0] == {"role": "system", "content": "sys"}
    assert kwargs["tools"] == tools and kwargs["reasoning_effort"] == "high"
    for absent in ("tool_choice", "response_format", "thinking"):
        assert absent not in kwargs


CREATE_KW = dict(model="deepseek-flash", system="s", messages=[], tools=[], max_tokens=10, effort="high")


async def test_fake_create_replies():
    fake = FakeLLM([tool_call_reply(("a", {"x": 1}), ("b", "{not json")), text_reply("plain")])
    first = await fake.create(**CREATE_KW)
    calls = first.choices[0].message.tool_calls
    assert first.choices[0].finish_reason == "tool_calls"
    assert [(c.id, c.function.name, c.function.arguments) for c in calls] == [
        ("call_1", "a", '{"x": 1}'), ("call_2", "b", "{not json")]
    second = await fake.create(**CREATE_KW)
    assert second.choices[0].message.content == "plain"
    assert not second.choices[0].message.tool_calls
    assert "tools" in fake.calls[0]


async def test_fake_wrong_step_type():
    fake = FakeLLM([Answer(value=1), text_reply("x")])
    with pytest.raises(AssertionError, match="create"):
        await fake.create(**CREATE_KW)
    with pytest.raises(AssertionError, match="parse"):
        await fake.parse(model="deepseek-flash", system="s", messages=[], output_format=Answer,
                         max_tokens=10, effort="high")


def test_message_dump_keeps_reasoning_and_tool_calls():
    # The Agent sends each reply back with model_dump(exclude_none=True). If the SDK
    # stops keeping reasoning_content (an extra field), DeepSeek returns 400.
    message = tool_call_reply(("a", {"x": 1})).choices[0].message
    dumped = message.model_dump(exclude_none=True)
    assert dumped["reasoning_content"] == "..."
    assert dumped["tool_calls"][0]["function"] == {"name": "a", "arguments": '{"x": 1}'}
    assert "content" not in dumped


def truncated() -> ModelStopped:
    return ModelStopped("max_tokens", make_completion(None, finish_reason="length"))


class Calls:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.max_tokens: list[int] = []

    async def __call__(self, max_tokens):
        self.max_tokens.append(max_tokens)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


async def test_length_retry_then_ok():
    call, truncated_seen = Calls(truncated(), "ok"), []
    assert await call_with_length_retry(call, 100, on_truncated=truncated_seen.append) == "ok"
    assert call.max_tokens == [100, 200]
    assert len(truncated_seen) == 1


async def test_length_retry_twice_raises():
    call, truncated_seen = Calls(truncated(), truncated()), []
    with pytest.raises(ModelStopped) as info:
        await call_with_length_retry(call, 100, on_truncated=truncated_seen.append)
    assert info.value.reason == "max_tokens"
    assert call.max_tokens == [100, 200]
    assert len(truncated_seen) == 1  # the 2nd cut-off reply goes to the caller with the exception


async def test_length_retry_not_allowed():
    call, truncated_seen = Calls(truncated(), "ok"), []
    with pytest.raises(ModelStopped):
        await call_with_length_retry(call, 100, on_truncated=truncated_seen.append, allow_retry=lambda: False)
    assert call.max_tokens == [100] and len(truncated_seen) == 1


async def test_refusal_not_retried():
    call, truncated_seen = Calls(ModelStopped("refusal", make_completion(None)), "ok"), []
    with pytest.raises(ModelStopped):
        await call_with_length_retry(call, 100, on_truncated=truncated_seen.append)
    assert call.max_tokens == [100] and truncated_seen == []
