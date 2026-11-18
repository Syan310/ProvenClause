# ProvenClause Stage 1 build spec: MVP (B0 single-pass review + scoring)

Written on 2026-10-21. Readers: the build agent. The user does not review the code line by line, but must be able to understand each module.

> **Change on 2026-10-21: the model changed to DeepSeek.** This document assumes Claude. During the build, the user decided to use only DeepSeek Flash and DeepSeek V4 Pro (see Technical decisions Sections 1.2 and 3.3). The actual build differs from this document in these places:
>
> | This document | Actual |
> |---|---|
> | `anthropic` SDK, `AnthropicLLM`, `ANTHROPIC_API_KEY` | `openai>=3.36,<4`, `DeepSeekLLM` (native endpoint `https://api.deepseek.com`), `DEEPSEEK_API_KEY` |
> | `messages.parse` structured output, returns `ParsedMessage` | JSON mode + schema in the prompt + Pydantic validation, returns `ParsedChatCompletion` |
> | `ModelStopped(reason, message)` | `ModelStopped(reason, completion)`. `stop_reason` becomes `finish_reason`: `content_filter` → `refusal`, `length` → `max_tokens` |
> | — | New `ModelInterrupted`: raised when `finish_reason` is `insufficient_system_resource` or `aborted`. The runner treats it as an API error |
> | Haiku two price tiers at 100,000 tokens | DeepSeek peak and off-peak prices, selected by the time in `completion.created` |
> | `call_cost(model, sdk_usage)`, `add_usage(total, model, sdk_usage)` | One more parameter `at` (the call time) |
> | `configs/b0-h.yaml`, `configs/b0-o.yaml`; B0-H | `configs/b0-f.yaml`, `configs/b0-p.yaml`; B0-F |
> | `manifest.json` records the `anthropic` version | Records `openai_version`, and also `checklist_hash` and `resumes` |
> | `--resume` compares `prompt_hash`, model, `split` | Compares `prompt_hash`, the full config, `split` |
> | — | `Usage` adds `reasoning_tokens` |

---

## 0. Read before you start

### 0.1 Read these files first

1. [CLAUDE.md](../CLAUDE.md): project rules.
2. [Technical decisions](decisions.md) Section 1 (verified facts), Section 2.3 (global conventions), and Section 3.2 (Stage 1 technology choices). **Do not break the global conventions.**
3. [Roadmap](roadmap.md): the interfaces in Section 2 and "Stage 1" in Section 3.
4. [Detailed design](design.md) Section 8.1 (metrics) and Section 9.3 (API usage notes).
5. Existing code: all files under `src/provenclause/` and `tests/`. Do not change the behavior of the Stage 0 code.

### 0.2 Rules

> **CAUTION: Real API calls cost money.** Before you run any real model call, tell the user what you will call, how many times, and the estimated cost. Wait for the user to agree clearly. This includes `pytest -m live` and `eval run`.

1. Before you write code that calls the Claude API, use the `/claude-api` skill to confirm the usage. Do not write SDK code from memory.
2. Do not change the decisions in Section 2.3. If you think a decision does not work, stop, tell the user why, and let the user decide.
3. Where this document says "default approach", you can follow it directly. For a design question that this document does not cover, ask the user first.
4. **Do not use test and test-40.** Stage 1 uses only dev.
5. Do not push to GitHub, do not create tags, and do not delete files. Ask the user before you commit.
6. Before you print contract text to the console, call `sys.stdout.reconfigure(encoding="utf-8")` (see CLAUDE.md).

### 0.3 Stage 1 goal

The input is one contract. B0 calls the model once and outputs 17 findings with evidence sentence IDs. The evaluation program compares them with the gold answers and gives a score. B0-H (Haiku 5.5) runs on the 61 dev contracts and produces the first results table and the measured cost.

---

## 1. Files to create and change

| File | Content | Create / change |
|---|---|---|
| `pyproject.toml` | Add dependencies and pytest config | Change |
| `.env.example` | `ANTHROPIC_API_KEY=` placeholder | Create |
| `src/provenclause/models.py` | `Finding`, `ReviewConfig`, `Usage`, `ReviewResult` | Change |
| `src/provenclause/parse.py` | `format_contract()` | Change |
| `src/provenclause/llm.py` | `LLM` protocol, `AnthropicLLM`, `FakeLLM`, price and cost calculation | Create |
| `src/provenclause/baseline.py` | B0 | Create |
| `src/provenclause/review.py` | `review_contract()`: dispatches by `config.method` | Create |
| `src/provenclause/eval/metrics.py` | Metrics | Create |
| `src/provenclause/eval/run.py` | Evaluation runner | Create |
| `src/provenclause/eval/__main__.py` | Command-line entry point `python -m provenclause.eval run ...` | Create |
| `configs/b0-h.yaml`, `configs/b0-o.yaml` | Two B0 configs. Write b0-o now, but do not run it in Stage 1 | Create |
| `tests/` | See Section 8 | Create |

---

## 2. Dependencies and config

1. Add runtime dependencies: `uv add "anthropic>=1.17,<2" python-dotenv`.
2. Add a dev dependency: `uv add --dev pytest-asyncio`.
3. Add this to `[tool.pytest.ini_options]` in `pyproject.toml`:
   ```toml
   asyncio_mode = "auto"
   markers = ["live: calls the real Claude API and costs money"]
   addopts = "-m 'not live'"
   ```
4. `.env` is already in `.gitignore`. Create `.env.example` with only one line, `ANTHROPIC_API_KEY=`, and commit it to Git.

---

## 3. `models.py`

Add `Outcome`, `Finding`, `ReviewConfig`, `Usage`, and `ReviewResult` as roadmap Section 2 describes. Do not change the 4 existing classes.

**Additional rules:**

- Set `extra="forbid"` on `ReviewConfig`, the same as on `ChecklistItem`. A wrong key in a config file causes an error.
- The type of `ReviewConfig.effort` is `Literal["low", "medium", "high", "xhigh", "max"]`.
- Add a constant:

  ```python
  # The reason when the model gives no finding. Scoring counts these items as "no answer"; see Section 6
  NO_ANSWER_REASONS = frozenset({"missing", "refusal", "max_tokens", "parse_error"})
  ```

- **How to fill `label` when there is no finding (default approach):** `label` is a required field. Do not make it optional. When the model gives no finding, set `label` to `"NotMentioned"`, `span_ids` to empty, `outcome="abstained"`, and `reason` to one of the 4 values above. Scoring uses `reason` and ignores this placeholder `label`.

---

## 4. `llm.py` (F6)

`llm.py` is the only place that calls the model. Stage 1 implements only structured output (`parse`). Stage 3 adds tool calls (`create`).

### 4.1 Interface

```python
class LLM(Protocol):
    async def parse(
        self, *, model: str, system: str, messages: list[dict],
        output_format: type[T], max_tokens: int, effort: str,
    ) -> ParsedMessage[T]: ...
```

- The return value is the SDK's own structured-output message type. Use the real class name from the installed SDK. Do not define your own copy of the class.
- `AnthropicLLM(client: AsyncAnthropic | None = None)`: when `client` is empty, create `AsyncAnthropic(max_retries=4)`. The SDK reads the credentials from environment variables.
- **One point to verify:** each call passes both `output_format=...` and `output_config={"effort": effort}`. First read the `messages.parse` source code in the installed SDK. Confirm that the SDK merges the two and does not let one overwrite the other. If the SDK does not merge them, find the form that the SDK supports, and explain it in the delivery report.
- **Do not pass the `thinking` parameter.** Both models turn on adaptive thinking by default. Opus 5.5 cannot turn thinking off. Keep both sides the same, and control them only with `effort`.
- The response `content` can start with a `thinking` block. When you read the content, check `block.type`, or use `parsed_output` directly.

### 4.2 Stop reasons

`AnthropicLLM.parse()` checks each response. In the 3 cases below, it raises `ModelStopped(reason, message)`. The exception must carry the original message, because these calls also cost money and the caller must record the cost from `message.usage`.

| Case | `reason` |
|---|---|
| `stop_reason == "refusal"` | `"refusal"` |
| `stop_reason == "max_tokens"` | `"max_tokens"` |
| `parsed_output` is `None`, or parsing fails | `"parse_error"` |

Do not catch API errors (400, 401, 429 and 5xx that still fail after retries, network errors). Raise them directly. The SDK already does the retries.

### 4.3 Price and cost

```python
@dataclass(frozen=True)
class Price:          # USD / million tokens
    input: float
    output: float
    cache_read: float
    cache_write: float
```

| Model | input | output | cache_read | cache_write |
|---|---|---|---|---|
| `claude-haiku-5-5` (prompt ≤ 100,000 tokens) | 0.10 | 0.50 | to verify | to verify |
| `claude-haiku-5-5` (prompt > 100,000 tokens) | 0.50 | 2.50 | to verify | to verify |
| `claude-opus-5-5` | 4.00 | 20.00 | 0.20 | to verify |

- Confirm the "to verify" prices with the `/claude-api` skill or the official pricing page. If you cannot confirm them, use cache_read = 0.1 × input and cache_write = 1.25 × input. State in a code comment and in the delivery report that these values are estimates.
- Calculate the "prompt" as `input_tokens + cache_read_input_tokens + cache_creation_input_tokens`. All NDAs are far below 100,000 tokens, but the code must handle this case.
- Write 2 pure functions:
  - `call_cost(model, sdk_usage) -> float`
  - `add_usage(total: Usage, model, sdk_usage) -> Usage`: adds the 4 token types, `n_calls + 1`, and `cost_usd`.
- For an unknown model name, raise an error. Do not price it at 0.

### 4.4 `FakeLLM`

- `FakeLLM(script)`: `script` is a list. Each item is one of the 3 types below. `FakeLLM` uses the items one by one in call order:
  1. A Pydantic object: return it normally, with `stop_reason="end_turn"`.
  2. A `ModelStopped` or other exception instance: raise it.
  3. A function `(request: dict) -> one of the two types above`: it decides what to return from the request content.
- Each call stores the request parameters in `fake.calls`, so tests can check the prompt.
- Usage is fixed (for example input 1000, output 200). You can also set it in the constructor.
- Write a helper function that builds the SDK message object. Tests for both `FakeLLM` and `AnthropicLLM` use it.
- If a call comes after the script is used up, raise `AssertionError`.

---

## 5. B0: `parse.py`, `baseline.py`, `review.py`

### 5.1 `format_contract(contract) -> str`

Put each sentence on one line in the format `[S{span_id}] {text}`. Join the lines with `\n`. The dataset sentences contain no newline characters (Stage 0 checked this on dev). B0, A1, and A2 share this function.

### 5.2 Output structure

```python
class B0Item(BaseModel):
    item_id: str
    label: Label
    span_ids: list[int]
    rationale: str

class B0Output(BaseModel):
    findings: list[B0Item]
```

`item_id` uses `str`, not `Literal`. Post-processing drops unknown IDs. Structured output does not support array length constraints, so post-processing also checks the number of items.

### 5.3 Prompt

- Write it in English.
- `system` holds the fixed instructions: the task, the meaning of the 3 finding types, the evidence rules (Entailment and Contradiction cite at least 1 sentence, NotMentioned cites none), cite only IDs inside `[S..]`, output 1 finding for each checklist item, and write 1 sentence in `rationale`.
- The `user` message holds the full text from `format_contract()`, then lists the `id`, `hypothesis`, and `guidance` of the 17 checklist items.
- Do not add examples. dev and test content must never go into the prompt.
- The prompt must not contain content that changes on each call, for example the time or random numbers.

### 5.4 Prompt hash

`prompt_hash()` returns the first 12 characters of a SHA-256 hash. The input is the `system` text, the `user` message template (without the actual contract and checklist content), and the JSON of `B0Output.model_json_schema()` (`sort_keys=True`). A different contract gives the same hash. A change of one character in the prompt changes the hash. Write the hash into `manifest.json`.

### 5.5 Post-processing

1. Build a table by `item_id`. When the same ID appears more than once, keep only the first one.
2. For each checklist item:
   - The model gave a finding: `Finding(outcome="verified", reason=None)`. By roadmap Section 4 #1, all B0 findings count as `verified`.
   - The model gave no finding: fill it as Section 3 says, with `reason="missing"`.
3. **Keep** a `span_id` that does not exist **as it is** (roadmap Section 4 #4). The metrics count it separately.
4. Unknown `item_id` values and duplicate items: drop them, and record the count with `logging.warning`.
5. Sort the results in checklist order.

**When the whole call fails:**

- `ModelStopped` is raised: fill all 17 items as Section 3 says, with the `reason` of the exception. Add the usage from `exc.message.usage` as usual.
- API error: do not catch it. The runner handles it (Section 7.4).

### 5.6 `review_contract()`

```python
async def review_contract(contract, checklist, config, llm) -> ReviewResult
```

- Put it in `review.py`. It dispatches by `config.method`: `"B0"` calls `baseline.py`. `"A1"` and `"A2"` raise `NotImplementedError`.
- Calculate `latency_s` with `time.perf_counter()`. Include the time of SDK retries.

---

## 6. `eval/metrics.py`

Write only pure functions. They do not call the model and do not read or write files. The input is `list[ReviewResult]`, the gold answers (the result of `load_gold()`), and the contracts (to check if each `span_id` exists). Score only the contracts that appear in the results.

### 6.1 Definitions

"No answer": an item whose `reason` is in `NO_ANSWER_REASONS`. In accuracy and F1, it counts as a prediction that is not equal to any gold answer.

| Metric | Definition |
|---|---|
| `accuracy` | Items where the predicted finding equals the gold answer ÷ all items. "No answer" counts as wrong |
| `f1_{label}`, `precision_{label}`, `recall_{label}` | Calculate each of the 3 classes separately, with all items pooled together (pooled calculation). When the denominator is 0, the value is 0 |
| `f1_contradiction_paper` | Paper style: calculate the Contradiction F1 for each checklist question separately, then take the mean. Include only checklist questions that have at least 1 gold Contradiction in this batch of results. Also output which checklist questions were used |
| `evidence_precision`, `evidence_recall` | Use only items whose gold answer is Entailment or Contradiction, pooled: Σ\|pred ∩ gold\| ÷ Σ\|pred\|, Σ\|pred ∩ gold\| ÷ Σ\|gold\| |
| `verified_precision` | Among items with `outcome == "verified"`, the share with a correct finding |
| `coverage` | `verified` items ÷ all items |
| `false_accepts` | Number of items that are `verified` but have a wrong finding |
| `n_abstained_by_reason` | Count for each `reason` |
| `n_invalid_span_ids` | Number of citations of a `span_id` that does not exist |
| `cost_total_usd`, `cost_per_contract_usd` | From `ReviewResult.usage` |
| `latency_p50_s`, `latency_p95_s` | The `latency_s` of each contract. Use the 50th and 95th cut points of `statistics.quantiles(data, n=100, method="inclusive")`. When there is only 1 contract, both values equal its latency |
| `tokens`, `cache_read_ratio` | The total of the 4 token types; `cache_read_input_tokens` ÷ all input tokens |
| `n_contracts`, `n_items` | Counts |

### 6.2 Print the results table

Write one more function that prints the metrics as a Markdown table: accuracy, F1 for the 3 classes, paper-style Contradiction F1, evidence precision and recall, verified precision, coverage, cost per contract, and P50 / P95 latency.

---

## 7. Evaluation runner: `eval/run.py` and `eval/__main__.py`

### 7.1 Commands

```bash
uv run python -m provenclause.eval run --config configs/b0-h.yaml --split dev --limit 5
uv run python -m provenclause.eval run --config configs/b0-h.yaml --split dev --resume runs/<run_id>
```

| Argument | Description |
|---|---|
| `--config` | YAML config file, read with `ReviewConfig.model_validate()` |
| `--split` | `dev`, `test40`, `test`. **`test40` and `test` run only with the extra flag `--allow-test`**. Without it, the program reports an error and exits. Stage 1 does not use this flag |
| `--limit N` | Take the first N contracts in numeric order of `contract_id` |
| `--workers` | How many contracts to review at the same time. Default 4 |
| `--resume DIR` | Continue in an existing results directory |
| `--runs-dir` | Where to put the results. Default `runs/` |

- Use the standard library `argparse`. The subcommand is `run`. Stage 6 adds `gate`, so use the subcommand structure now.
- The command-line entry point first calls `dotenv.load_dotenv()`, then creates `AnthropicLLM`.
- Write the core logic as an async function that takes `llm` as a parameter. Tests call it directly and pass in `FakeLLM`.

### 7.2 Results directory

`runs/<run_id>/`. `run_id` is `{config.name in lowercase}_{split}_{YYYYmmdd-HHMMSS}`, for example `b0-h_dev_20261022-101500`.

| File | Content |
|---|---|
| `manifest.json` | See 7.3 |
| `findings.jsonl` | Each line is the full `ReviewResult` of one contract (`model_dump_json()`) |
| `errors.jsonl` | Failed contracts: `contract_id`, exception type, message, time |
| `metrics.json` | Written after all contracts finish |

### 7.3 `manifest.json`

It must have these 5 fields (the roadmap acceptance criteria):

| Field | Content |
|---|---|
| `git_commit` | `git rev-parse HEAD`; also add `git_dirty: bool` |
| `model` | `agent_model` and `verifier_model` |
| `effort` | `config.effort` |
| `prompt_hash` | Section 5.4 |
| `seed` | `eval.sample.SEED` |

Also record: the full config, `split`, `limit`, the list of contract IDs, the `anthropic` version, the Python version, and the start time.

### 7.4 Concurrency, errors, and resume

1. Use `asyncio.Semaphore(workers)` to control concurrency.
2. After each contract finishes, append 1 line to `findings.jsonl` immediately, then call `flush()` and `os.fsync()`. Write the file in only one place, so that two tasks never write at the same time.
3. A contract raises an API error: write it to `errors.jsonl`, and continue with the other contracts. After all contracts finish, the exit code is not 0 if there is any error. `--resume` runs these contracts again.
4. `--resume`:
   - Read the existing `manifest.json`. If the config `prompt_hash`, the model, or `split` is different from this run, report an error and exit.
   - Read `findings.jsonl`. If the last line is incomplete (for example, because the process was killed), drop this line and rewrite the file with only the complete lines.
   - Skip the `contract_id` values that already exist, and review only the remaining contracts.
5. When the user presses Ctrl+C, keep the lines that are already written. Do not write half a line on exit.
6. After all contracts finish, calculate `metrics.json` from all results in `findings.jsonl`, and print the results table.

---

## 8. Tests

Tests that need data use the existing `contractnli_available` fixture. All tests except the `live` tests use `FakeLLM` and cost no money.

| File | Minimum content to test |
|---|---|
| `test_models.py` | `configs/b0-h.yaml` loads; a wrong `effort` is rejected; an extra key in the config file is rejected |
| `test_llm.py` | Check `call_cost` against hand-calculated usage; Haiku above 100,000 tokens uses the other price tier; `add_usage` adds correctly; an unknown model causes an error. Give `AnthropicLLM` a fake client and check the 3 `ModelStopped` cases |
| `test_baseline.py` | 17 normal items; 1 item missing → `missing`; for duplicate items only the first is kept; unknown IDs are dropped; a `span_id` that does not exist is kept; refusal → all 17 items are `abstained` and usage is added as usual; the prompt contains `[S0]`; a different contract gives the same `prompt_hash`, and a prompt change gives a different one |
| `test_metrics.py` | Write 2–3 small examples by hand, and calculate by hand the accuracy, F1 for each class, paper-style F1 (include a checklist question with no Contradiction), evidence precision and recall, verified precision, coverage, and quantiles. "No answer" counts as wrong |
| `test_run.py` | Use `FakeLLM` to run on 3 dev contracts: 3 result lines, and `manifest.json` has the 5 fields. **Interrupt test (use `workers=1`, so that concurrency timing does not affect the result):** raise an exception at the 2nd contract, then finish with `--resume`. The result is 3 lines with no duplicates, and `FakeLLM` is called only for the missing contracts. Resume works when the last line is incomplete. `test40` without `--allow-test` causes an error |
| `test_live.py` | Marked `live`: use the real Haiku to run B0 once on the shortest dev contract (`contract_id` 456, 2,270 characters). Check that there are 17 findings and the cost is above 0. **Ask the user before you run it** |

---

## 9. Build order and checkpoints

1. Dependencies and config (Section 2) → `uv run pytest` still passes.
2. `models.py`, `format_contract()`, and their tests.
3. `llm.py` and `test_llm.py`.
4. `baseline.py`, `review.py`, and `test_baseline.py`.
5. `metrics.py` and `test_metrics.py`.
6. `run.py`, `__main__.py`, the config files, and `test_run.py`.
7. **Checkpoint 1:** all offline tests pass. Stop, report to the user, and ask the user to confirm that `.env` contains the key.
8. **Checkpoint 2:** after the user agrees, run `uv run pytest -m live`.
9. **Checkpoint 3:** after the user agrees, run 5 contracts: `--split dev --limit 5`. Report the cost and latency of each contract, and estimate the cost of a full dev run.
10. **Checkpoint 4:** after the user agrees, run the full dev set (61 contracts).
11. Press Ctrl+C once during a run, then finish with `--resume`. Confirm that the number of results is correct and there are no duplicates (the roadmap acceptance criteria). You can combine this with step 10: press Ctrl+C halfway through the run.

---

## 10. Acceptance criteria

From roadmap Section 3 "Stage 1":

- [ ] The metric unit tests pass and give the known answers on small hand-written examples.
- [ ] B0-H runs on the 61 dev contracts and produces the first results table: accuracy, F1 for the 3 classes, evidence precision and recall, cost per contract.
- [ ] `manifest.json` has all 5 fields.
- [ ] Press Ctrl+C during a run, then run again with `--resume`. The number of results is correct, with no duplicates.

---

## 11. Documents to update at delivery

1. [Roadmap](roadmap.md) Section 3 "Stage 1": check the acceptance criteria and write a paragraph on the completion status.
2. [Detailed design](design.md) Section 9.2: update the estimate with the measured cost. test-40 contracts are longer than dev contracts on average. Consider this in the estimate (see [notes.md](notes.md) Section 2).
3. [Technical decisions](decisions.md):
   - In the Section 3 table, update the status of `llm.py` and M7.
   - **M7 is a core module.** By item 2 of the CLAUDE.md workflow, add a section "Module explanation of M7 evaluation (Stage 1 part)": what it does, why it has this design, which other options were considered, and which follow-up questions are possible. At minimum, explain: why the Contradiction F1 uses pooled calculation, why each line of `findings.jsonl` holds one contract, how `--resume` prevents duplicates, and how `manifest.json` makes results reproducible.
   - If the prices in Section 4.3 are estimates, state this in Section 1.2.
4. [README](../README.md): add the `eval run` command and the `.env` setup steps; update the directory listing.
5. [CLAUDE.md](../CLAUDE.md): update "Current progress" and the run commands.
6. Paste the results table into the report. Do not commit `runs/`.

---

## 12. Delivery report format

1. What was done: the files created and changed.
2. Acceptance criteria: whether each one passes, with evidence (test output, results table, run_id).
3. The B0-H results table on dev and the actual cost.
4. Differences from this document, and the reasons (for example, the SDK form verified in Section 4.1).
5. Problems found and items for the user to decide.
6. Whether you committed. Do not commit without the user's agreement.
