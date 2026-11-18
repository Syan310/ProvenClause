# ProvenClause stage 3 build spec: per-item Agent

Written on 2026-10-25. Reader: the build agent. The user does not review the code line by line, but must be able to understand each module.

---

## 0. Read before you start

### 0.1 Read these files first

1. [CLAUDE.md](../CLAUDE.md): project rules.
2. [Technical decisions](decisions.md) Section 1.2 (DeepSeek constraints, 4 new rows added on 2026-10-25), Section 2.3 (global conventions), and Section 3.6 (stage 3 technology choices). **Do not break the global conventions.** This document gives the build details for Section 3.6. If the two do not agree, Section 3.6 is correct, and you must tell the user.
3. [Roadmap](roadmap.md) Section 2 (interfaces) and Section 3 "Stage 3".
4. [Detailed design](design.md) M3, M4, M5, M6, N3, and Section 9.3.
5. [Stage 2 spec](stage2-spec.md) Sections 4–6: the interfaces of `db.py`, `fsm.py`, and `ledger.py`.
6. Current code: all files under `src/provenclause/` and `tests/`.
7. DeepSeek docs: [Thinking mode](https://api-docs.deepseek.com/guides/thinking_mode) (the multi-turn example with tools) and [API reference](https://api-docs.deepseek.com/api/create-chat-completion). Read them again before you write `llm.py`, and make sure that the facts in Section 1.2 did not change.

### 0.2 Rules

1. **Real calls cost money.** Do not run `eval run` or `pytest -m live` before all offline tests pass. Before you run them, tell the user the number of calls and the estimated cost, and wait for the user to agree (see Section 11, checkpoint 3).
2. Do not change the decisions in Section 2.3 and Section 3.6. If you think a decision does not work, stop, tell the user why, and let the user decide.
3. Where this document says "default approach", you can do it directly. For a design question that this document does not cover, ask the user first.
4. **Do not use test or test-40.** For debugging and prompt tuning, look only at dev.
5. Add only 1 new runtime dependency: `uv add rank-bm25==0.2.2` (it brings numpy). Do not add other dependencies.
6. **Do not change the B0 prompt.** `baseline.prompt_hash()` must still be `50fb0745f4aa`. For B0, change only the handling of truncation (Section 4).
7. Do not push to GitHub, do not create tags, and do not delete files. Ask the user before you commit.
8. Before you print contract text to the console, call `sys.stdout.reconfigure(encoding="utf-8")` (see CLAUDE.md).

### 0.3 Stage 3 goals

Unlock A1-F, and answer "how much does per-item review improve the results":

1. Review the 17 checklist items separately. Each item is one Agent loop: the model can use 4 read-only tools to search the contract, and at the end it uses `record_finding` to record the finding.
2. At most 4 of the 17 items run at the same time. Item 1 runs alone first, so that the full contract text goes into the cache.
3. Write the findings, the states, and each model call and tool call to SQLite. Redact the logs.
4. After the process stops, M6 can find the items that did not complete and do them again. All 17 items are complete, with no duplicates.
5. Run A1-F once on dev, and compare it with B0-F.

---

## 1. Files to create and change

| File | Content | New / Change |
|---|---|---|
| `src/provenclause/models.py` | `AgentStats`, `Finding.stats`, `ReviewConfig.max_review_output_tokens`; add `budget_exceeded` to `NO_ANSWER_REASONS` | Change |
| `src/provenclause/llm.py` | `LLM.create()`, `DeepSeekLLM.create()`, `check_tool_completion()`, `call_with_length_retry()`; `FakeLLM.create()` and reply helper functions | Change |
| `src/provenclause/baseline.py` | On truncation, do 1 retry with doubled tokens (C6) | Change |
| `src/provenclause/db.py` | `set_finding_outcome()`, `list_reviews()` | Change |
| `src/provenclause/tools.py` | M5: BM25 index, 5 tools, argument models and JSON Schema, timeout, redaction | New |
| `src/provenclause/agent.py` | M4: prompt, per-item loop, review loop, budget, events | New |
| `src/provenclause/recovery.py` | M6: `recover()`, `resume_review()` | New |
| `src/provenclause/review.py` | Add a `conn` argument to `review_contract()`, connect A1 | Change |
| `src/provenclause/eval/run.py` | `prompt_hash` for A1; one `reviews.db` for each run | Change |
| `src/provenclause/eval/metrics.py` | Agent statistics (steps, tool calls, and others) | Change |
| `configs/a1-f.yaml`, `configs/a1-p.yaml` | A1 configs | New |
| `tests/test_tools.py`, `tests/test_agent.py`, `tests/test_recovery.py` | Test cases for M5, M4, M6 | New |
| `tests/test_llm.py`, `tests/test_baseline.py`, `tests/test_db.py`, `tests/test_run.py`, `tests/test_metrics.py`, `tests/test_models.py` | Test cases for the new features; change the B0 truncation test cases to the new rule | Change |
| `tests/test_live.py` | 1 real-call test for A1, marked `live` | Change |

Dependencies (no circular imports): `models` ← `llm`, `parse` ← `db` ← `fsm`, `ledger` ← `tools` ← `agent` ← `recovery`, `review`. `tools` does not import `agent`.

---

## 2. `models.py`

Do not change the current fields. Only add:

```python
NO_ANSWER_REASONS = frozenset({"missing", "refusal", "max_tokens", "parse_error", "budget_exceeded"})

class AgentStats(BaseModel):
    """What the Agent loop did for one item (C7). Not stored in the database."""
    n_steps: int = 0                    # model calls, not counting the length retry
    tool_calls: dict[str, int] = {}     # tool name -> calls, failed calls included
    n_tool_errors: int = 0              # bad arguments, unknown tool, timeout
    n_rejected_records: int = 0         # record_finding calls that validate_finding() rejected
    n_nudges: int = 0                   # C2 reminders after a plain-text reply
    n_length_retries: int = 0           # C6 retries with 2x max_tokens

class Finding(BaseModel):
    ...                                 # current fields do not change
    stats: AgentStats | None = None     # stage 3: A1 and A2 fill it, B0 is None

class ReviewConfig(BaseModel):
    ...                                 # current fields do not change
    max_review_output_tokens: int = 300_000   # C4: limit on total output tokens for one review
```

- Add `budget_exceeded` to `NO_ANSWER_REASONS` (Section 3.6, gap 2). B0 does not produce this reason, so the B0 scores do not change.
- Add 1 test case to `test_models.py`: an old `findings.jsonl` (with no `stats`) can still be read.

---

## 3. `llm.py`

### 3.1 `create()`

```python
class LLM(Protocol):
    async def parse(...) -> ParsedChatCompletion[T]: ...      # no change
    async def create(
        self, *, model: str, system: str, messages: list[dict], tools: list[dict],
        max_tokens: int, effort: str,
    ) -> ChatCompletion: ...

def check_tool_completion(completion: ChatCompletion) -> ChatCompletion
```

- `DeepSeekLLM.create()`: `messages=[{"role": "system", "content": system}, *messages]`, and pass `tools`, `max_tokens`, `reasoning_effort=effort`. **Do not pass** `tool_choice` (the default is `auto`; in thinking mode, `required` returns 400). Do not pass `response_format`. Do not turn on strict, and do not change `base_url`.
- `check_tool_completion()` handles each `finish_reason` as follows:

  | `finish_reason` | Handling |
  |---|---|
  | `stop`, `tool_calls` | Return the completion |
  | `content_filter` | Raise `ModelStopped("refusal", completion)` |
  | `length` | Raise `ModelStopped("max_tokens", completion)` |
  | `insufficient_system_resource`, `aborted`, other values | Raise `ModelInterrupted` |

- Do not check "did the model call a tool". When the model replies with text only, the Agent handles it (Section 6.4).

### 3.2 Retry with doubled tokens after truncation: `call_with_length_retry()`

The C6 rule. B0 and the Agent both use it:

```python
LENGTH_RETRY_FACTOR = 2

async def call_with_length_retry(
    call: Callable[[int], Awaitable[R]], max_tokens: int, *,
    on_truncated: Callable[[ChatCompletion], None],
    allow_retry: Callable[[], bool] = lambda: True,
) -> R
```

1. `await call(max_tokens)`.
2. If it raises `ModelStopped` with `reason == "max_tokens"`:
   1. Call `on_truncated(exc.completion)`. The caller uses it to count cost and write events.
   2. If `allow_retry()` is `False` (for example, the C4 budget is used up): raise this exception again.
   3. Otherwise, `await call(max_tokens * LENGTH_RETRY_FACTOR)`. Whatever this second call raises, raise it unchanged.
3. Raise other exceptions unchanged.

The truncated reply **does not go into the conversation history**: `call` uses the same `messages` each time, and the truncated reply occurs only in `on_truncated`.

### 3.3 `FakeLLM`

Add `create()` to the current `FakeLLM`. It uses the same script as `parse()` (A3):

```python
def tool_call_reply(*calls: tuple[str, dict], content: str | None = None, **usage) -> ChatCompletion
    # finish_reason "tool_calls"; each (name, arguments) makes one tool_call, with id "call_1", "call_2", ...
def text_reply(content: str, **usage) -> ChatCompletion
    # finish_reason "stop", no tool_calls
```

- Add the argument `tool_calls: list[dict] | None = None` to `make_completion()`. Keep `reasoning_content` as `"..."`. Tests use it to check "the reasoning content is sent back".
- `FakeLLM.create()`: record the request (add a `tools` key), and pop the next step. If the step is a `ChatCompletion`, pass it to `check_tool_completion()` and then return it. If it is an exception, raise it. If it is a function, call it first.
- **Type check:** `ChatCompletion` is also a Pydantic object. If `parse()` gets a `ChatCompletion` step, or `create()` gets a Pydantic object that is not a `ChatCompletion`, raise `AssertionError`. The message must say that the script has a step in the wrong place.
- In concurrency tests, the call order is not fixed. Default approach: put functions in the script. Each function reads the checklist item `id` from `request["messages"][0]["content"]` and returns the reply for that checklist item. Write this as a helper function in `tests/`.

---

## 4. B0 truncation handling (`baseline.py`)

`review_b0()` wraps `llm.parse()` in `call_with_length_retry()`:

- The first call uses `config.max_tokens`. After truncation, it calls 1 more time with 2 times the tokens. Add the usage of both calls to `Usage`. `n_calls` is 2.
- If the second call is also truncated: all 17 items are `abstained`, with reason `max_tokens` (the same as before).
- Refusal and `parse_error`: no retry, the same as before.
- Do not change `SYSTEM`, `USER_TEMPLATE`, or `ITEM_TEMPLATE`. `prompt_hash()` is still `50fb0745f4aa`.
- In the `parametrize` at line 70 of `test_baseline.py`, change the `max_tokens` test case: the script must give 2 truncations before B0 gives up. Also add a test case for "the first call is truncated, the second call succeeds".

---

## 5. M5 tools (`tools.py`)

### 5.1 Interface

```python
TOOL_TIMEOUT_S = 10.0
READ_ONLY_TOOLS = ("search_spans", "read_spans", "lookup_definition", "get_section")

class ToolOutcome(BaseModel):
    name: str
    ok: bool                          # False: bad arguments, unknown tool, timeout, rejected record
    text: str                         # what goes back to the model
    error_kind: str | None = None     # "arguments" | "unknown_tool" | "timeout" | "rejected"
    record: RecordResult | None = None   # only for record_finding
    duration_ms: float

class ContractTools:
    def __init__(self, contract: Contract): ...      # builds the BM25 index once
    def search_spans(self, query: str, top_k: int) -> str
    def read_spans(self, span_ids: list[int], window: int) -> str
    def lookup_definition(self, term: str) -> str
    def get_section(self, section: str) -> str

TOOL_SCHEMAS: list[dict]   # OpenAI "tools" format, built from the argument models

async def run_tool(
    tools: ContractTools, conn: sqlite3.Connection, review_id: str, item_id: str,
    name: str, arguments: str,
) -> ToolOutcome

def redact(text: str) -> str
def redact_obj(value: Any) -> Any    # redact() on every str inside dicts and lists
```

- `ContractTools` only reads the contract. It does not touch the database. Create 1 for each review, and the 17 items share it.
- `run_tool()` is the only place that runs tools: parse arguments → validate → run → measure time. It **does not write events**. The Agent writes events (Section 6.6).

### 5.2 Argument models and JSON Schema

Each tool has one Pydantic argument model with `ConfigDict(extra="forbid")`. `TOOL_SCHEMAS` uses `model_json_schema()` to make the argument part, so the schema and the validation always agree. Write the tool descriptions in English.

| Tool | Arguments | Constraints |
|---|---|---|
| `search_spans` | `query: str`, `top_k: int = 8` | `query` not empty; `1 ≤ top_k ≤ 20` |
| `read_spans` | `span_ids: list[int]`, `window: int = 1` | `1 ≤ len(span_ids) ≤ 10`; `0 ≤ window ≤ 3` |
| `lookup_definition` | `term: str` | Not empty |
| `get_section` | `section: str` | Not empty |
| `record_finding` | `label: Label`, `span_ids: list[int]`, `rationale: str`, `self_confidence: int` | `0 ≤ self_confidence ≤ 100`. No length constraint on `span_ids` (it is empty for NotMentioned) |

- Do not turn on strict (A2). If the arguments are not valid JSON, or a field is missing, extra, or out of range: `ok=False`, `error_kind="arguments"`, and `text` is `ERROR: invalid arguments for search_spans: ...`, with the field and value that Pydantic reports.
- If the tool name is not one of these 5: `error_kind="unknown_tool"`, and `text` lists the available tool names.
- `record_finding` has no `item_id` argument. The Agent sets the checklist item, so the model cannot record a different item.

### 5.3 The 4 read-only tools

**Result format (B3):** one sentence on each line, `[S{id}] (§{section}) {text}`. When `section` is `None`, leave out `(§...)`. Each line starts with `[S{id}]`, the same as the full contract text in the system prompt.

**`search_spans(query, top_k)`:**
- Use `rank_bm25.BM25Okapi` with default parameters. Tokenization: `re.findall(r"[a-z0-9]+", text.lower())`, then remove about 30 English stop words (`the`, `of`, `and`, `to`, `a`, `in`, `or`, `any`, `such`, `by`, `for`, `be`, `shall`, and others, written as a module constant). Do not do stemming.
- Return the top `top_k` sentences from the highest score to the lowest. Return only sentences with a score above 0. Add the score at the end of each line: `[S12] (§2) ... (score 7.31)`. If two scores are equal, the smaller ID comes first.
- If no sentence matches: `No sentence matches "<query>".`

**`read_spans(span_ids, window)`:**
- For each ID, return `window` sentences before it and `window` sentences after it. Merge them, remove duplicates, and sort by ID. Put no separator between consecutive sentences. Where sentences are not consecutive, add a `...` line.
- If an ID does not exist: `ok=False`, `error_kind="arguments"`, and `text` gives the IDs that do not exist and the valid range (written the same way as in `validate_finding()`).

**`lookup_definition(term)`:**
- First use `parse.definition_spans(contract, term)`. If it finds nothing, try these in order: remove a leading `the `; singular and plural (remove or add a final `s`). This is the "singular and plural" work that stage 2 left for stage 3.
- Return the definition sentences. If it finds nothing: `No definition of "<term>" found. Terms defined in this contract: "Agreement", "Confidential Information", ...` (in order of occurrence, with no duplicates, at most 40).

**`get_section(section)`:**
- Use `parse.section_spans(contract, section)` (it already handles `Section 2(a)`, and falls back from `2.9` to `2`).
- If the section has more than 40 sentences: return only the first 40, and write this as the last line: `... <N> more sentences in this section; use read_spans to read them.`
- If it finds nothing: `Section "<section>" not found. Sections in this contract: 1, 2, 2.1, ...` (in order of occurrence, with no duplicates). If the contract has no section numbers, write `This contract has no numbered sections; use search_spans.`

### 5.4 `record_finding`

1. Use the arguments to build `Finding(item_id=item_id, label=..., span_ids=..., rationale=..., self_confidence=...)`.
2. Call `ledger.record_finding(conn, contract, review_id, finding)`.
3. On success: `ok=True`, `text` is `Recorded.`, and `record` is the `RecordResult`. `duplicate=True` is also a success.
4. On rejection: `ok=False`, `error_kind="rejected"`, and `text` is `ERROR: the finding was not recorded: <errors joined by "; ">. Fix it and call record_finding again.`
5. Call `record_finding` directly in the event loop thread. Do not put it in a thread (B2). `run_tool()` needs `contract`, and reads it from `tools.contract`.

### 5.5 Timeout (B2)

- The 4 read-only tools: `await asyncio.wait_for(asyncio.to_thread(fn, **args), TOOL_TIMEOUT_S)`.
- On timeout: `ok=False`, `error_kind="timeout"`, and `text` is `ERROR: search_spans timed out after 10 s.` The thread cannot be stopped. It runs to the end in the background, and its result is discarded.
- In tests, use `monkeypatch` to set `TOOL_TIMEOUT_S` to 0.05 seconds, and make one tool call `time.sleep(0.5)`.

### 5.6 Redaction (B4)

`redact(text)` uses regular expressions compiled at module level. It replaces in the order below (Chinese ID numbers come before phones, so that they are not taken as phone numbers):

| Type | Replace with | Must replace | Must not replace |
|---|---|---|---|
| Email | `[EMAIL]` | `jane.doe@acme-corp.com` | `Section 2.06` |
| Chinese ID number (18 digits, the last one can be `X`) | `[ID]` | `11010519491231002X` | `[S123]`, `$1,000,000` |
| US SSN `ddd-dd-dddd` | `[ID]` | `123-45-6789` | Date `2026-10-21` |
| Phone: US format, international format with `+`, Chinese mobile number of 11 digits that starts with `1[3-9]` | `[PHONE]` | `+1 (212) 555-0100`, `212-555-0100`, `(212) 555-0100`, `+86 138 1234 5678`, `13812345678` | `1934`, `Securities Exchange Act of 1934`, `span_ids [12, 15]`, `10 days` |

- Use it only when you write events (Section 6.6). The tool results and the contract that the model sees are the original text.
- Each cell in the table is a test case in `test_tools.py`.

---

## 6. M4 Agent loop (`agent.py`)

### 6.1 Interface

```python
def prompt_hash() -> str          # first 12 characters of the SHA-256 of SYSTEM, USER_TEMPLATE, NUDGE, TOOL_SCHEMAS

async def review_a1(
    conn: sqlite3.Connection, contract: Contract, checklist: Checklist,
    config: ReviewConfig, llm: LLM,
) -> tuple[list[Finding], Usage]
    # save_contract → create_review → created → parsing → reviewing → run_review()

async def run_review(
    conn: sqlite3.Connection, review_id: str, contract: Contract, checklist: Checklist,
    config: ReviewConfig, llm: LLM,
) -> tuple[list[Finding], Usage]
    # runs only the items in state pending; M6 resume_review() also calls it
```

- The `parsing` step does nothing: `load_contractnli()` already called `annotate_contract()`. In stage 5, this step parses the PDF.
- `review_a1()` does not measure time. `review_contract()` calculates the latency (the same as for B0).

### 6.2 Prompt (C1)

Write all of it in English. Write it as module constants. `prompt_hash()` covers them and `TOOL_SCHEMAS`.

**system = `SYSTEM` + `"\n\nContract:\n"` + `format_contract(contract)`.** It is exactly the same for all 17 items of one contract. Do not put a timestamp, `review_id`, or checklist item ID in it. `SYSTEM` includes at least:

1. Task: review 1 checklist item at a time, and decide the relation between the contract and the hypothesis.
2. The definitions of the 3 labels, written the same way as in the B0 `SYSTEM`.
3. The evidence rules, the same as B0: write sentence IDs as integers; Entailment and Contradiction cite at least 1 sentence; NotMentioned cites no sentence.
4. How to use the tools: for a capitalized term, use `lookup_definition` to find its definition; for a reference like `Section 5`, use `get_section` to follow it; look for exceptions like `except`, `provided that`, `notwithstanding`; before you give NotMentioned, use `search_spans` to confirm.
5. Submission: the finding can be recorded only through `record_finding`. A text answer does not count. Write 1 sentence for `rationale`. `self_confidence` is an integer from 0 to 100.

**user = `USER_TEMPLATE`:**

```
Checklist item
id: {id}
hypothesis: {hypothesis}
guidance: {guidance}

Decide the label for this item, then call record_finding.
```

**`NUDGE` (C2):** `You have not recorded a finding. Call the record_finding tool now; a plain-text answer is not recorded.`

### 6.3 Shared state of one review

```python
@dataclass
class ReviewContext:
    conn: sqlite3.Connection
    review_id: str
    contract: Contract
    config: ReviewConfig
    llm: LLM
    tools: ContractTools
    system: str
    usage: Usage                      # total usage of this review; every call (truncated ones included) adds to it
    def bill(self, completion: ChatCompletion) -> None      # add_usage()
    def budget_left(self) -> bool     # usage.output_tokens < config.max_review_output_tokens
```

- All items run in the same event loop thread. `bill()` is synchronous and needs no lock.
- Check the budget **before** each call. A call that is in progress can make the total go a little above the limit. This is permitted. Write the actual maximum in the delivery report.

### 6.4 Per-item loop: `run_item()`

```
run_item(ctx, item) -> AgentStats:
    row = transition_item(pending → drafting)
    messages = [user message]
    stats = AgentStats()
    for step in 1 .. config.max_steps:
        if not ctx.budget_left():
            abstain(item, "budget_exceeded", kind="review_tokens"); return stats
        try:
            completion = await call_with_length_retry(
                lambda n: llm.create(..., messages=messages, tools=TOOL_SCHEMAS, max_tokens=n),
                config.max_tokens,
                on_truncated = bill + write model_call and length_retry events + stats.n_length_retries += 1,
                allow_retry = ctx.budget_left)
        except ModelStopped as e:        # refusal, or truncated again on the second call
            bill(e.completion); abstain(item, e.reason); return stats
        bill(completion); write model_call event; stats.n_steps = step
        message = completion.choices[0].message
        messages.append(assistant_dict(message))       # unchanged, with reasoning_content and tool_calls
        if not message.tool_calls:                     # C2
            messages.append({"role": "user", "content": NUDGE}); stats.n_nudges += 1
            write nudge event; continue
        for call in message.tool_calls:                # run in order
            outcome = await run_tool(...); update stats; write tool_call event
            if outcome.record is not None and outcome.ok:
                finish_verified(item, outcome.record); return stats     # the later calls do not run
            messages.append({"role": "tool", "tool_call_id": call.id, "content": outcome.text})
    abstain(item, "budget_exceeded", kind="steps")
    return stats
```

- **`assistant_dict(message)`:** convert the SDK message to a dict. It must keep `reasoning_content` (the SDK treats it as an extra field) and `tool_calls`, and remove keys whose value is `None`. On 2026-10-25, we confirmed this on the local openai SDK: `message.model_dump(exclude_none=True)` keeps `reasoning_content` and `tool_calls`. Lock this with a test, so that the test fails if the SDK behavior changes.
- **`abstain(item, reason, kind=None)`:** in one transaction: use the `operation_id` of the current `attempt` to write a placeholder finding row (`label="NotMentioned"`, `span_ids=[]`, `rationale=""`, `reason`, `outcome="abstained"`, with `db.insert_finding()`); `transition_item(drafting → abstained)`. For `budget_exceeded`, also write a `budget_exceeded` event. Its `payload` has `kind` (`steps` or `review_tokens`), `step`, and the total output tokens of this review.
- **`finish_verified(item, record)` (A1):** in one transaction: `drafting → verifying`; `verifying → verified`; `db.set_finding_outcome(conn, record.operation_id, "verified")`. In stage 4, A2 inserts the verifier here at `verifying`.
- When a record is rejected, the error goes back to the model as the tool result, and the loop continues. A rejected record does not use an `operation_id` (the stage 2 rule). After the model fixes it, the record can succeed in the same `attempt`.

### 6.5 Review loop: `run_review()`

```
run_review(...):
    ctx = ReviewContext(..., tools=ContractTools(contract), system=build_system(contract))
    items = [items in checklist with state pending], in checklist order
    stats = {}
    try:
        if items:
            stats[items[0].id] = await run_item(ctx, items[0])      # C5: cache warm-up
            sem = asyncio.Semaphore(config.concurrency)
            async with asyncio.TaskGroup() as tg:  one task for each other item, each task uses async with sem
        transition_review(reviewing → completed)
    except Exception as exc:
        transition_review(reviewing → failed, error=f"{type(exc).__name__}: {exc}")
        raise   # when TaskGroup raises ExceptionGroup, raise its first exception, so that the eval runner can record its type
    findings = the finding of the current attempt for each checklist item (db.get_finding(operation_id(...))), with stats attached (None if missing)
    return findings, ctx.usage
```

- **Treat only `Exception` as a failure (Section 3.6, gap 4).** `KeyboardInterrupt`, `asyncio.CancelledError`, and `Crash(BaseException)` in tests do not change the state. The review stays in `reviewing`, the same as when the process is killed.
- When one item raises an `Exception` (for example `ModelInterrupted`, or `APIError` after SDK retries), `TaskGroup` cancels the other items. The cancelled items stay in `drafting`, and the review is recorded as `failed` (C6). The eval runner records the error, and `--resume` runs the full contract again (D4).
- The returned findings are in checklist order, 17 of them.

### 6.6 Events (B4, B5)

All events go through `log_event(ctx, type, payload, item_id)`: first `tools.redact_obj(payload)`, then `db.add_event()`. The stage 2 event types do not change. New types:

| `type` | When to write | `payload` |
|---|---|---|
| `model_call` | After each model call, truncated ones included | `{"attempt", "step", "max_tokens", "finish_reason", "content", "reasoning_content", "tool_calls": [{"id", "name", "arguments"}], "usage": {"input", "cache_hit", "output", "reasoning"}, "cost_usd"}` |
| `length_retry` | When the Agent decides to retry after truncation | `{"attempt", "step", "max_tokens_from", "max_tokens_to"}` |
| `tool_call` | After each tool call | `{"attempt", "step", "tool_call_id", "name", "arguments", "ok", "error_kind", "result", "duration_ms"}` |
| `nudge` | When the Agent appends `NUDGE` | `{"attempt", "step"}` |
| `budget_exceeded` | When the step limit or the total budget is exceeded | `{"attempt", "step", "kind", "review_output_tokens"}` |
| `review_recovered` | When M6 recovers a review | `{"reset_items": [item_id, ...]}` |

- `arguments` stores the original string from the model. `result` stores the full text that goes to the model. Redact both.
- The `stats` of each item can also be calculated again from these events. `test_agent.py` uses 1 test case to check that the two agree.

---

## 7. M6 crash recovery (`recovery.py`)

```python
def recover(conn: sqlite3.Connection) -> list[str]
async def resume_review(
    conn: sqlite3.Connection, review_id: str, llm: LLM, *, checklist: Checklist | None = None,
) -> tuple[list[Finding], Usage]
```

**`recover()`:**
1. `db.list_reviews(conn, ("created", "parsing", "reviewing"))`. This includes `created` (Section 3.6, gap 6).
2. For each review: for each item in state `drafting` or `verifying`, call `transition_item(..., → "pending")` (`attempt + 1`, `retries` does not change). Do not change items that already ended.
3. Write one `review_recovered` event.
4. Return the `review_id` of these reviews, sorted by `created_at`.
- It changes only the database and does not call the model. If you call it 2 times in a row on the same database, the second call changes nothing (it only returns the same `review_id` again; it can also skip the event. Default approach: write no event when no item was reset).

**`resume_review()`:**
1. Read the review (`config` is in the `reviews` table) and the contract (`db.load_contract()`). If `checklist` is `None`, use `load_checklist(review.checklist_id)`.
2. Bring the state up to `reviewing`: `created → parsing → reviewing`, or `parsing → reviewing`.
3. If `config.method` is `A1`, call `agent.run_review()`. `A2` comes in stage 4. For now, raise `NotImplementedError`.
- The returned usage counts only the calls after the resume. For items that ended before the crash, `stats` is `None` (Section 3.6, gap 5).

**Crash after the finding is recorded but before the state changes (D3):** this item stays in `drafting`. `recover()` moves it back to `pending`, with `attempt + 1`, and the item is done again. The finding of the old `attempt` stays in the `findings` table. `run_review()` reads only the finding of the current `attempt`.

---

## 8. Additions to `db.py`

```python
def set_finding_outcome(
    conn, operation_id: str, outcome: Outcome, *,
    checks: dict[str, str] | None = None, support_score: int | None = None,
) -> None                          # raises KeyError if not found; stage 4 also uses it
def list_reviews(conn, statuses: Iterable[ReviewStatus]) -> list[ReviewRow]   # sorted by created_at
```

The table structure does not change. `SCHEMA_VERSION` is still 1. `AgentStats` is not stored in the database.

---

## 9. Entry point, evaluation, and config

### 9.1 `review.py`

```python
async def review_contract(
    contract: Contract, checklist: Checklist, config: ReviewConfig, llm: LLM,
    *, conn: sqlite3.Connection | None = None,
) -> ReviewResult
```

- `B0`: does not use `conn`. The behavior does not change.
- `A1`: if `conn` is `None`, use `db.connect(":memory:")` and close it after use. Call `agent.review_a1()`.
- `A2`: still raises `NotImplementedError` (stage 4).

### 9.2 `eval/run.py`

- `prompt_hash(config)`: for `A1`, return `agent.prompt_hash()`.
- If `config.method != "B0"`, open `db.connect(run_dir / "reviews.db")`, pass it to each `review_contract(..., conn=conn)`, and close it at the end (`finally`). 4 contracts run at the same time and share this 1 connection: all database calls are synchronous and run in the same thread.
- `--resume`: a contract that is half done is done again in full (D4). Old reviews stay in the database (in state `reviewing` or `failed`). Do not handle them. `save_contract()` does not change a contract that is already there.
- Nothing else changes.

### 9.3 `eval/metrics.py`

If at least 1 finding has `stats`, `metrics["agent"]` includes:

| Key | Content |
|---|---|
| `n_items_with_stats` | Number of findings with `stats` |
| `avg_steps`, `max_steps` | Mean and maximum steps for each item |
| `tool_calls_total`, `tool_calls_per_item` | Total calls of each tool, and mean calls for each item |
| `n_tool_errors`, `n_rejected_records`, `n_nudges`, `n_length_retries` | Totals |

After `run_eval()` prints the result table, it prints these lines. `cache_read_ratio` already exists. Do not change it.

### 9.4 Config

```yaml
# configs/a1-f.yaml
# A1 with DeepSeek Flash: one Agent loop per checklist item, with tools, no verifier.
name: A1-F
method: A1
agent_model: deepseek-flash
effort: high
max_tokens: 32000              # per model call; a cut-off call is retried once with 2x (C6)
max_steps: 8
concurrency: 4
max_review_output_tokens: 300000   # C4; tune after the first 5 dev contracts
```

`configs/a1-p.yaml` is the same, with `name: A1-P` and `agent_model: deepseek-v4-pro`. Stage 3 does not run A1-P.

---

## 10. Tests

All tests use `FakeLLM` and cost no money. Tests that need data use the `contractnli_available` fixture. For the contract, use a small hand-written contract (about 10 sentences, with a definition, a `Section 2` reference, an `except` exception, 1 fake email, and 1 fake phone number). Put it in a fixture in `tests/conftest.py`.

| File | Minimum test content |
|---|---|
| `test_llm.py` | `check_tool_completion()`: each row of the table in Section 3.1; `FakeLLM.create()` returns `tool_call_reply()` and `text_reply()`; `parse()` and `create()` raise `AssertionError` when they get the wrong step type; `call_with_length_retry()`: the first call is truncated and the second succeeds, `on_truncated` is called 1 time, and the second `max_tokens` is 2 times the first; both calls are truncated, and it raises `ModelStopped("max_tokens")`; no retry when `allow_retry` is `False`; no retry on refusal |
| `test_baseline.py` | Success after 1 truncation: 17 normal findings, `n_calls` is 2, usage is the sum of the 2 calls; 2 truncations: 17 `abstained`, reason `max_tokens`; `prompt_hash()` is still `50fb0745f4aa` |
| `test_tools.py` | `search_spans`: the sentence with the query words comes first; `top_k` works; the message when nothing matches; `top_k=50` returns an argument error. `read_spans`: `window` works; no out-of-range read at the start and end of the contract; `...` when sentences are not consecutive; an ID that does not exist returns an error. `lookup_definition`: case, `the `, singular and plural; when nothing is found, it lists the defined terms. `get_section`: `"2(a)"` finds `"2"`; truncation and a message when there are more than 40 sentences; when nothing is found, it lists the section numbers. Arguments: 1 test case each for not JSON, missing field, extra field, unknown tool. Timeout (Section 5.5). `record_finding`: success; on rejection, `text` contains the `validate_finding()` errors. Redaction: each cell of the table in Section 5.6 |
| `test_agent.py` | Single item: `search_spans` → `record_finding`, the result is `verified`, `stats` is correct, 1 row in `findings`. The model replies with text only: `NUDGE` is appended, `n_nudges` is 1, and then the record succeeds. A rejected record that the model fixes: `n_rejected_records` is 1, and the record succeeds in the same `attempt`. **More than 8 steps: `abstained`, reason `budget_exceeded`**, and `findings` has 1 placeholder finding row. Total budget used up: the remaining items do not call the model again, and all are `abstained`, reason `budget_exceeded`. Retry after truncation succeeds; after 2 truncations, `abstained`, reason `max_tokens`. Refusal: `abstained`, reason `refusal`. `ModelInterrupted`: the review is `failed`, and the exception is raised. One reply with 2 tool calls: both run, each with 1 `tool` message; after `record_finding` succeeds, the later calls in the same reply do not run. **`reasoning_content` is sent back:** in the `messages` of the second call, the previous assistant message has `reasoning_content`. The `system` is exactly the same for all 17 items. All calls of item 1 come before the other items. **All 17 items end, each item has only 1 row in `findings`, and no `operation_id` is duplicated.** The events do not contain the original fake email and fake phone. The statistics calculated again from events agree with `stats` |
| `test_recovery.py` | **Crash:** `concurrency=1`, the first 5 items end normally, and the first call of item 6 raises `Crash(BaseException)`. After that: 5 items are in a terminal state, item 6 is `drafting`, the others are `pending`, and the review is `reviewing`. `recover()` returns this review, and item 6 becomes `pending`, with `attempt` 2 and `retries` 0. `resume_review()` runs to the end with a new `FakeLLM`: all 17 items are complete, the review is `completed`, each item has only 1 finding for the current `attempt`, and the findings of the first 5 items are not written again. **Recorded, state not changed:** raise `Crash` after the record succeeds and before `finish_verified()` (`monkeypatch`); after recovery, this item has 2 finding rows, and the final result uses the one from `attempt` 2. **A review in `created`** is recovered and runs to the end. Call `recover()` 2 times; the second call changes no item |
| `test_db.py` | `set_finding_outcome()` changes `outcome`; `KeyError` when not found. `list_reviews()` filters by state and sorts by time |
| `test_run.py` | A1 + `FakeLLM` runs 2 dev contracts: `reviews.db` exists, `findings.jsonl` has 2 rows, each contract has 17 findings, `metrics.json` has an `agent` part, and the `prompt_hash` in `manifest.json` is `agent.prompt_hash()` |
| `test_metrics.py` | Write 2 findings with `stats` by hand; each number in the `agent` part is correct; `budget_exceeded` counts as "no answer" |
| `test_live.py` | 1 `live` test case: A1-F reviews the first 2 items of contract 456. Skipped by default. **Do not run it yourself** |

---

## 11. Build order and checkpoints

1. `uv add rank-bm25==0.2.2`. Make the `models.py` changes. `uv run pytest` still passes in full (the 250 offline tests of stages 0–2).
2. `llm.py` (Section 3) and `test_llm.py`.
3. B0 truncation handling (Section 4) and `test_baseline.py`. Confirm that `prompt_hash()` does not change.
4. Additions to `db.py` (Section 8).
5. `tools.py` (Section 5) and `test_tools.py`.
6. The per-item loop in `agent.py` (Sections 6.2–6.4, 6.6). Test only a single item first.
7. The review loop in `agent.py` (Section 6.5), `review.py`, `test_agent.py`.
8. `recovery.py` and `test_recovery.py`.
9. `eval/run.py`, `eval/metrics.py`, config, `test_run.py`, `test_metrics.py`.
10. **Checkpoint 1:** `uv run pytest` passes in full. Report to the user the number of tests and the results of the offline part of Section 12.
11. **Checkpoint 2: after the user agrees, run 5 dev contracts.** First tell the user: 85 checklist items, an estimated 3–5 model calls for each item, about 250–450 calls in total, an estimated $0.2–0.3 (Flash, off-peak price; multiply by 2 at peak price). After the user agrees, run:
    ```bash
    uv run python -m provenclause.eval run --config configs/a1-f.yaml --split dev --limit 5
    ```
    Check: after item 2, `cache_read_input_tokens` is above 0 (if it is 0, check whether the system prompt has content that changes each time); there are no 400 errors; the actual cost and output tokens of each contract; whether any item hits `max_steps` or the total budget. From the measured values, suggest a value for `max_review_output_tokens`. The user decides whether to change it.
12. **Checkpoint 3: after the user agrees, run the full dev.** First multiply the measured cost of checkpoint 2 by 61 / 5, and tell the user the estimated cost. If you changed the prompt after checkpoint 2, `prompt_hash` changes. In that case, start a new run and do not use `--resume`.
13. Update the documents in Section 13.
14. CLAUDE.md workflow item 3 says to run `neat-freak` before a push. In stage 3, the user decides when to commit and push. Only give a reminder about this in your report.

---

## 12. Acceptance criteria

From roadmap Section 3 "Stage 3":

- [ ] Test (FakeLLM): all 17 items of one contract end, and `findings` has no duplicate findings.
- [ ] Test (FakeLLM): more than 8 steps gives the result `abstained` with reason `budget_exceeded`.
- [ ] Test: a tool result with a fake email and a fake phone number is replaced after it is written to `events`.
- [ ] Test (FakeLLM): raise an exception after 5 items to simulate a crash; after the recovery function is called, all 17 items are complete, with no duplicates.
- [ ] Numbers: the comparison table of A1-F and B0-F on dev. Add mean steps, calls of each tool, and cache hit rate.

This document adds:

- [ ] The B0 `prompt_hash` is still `50fb0745f4aa`; the B0 test for retry with doubled tokens after truncation passes.
- [ ] Test: `reasoning_content` is sent back in each turn; the `system` is exactly the same for all 17 items.
- [ ] Numbers: the cache hit rate after item 2 is above 0; cost and P50 / P95 latency of each contract; the count for each abstention reason; the counts of nudges, truncation retries, tool errors, and rejected records.
- [ ] All tests of stages 0–2 still pass.

---

## 13. Documents to update at delivery

1. [Roadmap](roadmap.md) Section 3 "Stage 3": check the acceptance criteria, write one paragraph on the completion status, and attach the comparison table of A1-F and B0-F (same format as the stage 1 table, plus mean steps, tool call counts, and cache hit rate). In the comparison with B0-F, say on which checklist items A1 becomes better or worse. Pay most attention to nda-1, nda-16, and nda-20, where B0 is weakest, and to the number of NotMentioned items that are classified as Entailment or Contradiction.
2. [Detailed design](design.md):
   - M4: describe the nudge (C2), the step counting method (C3), the total budget (C4), retry with doubled tokens after truncation (C6), and the difference between `failed` and `abstained`.
   - M5: the format of tool results, the limits, strict not turned on, the timeout method, and the redaction scope.
   - M6: includes `created`; on eval resume, the full contract is done again.
   - Section 9.2: use the measured values of A1-F to update the cost estimate table (A1-F, A1-P, A2-F, A2-P, and the total).
   - Section 9.3: change `tool_choice`, strict, and `reasoning_content` to confirmed facts; change `length` to "retry 1 time with doubled tokens, then give up".
3. [Technical decisions](decisions.md):
   - In the Section 3 table, change the status of M4, M5, and M6 to "implemented", and change `llm.py` to "stage 1 and stage 3 parts implemented".
   - Rules or values that you changed during the build (for example `max_review_output_tokens`, the stop word list): write them in Section 3.6, in the same format as "Rule changes during the build" in Section 3.5.
   - **M4 and M6 are core modules.** CLAUDE.md workflow item 2 says to add a "module explanation" section for each of them in the local explainer docs folder `docs/explainers/`. Do not write them in Technical decisions. If you work in a git worktree and cannot see this folder, write the two sections in the delivery report, and the user puts them in the local files. Explain at least:
     - M4: why review item by item and not all 17 items at once; why the contract is at the start of the system prompt (cache prefix, about 1/50 of the price); why item 1 runs first; how to make sure the model submits when `tool_choice` cannot be `required`; why `reasoning_content` must be sent back; why truncation gets only 1 retry with doubled tokens; why a server failure is recorded as `failed` and not `abstained`; why the budget has two layers (steps for each item, tokens for each review).
     - M6: why the state is saved for each checklist item; how `attempt + 1` prevents conflicts with old findings; why only `Exception` counts as a failure; how to handle the "recorded, state not changed" case; why eval resume does not use M6 (the cost and latency metrics).
4. [README](../README.md): add the A1-F command; add `tools.py`, `agent.py`, and `recovery.py` to the directory list; add `rank-bm25` to the dependencies.
5. [CLAUDE.md](../CLAUDE.md): update "Current progress", and add the A1-F numbers.

---

## 14. Delivery report format

1. What you did: the files you created and changed.
2. Acceptance criteria: whether each one passes, with evidence (test output, comparison table).
3. Actual cost: the run directory, number of contracts, and cost of each real run.
4. Differences from this document, and the reasons.
5. Problems you found and items for the user to decide (for example the value of `max_review_output_tokens`, and whether to tune the prompt).
6. Whether you committed. Do not commit without the user's agreement.
