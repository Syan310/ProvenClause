# ProvenClause Technical Decisions

**How to use this file:** Before you build a stage, discuss which technology each module of that stage uses. Follow the technology choice method in Section 2 during the discussion. Write foundation decisions in Section 2.3, and write module decisions in Section 3. If nobody has discussed a module yet, keep it as "to discuss".

The full technology stack is in [Detailed design](design.md) Section 10.1. This file only records the specific choice for each module.

---

## 1. Verified facts (not choices)

The facts below were checked on 2026-10-21. Use them as reference when you discuss modules.

### 1.1 Environment and libraries

| Fact | Source |
|---|---|
| The local Python is 3.13.7, and uv is 0.10.11 | Local command |
| `openai` Python SDK: the latest version that uv installed on 2026-10-21 is 3.36.0 | uv |
| The PyMuPDF license is AGPL-3.0 | PyMuPDF official site |
| The latest `rank-bm25` is 0.2.2 (released 2022-02-16, no updates since then), and it depends on numpy. The latest `bm25s` is 0.3.15 (2026-10-22), and it has many optional dependencies | PyPI, 2026-10-25 |

### 1.2 DeepSeek API constraints

On 2026-10-21 the project changed from Claude to DeepSeek (see Section 3.3). The table below comes from the [DeepSeek API docs](https://api-docs.deepseek.com/), checked on 2026-10-21.

| Constraint | Affected modules |
|---|---|
| Model IDs: `deepseek-flash` (DeepSeek-V4.1-Flash), `deepseek-v4-pro` (DeepSeek-V4-Pro). Context is 1 million tokens, and the maximum output is 384K tokens | All modules that call a model |
| The native API is compatible with the OpenAI format, and `base_url` is `https://api.deepseek.com`. There is also an Anthropic-compatible API, but its `output_config` only supports `effort` and has no structured output | `llm.py` |
| Prices (per million tokens, off-peak / peak): Flash input (cache miss) $0.15 / $0.30, cache hit $0.003 / $0.006, output $0.60 / $1.20; Pro input $0.66 / $1.32, cache hit $0.022 / $0.044, output $1.98 / $3.96. There is no cache write fee | Cost calculation in evaluation |
| Peak hours: UTC Monday to Friday 01:00–04:00 and 06:00–10:00, except Chinese public holidays. `llm.py` selects the price by call time and does not handle holidays, so the cost on holidays is too high | Cost calculation in evaluation |
| Thinking is on by default for both models. The thinking content is in `message.reasoning_content`, separate from `content` | B0, M4, L3 |
| `reasoning_effort` accepts `low`, `high`, `max`; the API treats `medium` and `xhigh` as `high`. The default value is `high` | B0, M4, L3 |
| Thinking also uses `max_tokens`. In thinking mode, the default `max_tokens` is 64K | B0, M4, L3 |
| In thinking mode, `temperature` gives no error but has no effect. No parameter can make the model output fixed | M7 experiment design |
| There is no structured output that follows a Schema. JSON mode (`response_format={"type": "json_object"}`) guarantees valid JSON but does not guarantee that it matches the Schema. The prompt must contain the word "json". The docs say that JSON mode sometimes returns empty content | B0, L3 |
| `finish_reason`: `stop`, `length`, `content_filter`, `tool_calls`, `insufficient_system_resource` (the server does not have enough resources and stops the request), `aborted` | `llm.py` |
| Caching is on automatically, matches by prefix, and has no `cache_control`. In usage, `prompt_cache_hit_tokens` plus `prompt_cache_miss_tokens` equals `prompt_tokens` | Cost calculation in evaluation, M4 |
| A multi-turn conversation with tools must send back the `reasoning_content` of every earlier turn, or the API returns 400 | M4, N4 retry |
| Concurrency limit: Flash 2500, Pro 500 | Evaluation runner |
| Measured (B0-F, dev 61 contracts, 2026-10-21): thinking tokens are 90% of output, and output is 89% of cost. The largest output for one contract is 17,649 tokens, which is below the configured `max_tokens: 32000` | Cost calculation in evaluation, `max_tokens` setting |
| Measured (B0-P, dev 61 contracts, 2026-10-21): thinking tokens are 88% of output. On the same contracts, output tokens are 1.06 times Flash, thinking tokens are 1.04 times, cost is 3.60 times, and P50 latency is 1.6 times. The largest output for one contract is 17,247 tokens | Cost calculation in evaluation, `max_tokens` setting |
| `tool_choice` accepts `none`, `auto`, `required`, and a named tool `{"type": "function", "function": {"name": ...}}`. With tools, the default is `auto`. **In thinking mode, `required` and a named tool return 400**, so only `auto` is possible. Checked in the [API reference](https://api-docs.deepseek.com/api/create-chat-completion) on 2026-10-25 | M4 |
| strict mode is Beta: `base_url` must be `https://api.deepseek.com/beta`, and each tool sets `"strict": true`. Both thinking and non-thinking modes support it. All properties of an object must be in `required`, and `additionalProperties` must be `false`. It does not support `minLength`, `maxLength`, `minItems`, `maxItems`. Checked in the [Tool Calls guide](https://api-docs.deepseek.com/guides/tool_calls) on 2026-10-25 | M5 |
| With tools, you must send `reasoning_content` back unchanged in **all** later requests, including turns where the model did not call a tool. If you append `choices[0].message` unchanged to `messages`, this is satisfied. Tool results use `role: "tool"` and the matching `tool_call_id`. In a multi-turn conversation without tools, the API ignores the `reasoning_content` that you send back. Checked in the [thinking mode guide](https://api-docs.deepseek.com/guides/thinking_mode) on 2026-10-25 | M4 |
| `reasoning_effort` also accepts `none` (turns thinking off); the API treats `minimal` as `low`. You can also turn thinking off with `extra_body={"thinking": {"type": "disabled"}}`. Checked in the API reference on 2026-10-25 | Not used (turning thinking off changes the experiment conditions) |

---

## 2. Technology choice method (decided on 2026-10-21)

### 2.1 Technology choice stage by stage

- **Choose technology stage by stage.** Do not fix the technology of all stages before work starts. Finish one stage, and then discuss what the next stage uses.
- **Reasons:**
  1. The Detailed design already fixes the module split, the data flow, and the interfaces. Most later choices are "which library goes into a fixed place".
  2. Most later choices affect only one module, so a later replacement does not affect other modules.
  3. Measured results from earlier stages (for example cost and cache hit rate) help with later decisions.
- **Exception:** The 6 foundation decisions in Section 2.2 are shared by many stages, so they must be fixed in stage 0 and stage 1.

### 2.2 The 6 foundation decisions that must be fixed early

| # | Decision | Stage where it is fixed | Affected stages | Cost of a wrong choice |
|---|---|---|---|---|
| F1 | Python version and package manager | Stage 0 | All | Rebuild the environment |
| F2 | Test framework | Stage 0 | All | Rewrite the tests |
| F3 | Config file format (checklists and experiment configs) | Stage 0 | 0, 1, 7 | Rewrite the configs |
| F4 | Sync or async | Stage 1 | 1, 3, 4, 5 | Rewrite the model calls and the evaluation runner |
| F5 | Library for data structures, and the core interfaces (`Finding`, `review_contract()`, and others, see roadmap Section 2) | Stage 0 (library), stage 1 (interfaces) | 1–7 | Change every module |
| F6 | How to wrap model calls, and how to replace them with a fake model in tests | Stage 1 | 1, 3, 4, 6 | Change B0, the Agent, and L3 |

> **Note on F4:** Do not decide it only from the needs of stage 1. B0 in stage 1 calls the model only 1 time, so sync code is the simplest. But stage 3 must run 4 checklist items at the same time, and the API framework in stage 5 is usually async too. So decide F4 from the needs of stage 3 and stage 5.

### 2.3 Global conventions

After the discussion of F1–F6, write the decisions in this table. No choice in a later stage can conflict with it.

| # | Decision | Reason | Decision date |
|---|---|---|---|
| F1 | Python 3.13 + uv. Use `uv init --package` to create a src layout; commit `uv.lock` to Git | The machine already has 3.13.7, so no download is necessary; all dependencies support 3.13; the lock file lets you reproduce the environment that `manifest.json` records | 2026-10-21 |
| F2 | pytest. Choose the async test plugin in stage 1 together with F4 | Fixtures can share the small hand-written contracts; `parametrize` suits regex test cases; `tmp_path` suits SQLite tests | 2026-10-21 |
| F3 | YAML. Read it with PyYAML `safe_load`, then validate it with Pydantic | The file names and examples in the docs already use `.yaml`; `>` can hold a multi-line `guidance` and comments; Pydantic validation stops YAML implicit type conversion (for example `no` → `False`) | 2026-10-21 |
| F4 | All async: `AsyncOpenAI` (`AsyncAnthropic` before 2026-10-21), `async def review_contract`, and `asyncio.Semaphore` for concurrency. Async tests use pytest-asyncio with `asyncio_mode = "auto"`. **Exception (2026-10-23):** database functions are sync, use the standard library `sqlite3`, and run directly in the event loop thread (see Section 3.5) | Stage 3 must run 17 items at the same time, and `asyncio.wait_for` can do tool timeouts directly; FastAPI in stage 5 is async; in auto mode, test functions need no marker. Each database write takes less than 1 millisecond and one model call takes about 30 seconds, so the blocking from sync calls is too small to matter | 2026-10-21 |
| F5 | Pydantic v2 for data structures. Interfaces follow roadmap Section 2, plus: `review_contract()` returns `ReviewResult` (findings + `Usage` + latency); `Finding` gets `reason`; `ReviewConfig` gets `max_tokens`, and `effort` uses `Literal` | Cost and latency must come back with each contract, so they do not mix when several contracts run at the same time; `abstained` must state the reason; Pydantic can validate the JSON that the model returns, and FastAPI supports Pydantic directly | 2026-10-21 |
| F6 | An `LLM` protocol, passed to `review_contract()` as a parameter. The real implementation is `DeepSeekLLM` (it calls the DeepSeek native API), and tests use `FakeLLM`. `parse()` returns the SDK `ParsedChatCompletion` object. The SDK does the retries (`max_retries=4`). The SDK is pinned to `openai>=3.36,<4`, with no upgrade until stage 7. There is also 1 test that makes a real call, marked `live` and skipped by default | Dependencies are clear, and tests cost no money and give fixed results; the Agent must append the model reply (with `reasoning_content`) unchanged to the history, so the code keeps the SDK object; a change of SDK in the middle changes the experiment conditions. Changed on 2026-10-21: it was `AnthropicLLM` + `anthropic>=1.17,<2`; Section 3.3 gives the reason for the change to DeepSeek | 2026-10-21 |

### 2.4 Steps for each technology choice

1. Read Section 1 (verified facts) and Section 2.3 (global conventions).
2. For each module of this stage, give the user 2–3 options, the advantages and disadvantages of each, and one recommendation.
3. Check whether each option conflicts with the global conventions. If there is a conflict, first state which existing code must change, and then the user decides whether to change the global conventions.
4. After the user decides, write the decision in Section 3. Write foundation decisions in Section 2.3.
5. When you write the build spec for this stage, add a link to Section 2.3 so that the build agent follows it too.

---

## 3. Module decisions

| Module | File | Build stage | Status | Decision |
|---|---|---|---|---|
| Shared data structures | `models.py` | 0, 1 | Decided | See F5 |
| Model calls | `llm.py` | 1, 3 | Stage 1 and 3 parts built | See F6 and Section 3.3. Stage 1 builds `parse()` (JSON mode + Pydantic validation), prices and cost calculation, and `FakeLLM`. Stage 3 adds `create()` (tool calls) and "after truncation, redo 1 time with double the limit", see Section 3.6 |
| N1 Contract parsing | `parse.py` | 0, 2, 5 | Stage 0 part and rule part built; PDF part to discuss | Stage 0 reads the official data package with the standard library `json`, so there was no technology choice. For the rule part (stage 2), see Section 3.5 and "Rule changes during the build". PDF part (stage 5) to discuss |
| N2 Checklist | `checklist.py` | 0 | Decided | YAML file, read with PyYAML `safe_load`, validated with Pydantic (see F3, F5) |
| N3 Finding ledger | `ledger.py` | 2 | Built | Pure functions `validate_finding()` + `record_finding()`, readable `operation_id`, see Section 3.5 |
| N4 Verifier | `verify.py` | 4 | To discuss | Candidate options for L3 (on 2026-10-26 the user asked to compare them together in stage 4): (1) a single call with JSON output and no tools (the original plan in the Detailed design); (2) an Agent with read-only tools that searches the contract for counter-evidence by itself, and still cannot see the reasoning of the review Agent. Reason: the main error of A1-F is to label "NotMentioned" as "Entailment" or "Contradiction" (163 / 423 items), and option (2) targets this error directly |
| N5 Report | `report.py` | 5 | To discuss | |
| M1 API | `api.py` | 5 | To discuss | |
| M2 Database | `db.py` | 2 | Built | Standard library `sqlite3`, sync calls, 6 tables (adds `review_items`), see Section 3.5 |
| M3 State machine | `fsm.py` | 2 | Built | Hand-written transition table + "compare, then write", see Section 3.5 |
| M4 Agent loop | `agent.py` | 3 | Built | See Section 3.6 |
| M5 Tool system | `tools.py` | 3 | Built | `rank-bm25`, strict off, read-only tools run in a thread with a 10-second timeout, see Section 3.6 |
| M6 Crash recovery | `recovery.py` | 3 | Built | Unfinished items go back to `pending` and run again; an evaluation resume runs the whole contract again, see Section 3.6 |
| M7 Evaluation | `eval/` | 0, 1, 6, 7 | Stage 0 and 1 parts built; stage 6 and 7 to discuss | Stage 0 wrote `dataset.py`, `sample.py`, `stats.py`, with the standard library only. Stage 1 wrote `metrics.py`, `run.py`, `__main__.py`, see Section 3.2 and Section 3.4 |

After the discussion of each module, add a section below. State what was chosen, what was considered, and why.

### 3.1 Technology choice for stage 0 (2026-10-21)

The decisions and reasons are in Section 2.3. This section only records the options that were considered but not chosen.

| Decision | Chosen | Options not chosen and reasons |
|---|---|---|
| F1 | Python 3.13 + uv | Python 3.12 + uv: needs a separate 3.12 install, and has no feature difference from 3.13 in this project. pip + venv: no lock file, so the environment cannot be reproduced reliably |
| F2 | pytest | unittest: no dependency, but the code is longer and there is no parametrization |
| F3 | YAML + PyYAML | TOML + `tomllib`: no dependency, but long text is hard to read, and the `.yaml` file names in the docs must change. JSON: no comments, and long text needs escapes |
| F5 (library) | Pydantic v2 | Use `dataclass` in stage 0 first: stage 1 must change the code and the tests. Decide in stage 1: stage 0 can only return `dict`, and the tests must be rewritten |

### 3.2 Technology choice for stage 1 (2026-10-21)

The decisions for foundation decisions F4, F5, F6 are in Section 2.3.

**Options not chosen:**

| Decision | Chosen | Options not chosen and reasons |
|---|---|---|
| F4 | All async + pytest-asyncio | Sync + thread pool: timeouts and cancellation are hard, SQLite connections must be split by thread, and stages 3 and 5 may need a rewrite. A sync core wrapped in `asyncio.to_thread`: the same logic has two forms. The anyio test plugin: no extra dependency, but each test needs a marker |
| F5 | Return `ReviewResult` | Keep `list[Finding]` and let the `llm` object add up usage: usage mixes when several contracts run at the same time |
| F6 | `LLM` protocol + `FakeLLM` | Replace at the HTTP layer: the prepared API JSON is long, and thinking blocks are hard to write. Record and replay (vcrpy and similar): the recorded files contain the full contract text, and compatibility with `httpx2` in SDK 1.x is not certain |
| SDK version | `anthropic>=1.17,<2` (changed to `openai>=3.36,<4` on 2026-10-21, see Section 3.3) | 2.0.0: released on 2026-10-21, and the changelog only says "new API version 2026-10-21" |

**Stage 1 part of M7 evaluation:**

| Item | Decision | Reason |
|---|---|---|
| "Contradiction" F1 | The main table uses pooled calculation (all items together). A separate paper-style macro average is also calculated: first calculate F1 for each checklist question, then take the average, with only the checklist questions that have at least 1 gold "Contradiction". The paper-style value is only for comparison with the paper | test-40 has only 66 "Contradiction" items, and 7 checklist questions have none, so the macro average is not stable (see [notes.md](notes.md) Section 1.2) |
| Evidence precision and recall | Pooled calculation, only on items whose gold answer is "Entailment" or "Contradiction" | Same method as "Contradiction" F1 |
| Result file | Each line of `findings.jsonl` is the full `ReviewResult` of one contract | A contract is either fully written or absent; `--resume` only has to skip the `contract_id` values that already exist |
| Concurrency between contracts | `eval run --workers`, default 4 | One B0 run on dev makes 61 calls, and a sequential run is too slow |
| Command line | Standard library `argparse` | Same as the stage 0 scripts, with no extra dependency. typer was not chosen: it adds one more dependency |
| API key | `python-dotenv`, which reads `.env` in the command-line entry point | The Detailed design says that the key goes in `.env` |
| Roadmap Section 4 #1, #4, #5 | Follow the original recommendations: all B0 findings count as `verified`; when B0 cites a `span_id` that does not exist, keep it unchanged and count it separately; add `models.py`, `llm.py`, `configs/` | Confirmed on 2026-10-21 |

### 3.3 Change to DeepSeek (2026-10-21)

The user decided: from now on, do not use Claude, and use only DeepSeek Flash and DeepSeek V4 Pro. The DeepSeek constraints are in Section 1.2.

| Decision | Chosen | Options not chosen and reasons |
|---|---|---|
| Access method | Native API (OpenAI format) + `openai` SDK | Anthropic-compatible API: it keeps the `anthropic` SDK and the F6 code, but this is useful only if the project changes back to Claude. It has no JSON mode, and its usage fields have no documentation |
| Structured output | JSON mode + the Schema in the prompt + Pydantic validation. A validation failure counts as `parse_error` | Force a call to one tool and read the result from the tool arguments: DeepSeek does not guarantee that tool arguments match the Schema, so the effect is the same as JSON mode with more code |
| Config names | `-F` (Flash), `-P` (Pro): B0-F, A2-P, and others | Keep `-H`, `-O`: the names do not match the models |
| Peak and off-peak prices | Select the price by the time of each call (`completion.created`), and do not handle holidays | Peak price for all calls: simple, but the off-peak cost is 2 times too high. Off-peak price for all calls: the peak cost is too low |
| `effort` | Write `high` in the config. The type of `ReviewConfig.effort` does not change | Write `medium`: DeepSeek treats it as `high`, so the value that `manifest.json` records does not match the real value |

**Effect on existing code:** The stage 0 code does not call a model, so it does not change. The fields of `ReviewConfig` and `Finding` do not change. `Usage` gets `reasoning_tokens` (thinking tokens, which are part of `output_tokens`). `cache_creation_input_tokens` stays, but DeepSeek does not report it or charge for it, so it is always 0.

### 3.4 Module explanation of M7 evaluation (stage 1 part)

On 2026-10-24 this moved to the local explainer docs in `docs/explainers/`, which are not committed to GitHub.

### 3.5 Technology choice for stage 2 (2026-10-23)

The user decided to follow all recommendations. The build details are in the [Stage 2 spec](stage2-spec.md).

**What was chosen, and what was not chosen:**

| Module | Decision | Chosen | Options not chosen and reasons |
|---|---|---|---|
| M2 | Database library | Standard library `sqlite3`, sync calls. Only 1 connection, and all calls run in the event loop thread | `aiosqlite`: matches F4, but adds a dependency, opens 1 background thread for each connection, and all functions and tests must change to `async`. `sqlite3` wrapped in `asyncio.to_thread`: the connection crosses threads and needs a lock, which is also why F4 did not choose a thread pool |
| M2 | List fields | Store `span_ids`, `refs`, `defines`, `checks` as JSON text columns | Separate link tables: more tables, and queries need joins. SQLite 3.50 has JSON functions, so you can query these columns later if necessary |
| M2 | Table setup | `STRICT` tables, WAL, `foreign_keys=ON`. The schema version is in `PRAGMA user_version` | A migration tool (Alembic and similar): needs SQLAlchemy, and this project has only 1 schema version |
| M2 | `review_id` | `uuid4().hex` | Auto-increment integer: repeats across evaluation runs. ULID: adds a dependency |
| M3 | State machine form | Hand-written transition table (`dict[state, frozenset[state]]`) + `transition_review()`, `transition_item()` | `transitions` library: adds a dependency, adds methods to objects at runtime, and keeps the state in Python objects, not in the database. `python-statemachine`: adds a dependency, and 2 small tables do not need its features |
| M3 | Prevent wrong state from concurrent writes | "Compare, then write": `UPDATE ... WHERE status = :from`, and if 0 rows change, raise `InvalidTransition`. The state change and the `events` write are in the same transaction | Read the state first, then write: the state can change between the read and the write |
| N3 | Where the record rules live | Pure function `validate_finding(finding, contract) -> list[str]`, which `record_finding()` calls. L1 in stage 4 also uses it | In the Pydantic validator of `Finding`: when B0 abstains it uses "NotMentioned" as a placeholder, and roadmap Section 4 #4 requires B0 to keep a `span_id` that does not exist, so the validator would reject these findings |
| N3 | On error | Return a list of errors that shows all problems at once, in English. The stage 3 tool gives it back to the Agent | Raise an exception: the Agent learns only 1 problem at a time |
| N3 | `operation_id` | Readable string `"{review_id}:{item_id}:{attempt}"` | sha256: not readable, and as unique as the readable string |
| N3 | Duplicate writes | `INSERT ... ON CONFLICT(operation_id) DO NOTHING`, return the existing row, `duplicate=True` | Raise an error on a duplicate: an Agent that retries the same record would get a failure |
| N1 | Regex library | Standard library `re` | `regex` package: its extra features are not necessary. spaCy: a large dependency, and section numbers do not need syntax analysis |
| N1 | `Span.section` | The deepest "number-dot" number, for example `"2"`, `"2.06"`, `"1.1"`; `ARTICLE 1` is recorded as `"1"`. Bracket numbers such as `(a)`, `(i)` are not recorded | Record the full path `"5(a)(ii)"`: the code must tell whether `(i)` is a Roman numeral or a letter, and sentences that start in the middle of a clause (notes Section 3.5) also break the levels. Record only the top level `"5"`: dev has 266 headings in the `1.1` form, and references such as `Clause 14.1` would point to the wrong place |
| N1 | Prevent false headings | Numbers must appear in order: equal to the previous number plus 1, equal to the previous number, or a restart from 1 | No order check: sentences such as a `2.` in the middle of a sentence or `Section 4(b) of this Agreement` would become headings |
| N1 | Letter headings `A.` | If the contract has number headings, `A.`, `B.` are a preamble or a group, not sections; they are sections only when there are no number headings | Always sections: in dev contract 10, `A.`–`C.` are a preamble, and in contract 9, numbering starts again from `1.` under `A.` and `B.` |
| N1 | Where definitions are stored | `Span` gets the field `defines: list[str]` (the terms that this sentence defines), and the `spans` table gets one column | `Contract` gets `definitions: dict[str, list[int]]`: the same information is stored in two places, with one more copy outside the `spans` table |
| N1 | What `refs` stores | Normalized section numbers (for example `"2.06"`), only for references whose target section exists in this contract. The dropped references are counted separately | Store all matches: this would store references that point outside the contract, such as `Section 12 of the Securities Exchange Act` and `Section A of this Attachment A` |
| N1 | When it runs | Pure function `annotate_contract()`, which `load_contractnli()` calls by default. `format_contract()` does not change, and the B0 `prompt_hash` does not change | Call it separately only when necessary: callers can easily forget, and then `section` is empty |

**How the design gaps were handled:**

| # | Gap | Decision |
|---|---|---|
| 1 | The 5 tables in the design have no state for checklist items, and both M3 and M6 need it | Add a 6th table `review_items (review_id, item_id, status, attempt, retries, updated_at)`. "Find the last transition in `events`" was not chosen: the recovery query is complex |
| 2 | The transition table for checklist items has missing edges | Add `drafting → abstained` (budget exceeded or refusal before the record), and `drafting → pending` and `verifying → pending` (M6 recovery). A1 also goes `drafting → verifying → verified`, where `verifying` does nothing, so A1 and A2 use the same table |
| 3 | When `attempt` increases by 1 | A return to `drafting` (retry) and a return to `pending` (recovery) both add 1, so a new record gets a new `operation_id`. Only a retry adds 1 to `retries`. So "at most 2 retries" uses `retries`, and recovery does not count (roadmap Section 4 #3) |
| 4 | The exception word list for L2 | Not handled in stage 2; discuss it in stage 4 (notes Section 4, item 3) |
| 5 | Dev evaluation at the end of the stage | Not run. Stage 2 does not change B0 or the prompt, so another run would only repeat the same conditions and cost about $0.32. The stage 2 numbers are N1 statistics, with no model calls |
| 6 | Where the database file goes | Decide in stage 3. Stage 2 only writes `connect(path)`, and tests use `tmp_path`. Stage 3 decided it, see Section 3.6 D1 |
| 7 | Verification results in the `findings` table | One column `checks` (JSON), which matches `Finding.checks` one to one, not three columns `l1`, `l2`, `l3`. Also add a `reason` column, which matches `Finding.reason` |

**Rule changes during the build (2026-10-24):** Build spec Sections 3.2–3.4 allow changes to the N1 rules on train and dev. The list below shows all changes. test and test-40 were not used.

| # | Rule | Build spec text | Changed to | Reason and evidence |
|---|---|---|---|---|
| 1 | Section order | The next number equals the previous one, the previous one plus 1, or 1 | Also allow a forward jump, but the number of the next candidate heading must be between N and N+2. The last heading has no next heading, so it can jump at most 3 | When one heading is missing, all later headings are rejected, and sentences keep the old number. For example, dev 582 has no `9.`, so `10.`–`15.` are all rejected; dev 35 has no `3.`, so 37 headings from `4.1` to `13.9` are rejected. In dev 598, an isolated `4.` is followed by `3.`, so it is still rejected |
| 2 | First heading | Number ≤ 2 | Also allow it when the next heading confirms it (same as rule 1) | train 145 starts from `Article 4`, and all 13 headings were rejected |
| 3 | Heading form | `Section 5` | Also accept `Section 5.1`, recorded as `"5.1"` | Like `N.M`, it is a "number-dot" number |
| 4 | Definition verbs | 9 forms, such as `means`, `shall mean` | Also add `has the meaning`, `shall have the meanings`, `is defined as`, `shall be deemed to include`, `shall be (broadly) interpreted to include`; allow `as used herein` in between | dev 37 S40, dev 45 S8, dev 610 S11, dev 19 S17, and others |
| 5 | Several terms before the verb | Take only the term next to the verb | `"X", "Y" and "Z" shall have the meanings` takes all 3 | dev 37 S47 |
| 6 | Lowercase terms | A term starts with a capital letter or a digit | Also accept a lowercase start when a definition verb follows directly | `“day” shall mean` (dev 7 S181), `'confidential information' means` (dev 54 S13), `"trade secrets" means` (dev 398 S30). Lowercase terms in brackets are still not accepted |
| 7 | Words before a bracket and before `referred to as` | `the`, `a`, `an`, `each`, `collectively`, `hereinafter`, `individually`, `or`, and others | Also add `this`, `and`, `together`, `jointly`, `also`, `hereafter`, `herein`, `called`; `referred to herein / below, collectively, as` | `(this “Agreement”)` (dev 32 S1), `(hereinafter called "BROOKSTECH")` (dev 45 S2), `referred to herein, collectively, as the "Indemnified Parties"` (dev 64 S72) |
| 8 | `the` inside quotes | Keep as is | `(“the Database”)` is recorded as `"Database"` | dev 9 S41, dev 82 S1, dev 152 S6. If it is kept as is, `lookup_definition("Database")` finds nothing |
| 9 | Appositives | Not detected | A sentence that ends with `, the “X”.` is a definition | dev 64 S14 `, the “Property”.`, S18 `, the “Business”.` |
| 10 | Closing quote | `"”` | Also accept `“` | dev 37 S8 `(the “Authority“)` |
| 11 | Cross-references | All matches | A heading at the start of a sentence, such as `ARTICLE 1 - DEFINITIONS`, is not a reference | dev 10 S11 and dev 398 S50 counted themselves as references |
| 12 | Reference exclusion (changed after the 2026-10-24 review) | Exclude when the text after `of` is not `this Agreement`, `this NDA`, or `hereof` | Do not exclude any `of this ...` | train and dev have 2 cases of `paragraph 4 of this letter agreement`, which point to this contract and were dropped before. References found in train go from 779 to 781, and dropped references stay at 62; dev does not change |
| 13 | Section number normalization (changed after the 2026-10-24 review) | — | `normalize_key()` adds `(?!\d)`, the same as `_REF`: a 3-digit number is not a section number | Before, `"123"` was cut to `"12"`, and `"Section 100"` was cut to `"10"`. `get_section` in stage 3 gives the number from the model directly to `section_spans()`, so it would return the wrong section. Now it returns an empty list |

**Known inaccuracies (not fixed):**

- Uppercase headings without a number (`CONFIDENTIALITY OF EVALUATION MATERIALS`) are not detected. Because of this, dev 405, 507, 595 have no section numbers, and train has 41 such contracts. Detection of them needs a separate rule, and the build spec does not cover it.
- Single Roman numeral headings `I.`, `II.` are not detected (a decision in build spec Section 3.2). Because of this, train 89, 111, 153 lost references such as `Section III`.
- Headings such as `1)` and `16 Except` (no dot) are not detected. Most of the 62 dropped references in train come from these 3 cases.
- In contract 9, numbering starts again from `1.` under both `A.` and `B.`, so `section_spans(c, "1")` returns two places (build spec Section 3.2).
- `Sections 3 and 4` takes only `3` (build spec Section 3.4).
- In contracts with wrong numbering (train 211 has `1.1.`–`1.7.` under `2.`), the order rule rejects these subsections.
- `"DIRECTLY OR INDIRECTLY" as they modify the words "Compete" or "Competing" shall mean` (dev 398 S17) is recorded as `Compete`, `Competing`.

**Other decisions during the build (details that the build spec does not state):**

- When `transaction()` is called inside an open transaction, it joins the outer transaction and does not run `BEGIN` again. So `save_contract()` can run inside the caller's transaction.
- `connect()` checks `user_version` first, then creates the tables. A file with a wrong version is not changed.
- `db.py` also has `insert_finding()` and `get_finding()`, so `ledger.py` does not write SQL directly. `StoredFinding` also has `finding_id`.
- Statements with `RETURNING` are read fully with `fetchall()`. If only 1 row is read, the statement does not finish, and in autocommit mode the write is not committed.
- The "defined term count" of `stats --n1` removes duplicates per contract (case-insensitive). `--n1` does not accept `--split test`.

### 3.6 Technology choice for stage 3 (2026-10-25)

Before the discussion, the DeepSeek docs were checked, and the new facts are in Sections 1.1 and 1.2. The user decided: C6 changes to "after truncation, redo 1 time with double the limit", D4 takes "run the whole contract again", and the other 17 items follow the recommendations. The build details are in the [Stage 3 spec](stage3-spec.md).

**What was chosen, and what was not chosen:**

| # | Module | Decision | Chosen | Options not chosen and reasons |
|---|---|---|---|---|
| A1 | `llm.py` | Tool call interface | The `LLM` protocol gets `create(*, model, system, messages, tools, max_tokens, effort) -> ChatCompletion`. It returns when `finish_reason` is `stop` or `tool_calls`; `content_filter` and `length` raise `ModelStopped`; `insufficient_system_resource` and `aborted` raise `ModelInterrupted`. The model reply is appended unchanged to the conversation, so `reasoning_content` is sent back in every turn | — |
| A2 | `llm.py`, `tools.py` | strict mode | Off. Pydantic validates the tool arguments, and errors go back to the model as tool results and are counted | strict on: needs the Beta URL, and the behavior can change before stage 7; B0 and A1 would use different URLs; the program must still check values such as whether a `span_id` exists |
| A3 | `llm.py` | Fake model for tests | The existing `FakeLLM` gets `create()`, which shares one script in call order with `parse()`. Helper functions make two kinds of replies: "call a tool" and "text only" | A separate `FakeAgentLLM`: in stage 4 the Agent and L3 call in turns, so one script is easier to write |
| B1 | `tools.py` | BM25 | `BM25Okapi` from `rank-bm25==0.2.2`. Tokenization: lowercase, `[a-z0-9]+`, remove about 30 stop words, no stemming. Build the index 1 time per contract | Write it ourselves: the IDF details are easy to get wrong. `bm25s`: many dependencies, and with an average of 84 sentences per contract its speed is not necessary |
| B2 | `tools.py` | 10-second timeout | The 4 read-only tools use `asyncio.wait_for(asyncio.to_thread(...), 10)`. `record_finding` runs in the event loop thread (the SQLite connection is used only in this thread) | Direct call: `wait_for` cannot stop sync code, so the timeout exists only in name |
| B3 | `tools.py` | Tool result format | Text, one sentence per line `[S23] (§2.06) ...`, and `ERROR: ...` on error. Limits: `top_k` ≤ 20, `window` ≤ 3, `span_ids` ≤ 10, `get_section` returns at most 40 sentences | JSON: more tokens, and a different form from the contract in the system prompt |
| B4 | `agent.py`, `tools.py` | Redaction scope | Redact all text written to `events`, including model replies and thinking content: emails, phone numbers, Chinese ID numbers, US SSNs. The model sees the original text | Redact only tool results (the original text of the Detailed design): the thinking content also copies this information |
| B5 | `agent.py` | What `events` stores | For each model call, store `finish_reason`, `content`, `reasoning_content`, `tool_calls`, and usage; for each tool call, store the tool name, arguments, result, and duration. Estimate: about 60 MB for one dev run | Store only lengths: error analysis on the buffer day must read the reasoning of the model |
| C1 | `agent.py` | Prompt structure | system = general instructions + full contract text, the same for all 17 items, with no timestamp and no `review_id`. user = checklist question + `guidance`. All 17 items use the same tool definitions | The roadmap already fixed this; this row only confirms it |
| C2 | `agent.py` | The model ends without a tool call | Append a user message that asks for a `record_finding` call. This counts as 1 step | Parse the finding from the text: the rules are not reliable. Turn thinking off to use `required`: the conditions differ from B0 |
| C3 | `agent.py` | Steps | 1 model call counts as 1 step, at most `max_steps` (8). Several tool calls in one reply run in order and count as 1 step. After `record_finding` succeeds, the loop stops, and later calls in the same reply do not run. When a record is rejected, the error goes back to the model, and the step still counts. When steps run out: `abstained`, reason `budget_exceeded` | — |
| C4 | `agent.py`, `models.py` | Token limit per review | `ReviewConfig` gets `max_review_output_tokens`, default 300,000 (about $0.18 on Flash). Before each model call, check the total output tokens of this review; when it is over the limit, all unfinished items are `abstained` with reason `budget_exceeded`. Run 5 contracts first, then adjust the value | Rely only on 8 steps × `max_tokens` per item: the worst case is 17 × 8 × 32,000 ≈ 4.35 million tokens, about $2.6 per contract on Flash |
| C5 | `agent.py` | Concurrency and cache warm-up | `Semaphore(config.concurrency)` (4) in one review. Run item 1 alone first, then run the other items at the same time. Evaluation runs 4 contracts at the same time, so at most 16 calls run at the same time | — |
| C6 | `llm.py`, `agent.py`, `baseline.py` | Call errors | **Truncation (`length`):** redo the same call 1 time with 2 times the `max_tokens`. The truncated reply does not go into the conversation history, but its cost counts; the redo counts in the same step; before the redo, check the C4 budget, and do not redo if the budget is not enough. If the 2nd call is also truncated: `abstained`, reason `max_tokens`. **B0 uses the same rule**; B0-F and B0-P had no truncation on dev, so the old results stay valid, and `prompt_hash` does not change. **Refusal**: `abstained`, reason `refusal`. **Server interruption, or failure after SDK retries**: the whole review is `failed`, the exception goes up to the evaluation runner, and `--resume` runs this contract again | Give up on truncation (the original recommendation): the user decided to give 1 chance with a double limit. Count a server failure as `abstained` for this item: infrastructure failures would then count in coverage |
| C7 | `models.py`, `eval/metrics.py` | Run statistics | `Finding` gets `stats: AgentStats \| None`: steps, call count for each tool, tool error count, rejected record count, reminder count (C2), truncation redo count (C6). `metrics.py` calculates them from `findings.jsonl` | Store them only in `events`: metric calculation would also need to read the database |
| D1 | `eval/run.py` | Where the database file goes (Section 3.5 gap 6) | Evaluation: one file per run, `runs/<run_id>/reviews.db`. The stage 5 API: `data/provenclause.db` | `:memory:`: lost after the run, so the buffer day has no `events` to analyze. One global file: different runs mix together |
| D2 | `review.py` | How the review function gets a connection | `review_contract()` gets a keyword-only parameter `conn: sqlite3.Connection \| None = None`. A1 and A2 open `:memory:` when they get `None`. B0 does not use it | A module-level global connection: `db.py` says that it keeps no global connection |
| D3 | `recovery.py` | Crash recovery | `recover(conn)` finds reviews in `created`, `parsing`, `reviewing`, and moves items in `drafting` or `verifying` back to `pending` (`attempt + 1`). `resume_review()` runs only the `pending` items. If the process crashes after a finding is recorded but before the state changes, this item also runs again; findings of the old `attempt` stay in the table, and the final result takes the finding of the current `attempt` | If the current `attempt` already has a finding, go directly to `verifying`: saves 1 call, but has more branches |
| D4 | `eval/run.py` | An evaluation resume meets a half-done contract | Run the whole contract again with a new `review_id`, the same as B0. M6 is tested with FakeLLM in stage 3, and the stage 5 API calls it at startup | Use M6 to continue item by item: each interruption saves at most about $0.16 (Flash) or $0.6 (Pro); but usage must add up across processes, and latency cannot be calculated correctly, and both are numbers in the stage 7 main table |

**Effect on the global conventions and existing code:**

- **D2 adds 1 parameter to a core interface.** Roadmap Section 2 says "only add fields". The user agreed on 2026-10-25. The B0 behavior and `prompt_hash` do not change. `review.py`, `eval/run.py`, and their tests must change.
- **B2 uses threads, and this does not conflict with F4.** F4 did not choose a thread pool because model calls and the SQLite connection would cross threads. Here, only read-only tools that do not touch SQLite run in threads.
- **C4 and C7 are new fields, and both have default values.** Old `findings.jsonl` files can still be read. But `eval run --resume` compares the full config, so old B0 runs cannot resume after the new fields are added. All of them are already finished, so this has no effect.
- **C6 changes one code path of B0** (on truncation). This path never ran on dev, so the existing B0 results do not change.
- **New runtime dependency:** `rank-bm25==0.2.2`, which brings numpy.

**How the design gaps were handled:**

| # | Gap | Decision |
|---|---|---|
| 1 | An abandoned item has no row in the `findings` table, so the final result and recovery cannot read the reason | Also write a `findings` row on abstention: `label` uses `NotMentioned` as a placeholder, `span_ids` is empty, `reason` is written, `outcome = "abstained"`, and `operation_id` uses the current `attempt`. This matches how B0 `no_answer()` writes it |
| 2 | `budget_exceeded` is not in `NO_ANSWER_REASONS`, so scoring would count the placeholder `NotMentioned` as an answer | Add it to `NO_ANSWER_REASONS`. Both "steps exceeded" and "total budget exceeded" use `budget_exceeded`, and the event tells `steps` and `review_tokens` apart |
| 3 | A1 has no verifier, so when is `findings.outcome` written | After a successful record, do `drafting → verifying → verified` in one transaction, and change the `outcome` of this finding to `verified`. `db.py` adds `set_finding_outcome()`, which stage 4 also uses |
| 4 | How to simulate "the process is killed" | The review loop treats only `Exception` as a failure (review `failed`). `BaseException` (`KeyboardInterrupt`, `CancelledError`, `Crash` in tests) does not change the state and stays in `reviewing`, the same as a killed process |
| 5 | How to calculate usage and latency for a review that M6 recovered | Count only the part after recovery. Items that finished before the crash have `stats` as `None` (`stats` is not stored in the database). Evaluation does not use recovery (D4), so metrics are not affected |
| 6 | A review that stopped in `created` (crash right after creation) | M6 in the Detailed design only mentions `parsing` and `reviewing`. This decision also recovers `created`, or the review would never start |

**Rule changes during the build (2026-10-26):** The list below shows the rules and values that changed during the build. All results were checked only on dev; test and test-40 were not used.

| # | Rule | Build spec text | Changed to | Reason and evidence |
|---|---|---|---|---|
| 1 | `max_review_output_tokens` (C4) | 300,000 | The A1-F and A1-P configs change to 100,000. The default value of `ReviewConfig` stays 300,000 | On 5 dev contracts (run `a1-f_dev_20261025-201026`), the largest output for one contract was 16,065 tokens, and the largest single call was 3,393 tokens. 100,000 is about 6 times the measured maximum, and the worst-case cost is lower. The user decided on 2026-10-26 |
| 2 | Stop word list | About 30 | 31: `a an and any are as at be been by for from has have in is it its of on or shall such that the this to was were will with` | — |
| 3 | Truncation redo count | `n_length_retries + 1` in `on_truncated` | Add 1 only when a redo really happens: no count when the total budget is used up and there is no redo | If it also counts when there is no redo, the statistics are too high |
| 4 | Expected state in the crash test | Item 6 `drafting`, the others `pending` | Items 6 and 7 are both `drafting` | When item 6 fails, it releases the semaphore, so item 7 starts at once, and then TaskGroup cancels it in the middle of a call. A killed process also leaves this state, and `recover()` moves both items back to `pending` |

**A prompt rule that was tried and not used (2026-10-26):** On 5 dev contracts, A1-F labeled "NotMentioned" as "Contradiction" more often than B0-F (nda-1: the definition lists information by content and does not require marking, and the model labeled it Contradiction). One rule was added to `SYSTEM` of A1: "A missing requirement is not a Contradiction ... the label is NotMentioned" (run `a1-f_dev_20261025-202007`, `prompt_hash` `4d81334e6aa1`). Accuracy did not change (0.753), and the 2 errors on nda-1 stayed. Two A1 runs already differ on 4 items without any change. The user decided to remove this rule, so the A1 label definitions stay the same as B0, and `prompt_hash` goes back to `39fa1266b5e6`.

**Other decisions during the build (details that the build spec does not state):**

- `FakeLLM.create()` runs `await asyncio.sleep(0)` first each time, so it gives up the event loop like a real network call. Without this, the concurrent items finish one after another, and a crash cannot cancel them.
- `llm._tokens()` is renamed to `token_counts()`: the Agent needs it when it writes `model_call` events. `usage.input` in the event is the cache-miss input tokens, the same as `Usage.input_tokens`.
- The call with a server interruption (`ModelInterrupted`) is also charged and also writes a `model_call` event, and then the exception is raised.
- The tool JSON Schema removes the `title` that Pydantic adds automatically, to use fewer tokens.
- `resume_review()` checks the method first. If it is not A1, it raises `NotImplementedError`, and the review state does not change.
- For a text-only reply with empty `content`, the reply is sent back with `content` as an empty string. A reply with `tool_calls` gets no `content`.
