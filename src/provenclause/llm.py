"""Model calls (F6). This is the only module that calls the model API.

The models are DeepSeek's, called through its native OpenAI-compatible API with
the openai SDK. Stage 1 has parse(): JSON output, validated with Pydantic.
Stage 3 adds create() for tool calls.

DeepSeek has no schema-enforced structured output. parse() turns on JSON mode
(the reply is valid JSON), the prompt gives the schema, and Pydantic checks it.

create() gives the model tools. It does not set tool_choice: in thinking mode
"required" returns 400, so the Agent itself handles a reply without a tool call.
Strict mode is off (decision record 3.6 A2); tools.py validates the arguments.
"""

import asyncio
import json
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol, TypeVar

from openai import AsyncOpenAI
from openai.types import CompletionUsage
from openai.types.chat import ChatCompletion, ParsedChatCompletion
from pydantic import BaseModel, ValidationError

from provenclause.models import Usage

DEEPSEEK_BASE_URL = "https://api.deepseek.com"

T = TypeVar("T", bound=BaseModel)
R = TypeVar("R")

# C6: a reply cut off at max_tokens is retried once with this many times max_tokens.
LENGTH_RETRY_FACTOR = 2


class ModelStopped(Exception):
    """The model returned no usable answer. reason is in NO_ANSWER_REASONS.

    The call is still billed, so the exception keeps the completion for its usage.
    """

    def __init__(self, reason: str, completion: ChatCompletion):
        super().__init__(f"model stopped: {reason}")
        self.reason = reason
        self.completion = completion


class ModelInterrupted(RuntimeError):
    """DeepSeek stopped the request on its side (finish_reason
    "insufficient_system_resource" or "aborted"). It is not the model's answer,
    so callers treat it like an API error: the runner logs it and --resume retries.
    """

    def __init__(self, finish_reason: str, completion: ChatCompletion):
        super().__init__(f"request interrupted by the server: {finish_reason}")
        self.finish_reason = finish_reason
        self.completion = completion


class LLM(Protocol):
    async def parse(
        self, *, model: str, system: str, messages: list[dict],
        output_format: type[T], max_tokens: int, effort: str,
    ) -> ParsedChatCompletion[T]: ...

    async def create(
        self, *, model: str, system: str, messages: list[dict], tools: list[dict],
        max_tokens: int, effort: str,
    ) -> ChatCompletion: ...


def check_completion(completion: ChatCompletion, output_format: type[T]) -> ParsedChatCompletion[T]:
    """Check finish_reason, then validate the JSON reply against output_format."""
    choice = completion.choices[0]
    match choice.finish_reason:
        case "content_filter":
            raise ModelStopped("refusal", completion)
        case "length":
            raise ModelStopped("max_tokens", completion)
        case "insufficient_system_resource" | "aborted":
            raise ModelInterrupted(choice.finish_reason, completion)
        case "stop":
            pass
        case _:  # e.g. "tool_calls": no tools are given, so this is not an answer
            raise ModelStopped("parse_error", completion)

    content = choice.message.content
    if not content:  # DeepSeek's JSON mode may return empty content
        raise ModelStopped("parse_error", completion)
    try:
        parsed = output_format.model_validate_json(content)
    except ValidationError:
        raise ModelStopped("parse_error", completion) from None

    result = ParsedChatCompletion[output_format].construct(**completion.model_dump())
    result.choices[0].message.parsed = parsed
    return result


def check_tool_completion(completion: ChatCompletion) -> ChatCompletion:
    """Check finish_reason of a call with tools. A reply with or without tool
    calls is returned; the Agent decides what a plain-text reply means."""
    choice = completion.choices[0]
    match choice.finish_reason:
        case "stop" | "tool_calls":
            return completion
        case "content_filter":
            raise ModelStopped("refusal", completion)
        case "length":
            raise ModelStopped("max_tokens", completion)
        case _:  # "insufficient_system_resource", "aborted" or an unknown value
            raise ModelInterrupted(choice.finish_reason, completion)


async def call_with_length_retry(
    call: Callable[[int], Awaitable[R]], max_tokens: int, *,
    on_truncated: Callable[[ChatCompletion], None],
    allow_retry: Callable[[], bool] = lambda: True,
) -> R:
    """C6: call(max_tokens); when the reply is cut off, call once more with
    LENGTH_RETRY_FACTOR x max_tokens. B0 and the Agent both use it.

    The cut-off reply goes only to on_truncated (the caller bills it and logs it);
    it never enters the conversation. When allow_retry() is False (for example
    the review's token budget is used up), the ModelStopped is raised again.
    Whatever the second call raises, it is raised as-is.
    """
    try:
        return await call(max_tokens)
    except ModelStopped as exc:
        if exc.reason != "max_tokens":
            raise
        on_truncated(exc.completion)
        if not allow_retry():
            raise
    return await call(max_tokens * LENGTH_RETRY_FACTOR)


class DeepSeekLLM:
    def __init__(self, client: AsyncOpenAI | None = None):
        # Retries for 429, 5xx and network errors are done by the SDK.
        self.client = client or AsyncOpenAI(
            api_key=os.environ["DEEPSEEK_API_KEY"], base_url=DEEPSEEK_BASE_URL, max_retries=4,
        )

    async def parse(
        self, *, model: str, system: str, messages: list[dict],
        output_format: type[T], max_tokens: int, effort: str,
    ) -> ParsedChatCompletion[T]:
        # No "thinking" parameter: thinking is on by default for both models,
        # and reasoning_effort sets how much. DeepSeek maps medium and xhigh to high.
        completion = await self.client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": system}, *messages],
            response_format={"type": "json_object"},
            max_tokens=max_tokens,
            reasoning_effort=effort,
        )
        return check_completion(completion, output_format)

    async def create(
        self, *, model: str, system: str, messages: list[dict], tools: list[dict],
        max_tokens: int, effort: str,
    ) -> ChatCompletion:
        # No tool_choice (default "auto"), no response_format, no strict mode.
        completion = await self.client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": system}, *messages],
            tools=tools,
            max_tokens=max_tokens,
            reasoning_effort=effort,
        )
        return check_tool_completion(completion)


# ---------------------------------------------------------------- prices and cost


@dataclass(frozen=True)
class Price:  # USD per million tokens
    input: float  # prompt tokens that miss the cache
    output: float  # includes reasoning tokens
    cache_read: float  # prompt tokens that hit the cache


# From api-docs.deepseek.com/quick_start/pricing, checked 2026-10-21.
# DeepSeek has no cache-write charge.
PRICES: dict[str, dict[str, Price]] = {
    "deepseek-flash": {
        "off_peak": Price(input=0.15, output=0.60, cache_read=0.003),
        "peak": Price(input=0.30, output=1.20, cache_read=0.006),
    },
    "deepseek-v4-pro": {
        "off_peak": Price(input=0.66, output=1.98, cache_read=0.022),
        "peak": Price(input=1.32, output=3.96, cache_read=0.044),
    },
}


def is_peak(at: datetime) -> bool:
    """Peak hours: 01:00-04:00 and 06:00-10:00 UTC, Monday to Friday.

    DeepSeek also bills Chinese public holidays at the off-peak price. This
    function ignores holidays, so on a holiday the cost is overestimated.
    """
    at = at.astimezone(UTC)
    return at.weekday() < 5 and (1 <= at.hour < 4 or 6 <= at.hour < 10)


def price_for(model: str, at: datetime) -> Price:
    if model not in PRICES:
        raise ValueError(f"no price for model {model!r}; add it to llm.PRICES")
    return PRICES[model]["peak" if is_peak(at) else "off_peak"]


def completion_time(completion: ChatCompletion) -> datetime:
    """The server's timestamp of the call. It decides peak or off-peak."""
    return datetime.fromtimestamp(completion.created, UTC)


def token_counts(sdk_usage: CompletionUsage) -> tuple[int, int, int, int]:
    """(cache miss, cache hit, output, reasoning) from DeepSeek's usage object."""
    hit = getattr(sdk_usage, "prompt_cache_hit_tokens", None) or 0
    miss = getattr(sdk_usage, "prompt_cache_miss_tokens", None)
    if miss is None:
        miss = sdk_usage.prompt_tokens - hit
    details = sdk_usage.completion_tokens_details
    reasoning = (details.reasoning_tokens if details else None) or 0
    return miss, hit, sdk_usage.completion_tokens, reasoning


def call_cost(model: str, sdk_usage: CompletionUsage, at: datetime) -> float:
    price = price_for(model, at)
    miss, hit, output, _ = token_counts(sdk_usage)
    return (miss * price.input + hit * price.cache_read + output * price.output) / 1_000_000


def add_usage(total: Usage, model: str, sdk_usage: CompletionUsage, at: datetime) -> Usage:
    miss, hit, output, reasoning = token_counts(sdk_usage)
    return Usage(
        input_tokens=total.input_tokens + miss,
        output_tokens=total.output_tokens + output,
        cache_read_input_tokens=total.cache_read_input_tokens + hit,
        cache_creation_input_tokens=total.cache_creation_input_tokens,
        reasoning_tokens=total.reasoning_tokens + reasoning,
        n_calls=total.n_calls + 1,
        cost_usd=total.cost_usd + call_cost(model, sdk_usage, at),
    )


# ---------------------------------------------------------------- fake model for tests

# Saturday 2026-10-24 12:00 UTC: off-peak, so fake costs are fixed.
FAKE_CREATED = int(datetime(2026, 10, 24, 12, tzinfo=UTC).timestamp())


def make_completion(
    content: str | None, *, finish_reason: str = "stop", model: str = "deepseek-flash",
    prompt_tokens: int = 1000, cache_hit_tokens: int = 0, completion_tokens: int = 200,
    reasoning_tokens: int = 0, created: int = FAKE_CREATED, tool_calls: list[dict] | None = None,
) -> ChatCompletion:
    """Build a ChatCompletion shaped like a DeepSeek response.

    reasoning_content is always "...", so tests can check that it is sent back.
    """
    message: dict[str, Any] = {"role": "assistant", "content": content, "reasoning_content": "..."}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    return ChatCompletion.construct(
        id="fake", object="chat.completion", created=created, model=model,
        choices=[{"index": 0, "finish_reason": finish_reason, "message": message}],
        usage={
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "prompt_cache_hit_tokens": cache_hit_tokens,
            "prompt_cache_miss_tokens": prompt_tokens - cache_hit_tokens,
            "completion_tokens_details": {"reasoning_tokens": reasoning_tokens},
        },
    )


def tool_call_reply(*calls: tuple[str, dict | str], content: str | None = None, **usage: Any) -> ChatCompletion:
    """A reply that calls tools: finish_reason "tool_calls", ids "call_1", "call_2", ...

    Each call is (name, arguments). A str is sent as the raw arguments, so tests
    can give arguments that are not valid JSON.
    """
    tool_calls = [
        {"id": f"call_{n}", "type": "function",
         "function": {"name": name, "arguments": args if isinstance(args, str) else json.dumps(args)}}
        for n, (name, args) in enumerate(calls, start=1)
    ]
    return make_completion(content, finish_reason="tool_calls", tool_calls=tool_calls, **usage)


def text_reply(content: str, **usage: Any) -> ChatCompletion:
    """A plain-text reply: finish_reason "stop", no tool calls."""
    return make_completion(content, **usage)


ScriptStep = BaseModel | BaseException | Callable[[dict[str, Any]], BaseModel | BaseException]


class FakeLLM:
    """Returns scripted answers in call order. parse() and create() share one script.
    Each step is one of:

    1. for parse(): a Pydantic object, returned as a normal reply (finish_reason "stop");
    2. for create(): a ChatCompletion (see tool_call_reply() and text_reply()),
       checked with check_tool_completion();
    3. an exception instance (e.g. ModelStopped): raised;
    4. a function (request: dict) -> one of the above.
    """

    def __init__(self, script: list[ScriptStep], **usage: int):
        self.script = list(script)
        self.usage = usage  # keyword arguments for make_completion's token counts
        self.calls: list[dict[str, Any]] = []

    async def parse(
        self, *, model: str, system: str, messages: list[dict],
        output_format: type[T], max_tokens: int, effort: str,
    ) -> ParsedChatCompletion[T]:
        request = dict(model=model, system=system, messages=messages,
                       output_format=output_format, max_tokens=max_tokens, effort=effort)
        step = self._next_step(request)
        if isinstance(step, ChatCompletion):
            raise AssertionError(f"FakeLLM call {len(self.calls)} is parse(), but the script has a "
                                 "ChatCompletion, which is a create() step")
        completion = make_completion(step.model_dump_json(), model=model, **self.usage)
        return check_completion(completion, output_format)

    async def create(
        self, *, model: str, system: str, messages: list[dict], tools: list[dict],
        max_tokens: int, effort: str,
    ) -> ChatCompletion:
        # A copy of messages: the Agent appends to its list after the call.
        request = dict(model=model, system=system, messages=list(messages), tools=tools,
                       max_tokens=max_tokens, effort=effort)
        # A real call waits for the network, so other tasks get a turn. Without
        # this, concurrent items run one after another and a crash cannot cancel them.
        await asyncio.sleep(0)
        step = self._next_step(request)
        if not isinstance(step, ChatCompletion):
            raise AssertionError(f"FakeLLM call {len(self.calls)} is create(), but the script has a "
                                 f"{type(step).__name__}, which is a parse() step")
        return check_tool_completion(step)

    def _next_step(self, request: dict[str, Any]) -> BaseModel:
        self.calls.append(request)
        if not self.script:
            raise AssertionError(f"FakeLLM script is used up (call {len(self.calls)})")
        step = self.script.pop(0)
        if not isinstance(step, (BaseModel, BaseException)):
            step = step(request)
        if isinstance(step, BaseException):
            raise step
        return step


def fake_stopped(reason: str, **usage: int) -> ModelStopped:
    """A ModelStopped for a FakeLLM script, with usage to bill."""
    finish = {"refusal": "content_filter", "max_tokens": "length"}.get(reason, "stop")
    return ModelStopped(reason, make_completion(None, finish_reason=finish, **usage))
