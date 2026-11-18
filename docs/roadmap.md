# ProvenClause roadmap: from MVP to the complete system

Version: v1.0 (2026-10-21)
Basis: [Detailed design v1.0](design.md). This document only tells "in what order to build, and how far each step goes". For the design of each module, read the Detailed design.

---

## 0. General principles

1. **At the end of each stage, the system runs end to end and gives one new experiment number.** Do not leave a module "half done and not able to run".
2. **Define the interfaces first, then fill in the implementation.** Stage 1 defines 4 core data structures and 1 core function. Later stages only add fields. They do not change existing fields.
3. **At the end of each stage, do 3 things:** run `pytest`; run one evaluation on dev with DeepSeek Flash and save the result in `runs/`; add a git tag (`v0.0` to `v0.8`).
4. **During development, use Flash by default. Pro can run reference results on dev; the formal experiments run only in Stage 7.** Changed on 2026-10-21: the old rule was "use only Flash during development, and run Pro only in Stage 7". The reason: Pro costs only 3–4 times as much as Flash, and one B0-P run on dev costs about $1. On 2026-10-21 the project changed from Claude to DeepSeek. See Technical decisions Section 3.3. Config name suffixes: `-F` is Flash, `-P` is Pro.
5. **Use test only in Stage 7.** All debugging before that uses only dev.

---

## 1. Stage overview

| Stage | Name | Day (date) | New modules | Unlocked configs | What the system can do at the end of the stage | Tag |
|---|---|---|---|---|---|---|
| 0 | Skeleton and data | 1 (10/21) | Data loading, N2 | — | Load any contract and its gold answers, and print statistics | v0.0 |
| 1 | **MVP: single-pass review + scoring** | 2 (10/22) | B0, M7 metrics, evaluation runner, model call wrapper | B0-F | Give the first result table and the measured cost on dev | v0.1 |
| 2 | Storage base | 3 (10/23) | M2, M3, N3, rule part of N1 | — | Findings go into the database; invalid findings and invalid transitions are blocked | v0.2 |
| 3 | Per-item agent | 4–5 (10/24–25) | M5, M4, M6 | A1-F | Review all 17 items of one contract concurrently; resume after an interruption | v0.3 |
| — | Buffer day 1 | 6 (10/26) | Hard-case classification code | — | Error analysis on dev; first cases of the regression set | — |
| 4 | Verifier | 7–8 (10/27–28) | N4 (L1, L2, L3) | A2-F | **Core system complete** | v0.4 |
| 5 | Service | 9 (10/29) | M1, N5, PDF parsing | — | Review a PDF through the API and get a report | v0.5 |
| 6 | Verifier trustworthiness | 10 (10/30) | Error injection, regression gate | — | Know which errors the verifier can catch | v0.6 |
| 7 | Formal experiments and analysis | 11–12 (10/31–11/01) | Analysis scripts | B0-P, A1-P, A2-P, C5 | All result tables and figures | v0.7 |
| 8 | Delivery | 13–14 (11/02–03) | README | — | Push to GitHub | v0.8 |

### Dependencies

```
Stage 0 data + checklist
   │
Stage 1 B0 + metrics + evaluation runner  ←── MVP: from here on, every stage has numbers
   │
Stage 2 database + state machine + finding ledger + section/definition/cross-reference detection
   │
Stage 3 tools + agent loop + concurrency + crash recovery        → A1-F
   │
Stage 4 verifier L1 → L2 → L3 + retry + three outcomes           → A2-F
   │
   ├── Stage 5 API + report + PDF (for demo, not in evaluation)
   └── Stage 6 error injection + regression gate
          │
Stage 7 formal experiments (6 configs × test-40) + analysis
   │
Stage 8 README
```

Stage 5 and Stage 6 do not depend on each other. When time is short, do Stage 6 first, because it produces experiment numbers.

---

## 2. Interfaces to define in Stage 1

All later stages build on these interfaces. Write them correctly first to avoid rework later.

```python
# src/provenclause/models.py
Label = Literal["Entailment", "Contradiction", "NotMentioned"]
Outcome = Literal["verified", "violated", "abstained"]

class Span(BaseModel):
    span_id: int
    start: int
    end: int
    text: str
    section: str | None = None        # filled in Stage 2
    is_definition: bool = False       # filled in Stage 2
    refs: list[str] = []              # filled in Stage 2
    defines: list[str] = []           # filled in Stage 2: the terms this sentence defines

class Contract(BaseModel):
    contract_id: str
    source: Literal["contractnli", "pdf"]
    title: str
    text: str
    spans: list[Span]

class Finding(BaseModel):
    item_id: str
    label: Label
    span_ids: list[int]
    rationale: str
    reason: str | None = None            # reason for abstained, for example "refusal", "budget_exceeded", "missing"
    self_confidence: int | None = None   # filled by the agent from Stage 3
    outcome: Outcome | None = None       # filled by the verifier from Stage 4
    support_score: int | None = None     # filled by L3 from Stage 4
    checks: dict[str, str] = {}          # from Stage 4: "L1" / "L2" / "L3" → result
    stats: AgentStats | None = None      # filled by A1 and A2 from Stage 3: steps, call count per tool, etc.; None for B0

class ReviewConfig(BaseModel):
    name: str                            # for example "A2-F"
    method: Literal["B0", "A1", "A2"]
    agent_model: str                     # "deepseek-flash" or "deepseek-v4-pro"
    verifier_model: str | None = None    # different from agent_model for C5
    effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"   # config files set high explicitly
    max_tokens: int = 16000
    max_steps: int = 8
    max_retries: int = 2
    concurrency: int = 4
    max_review_output_tokens: int = 300_000   # Stage 3: limit on total output tokens in one review

class Usage(BaseModel):                  # total usage of one review
    input_tokens: int = 0                # input tokens that did not hit the cache
    output_tokens: int = 0               # includes reasoning_tokens
    cache_read_input_tokens: int = 0     # DeepSeek prompt_cache_hit_tokens
    cache_creation_input_tokens: int = 0 # DeepSeek does not report or charge for these
    reasoning_tokens: int = 0            # part of output_tokens
    n_calls: int = 0
    cost_usd: float = 0.0

class ReviewResult(BaseModel):
    contract_id: str
    findings: list[Finding]
    usage: Usage
    latency_s: float
```

**Core function:**

```python
async def review_contract(contract: Contract, checklist: Checklist, config: ReviewConfig, llm: LLM,
                          *, conn: sqlite3.Connection | None = None) -> ReviewResult
```

- `llm` is the `LLM` protocol in `llm.py`: real calls use `DeepSeekLLM`, and tests use `FakeLLM` (see Technical decisions F6).
- Changed on 2026-10-21: the return value changed from `list[Finding]` to `ReviewResult`, `Finding` got `reason`, and `ReviewConfig` got `max_tokens` (see Technical decisions F5).
- Changed on 2026-10-25: added the keyword-only parameter `conn`. A1 and A2 write the review into this database, and use `:memory:` when it is `None`; B0 does not use it (see Technical decisions Section 3.6 D2). `Finding` got `stats`, and `ReviewConfig` got `max_review_output_tokens` (C4, C7).

- B0, A1, and A2 all implement this function, and `config.method` tells them apart.
- The evaluation script and the API call only this function. Thus the evaluation code is the same for all 6 configs.
- Each config is one YAML file: `configs/b0-f.yaml`, `configs/b0-p.yaml`, `configs/a2-p.yaml`, etc.

---

## 3. Tasks and acceptance criteria for each stage

### Stage 0: skeleton and data (Day 1, 10/21)

**Goal:** Load any contract and its gold answers.

**Tasks:**
1. Create the project with `uv init`, and create the directories as in Detailed design Section 10.2. Add `data/`, `runs/`, and `.env` to `.gitignore`.
2. Download the official ContractNLI data package and put it in `data/contract-nli/`. Do not use the sentence-level version on Hugging Face.
3. Write `load_contractnli(split) -> list[Contract]` in `parse.py`. Use the `spans` in the data package directly. Do not split sentences again.
4. Write `load_gold(split)` in `eval/dataset.py`. It returns `{(contract_id, item_id): (label, span_ids)}`.
5. Write `checklists/nda_17.yaml` and `checklist.py` (Pydantic validation). Write `guidance` in your own words. The paper appendix does not publish the annotation guidelines, so write it from the original checklist question text and the evidence examples in train (see [Notes](notes.md) Section 1.3).
6. Sample 40 contracts from test with a fixed random seed, write them to `eval/splits/test40.txt`, and commit the file to Git.
7. Open the original paper, and check the baseline numbers (BERT-large accuracy, F1 of the Contradiction class) and the evaluation setup. Record them in `docs/notes.md`.
8. Read 5 NDAs by hand, and note how they write exception clauses, definitions, and cross-references. The Stage 2 regular expressions use these notes.

**Acceptance criteria:**
- [x] Test: 607 contracts load, split into 423 / 61 / 123.
- [x] Test: every span satisfies `span.text == contract.text[span.start:span.end]`.
- [x] Test: the finding distribution in test is Entailment 968, NotMentioned 903, Contradiction 220.
- [x] Run the sampling script again, and the content of `test40.txt` does not change.

**Status (2026-10-21):** All 4 items pass. `test40.txt` is in `src/provenclause/eval/splits/`. Check it with `uv run python -m provenclause.eval.sample --check`. The paper check and the notes on 5 NDAs are in [Notes](notes.md).

---

### Stage 1: MVP — single-pass review + scoring (Day 2, 10/22)

**Definition of the MVP:** Input one contract, output 17 findings with evidence sentence IDs, then score them with a program against the gold answers. This is the B0 baseline.

**Why the MVP is B0:**
- It is the shortest closed loop: contract → findings → score.
- Each module added later is compared with it to see how much it improves.
- At the end of Day 2, you already have a number that can go into the README.

**Tasks:**
1. **First discuss the technology choice for each module of this stage**, and record the decisions in [Technical decisions](decisions.md). Section 1.2 of that file gives the DeepSeek API constraints. Before you write code, check the official DeepSeek documentation (api-docs.deepseek.com) again.
2. Write `llm.py`: the only place that calls the model. `DeepSeekLLM` uses the `openai` SDK to call the OpenAI-compatible DeepSeek API. It creates the client, sets `reasoning_effort`, checks `finish_reason`, records token usage, and converts it to US dollars. B0, the agent, and L3 all call the model through it.
3. Write `format_contract(contract)` in `parse.py`. It outputs the numbered full text, for example `[S23] Recipient may disclose ...`. B0, A1, and A2 share this function.
4. Write `baseline.py` (B0): one call that returns 17 `Finding` objects. Use JSON mode for output, put the schema in the prompt, and then validate with Pydantic. When validation fails, the result is `abstained` with reason `parse_error`.
5. Write `eval/metrics.py`: accuracy, F1 per class, evidence precision and recall, verified precision, coverage, cost, and P50 / P95 latency.
6. Write `eval/run.py`:
   ```bash
   uv run python -m provenclause.eval run --config configs/b0-f.yaml --split dev --limit 5
   ```
   - Results go to `runs/<run_id>/`: `metrics.json`, `findings.jsonl`, `manifest.json` (git commit, model, effort, prompt hash, random seed).
   - **Add `--resume` now:** contracts that are already done are read from `findings.jsonl`, and the model is not called again. When Pro runs in Stage 7, an error in the middle does not waste money.
7. Run 5 contracts first to confirm the cost. Then run all of dev, and update the cost estimate in Detailed design Section 9.2.

**Acceptance criteria:**
- [x] The metric unit tests pass. Small hand-written examples give known answers.
- [x] B0-F runs all 61 dev contracts and produces the first result table: accuracy, F1 for 3 classes, evidence precision and recall, and cost per contract.
- [x] `manifest.json` has all 5 fields.
- [x] Press Ctrl+C in the middle, then run again with `--resume`. The number of results is correct, with no duplicates.

**Status (2026-10-21):** All 4 items pass. The model changed to DeepSeek during the build (see Technical decisions Section 3.3).

1. `uv run pytest`: 75 offline tests pass. `tests/test_metrics.py` checks all metrics with 2 hand-written contracts. `uv run pytest -m live` reviews contract 456 with the real Flash: 17 findings, $0.0017, 9.6 s.
2. B0-F results on dev (run `b0-f_dev_20261021-154250`):

   | Config | Accuracy | F1 Entailment | F1 Contradiction | F1 NotMentioned | F1 Contradiction (paper style) | Evidence precision | Evidence recall | Verified precision | Coverage | Per contract | P50 | P95 |
   |---|---|---|---|---|---|---|---|---|---|---|---|---|
   | B0-F | 0.804 | 0.877 | 0.574 | 0.777 | 0.420 | 0.875 | 0.552 | 0.804 | 1.000 | $0.0052 | 34.4 s | 55.8 s |

   - Total cost $0.32. The 1,037 findings have no abstentions and no reference to a `span_id` that does not exist.
   - Contradiction precision is 0.454 and recall is 0.779: the model gives Contradiction too often. There are 203 wrongly accepted findings.
   - The paper-style F1 uses 8 checklist questions. The other 9 have no gold Contradiction on dev.
3. `manifest.json` has `git_commit`, `model`, `effort`, `prompt_hash` (`50fb0745f4aa`), and `seed`. The Stage 1 code was not committed at the time of this run, so `git_dirty` is `true`.
4. Press Ctrl+C at contract 18. The program prints a message and exits, and `findings.jsonl` has 18 complete result lines. `--resume` reviews only the remaining 43 contracts. The final file has 61 lines and 61 different `contract_id` values, which match the contract list in `manifest.json`.

**B0-P reference results on dev (2026-10-21):** Run the same B0 with Pro (same `prompt_hash`: `50fb0745f4aa`) and compare with B0-F. These are reference results. The formal numbers come from test-40 in Stage 7. Run `b0-p_dev_20261021-162737`, `git_commit` `ba1e2d6`, `git_dirty` is `false`. All calls ran in off-peak hours, with no errors and no resume.

| Config | Accuracy | F1 Entailment | F1 Contradiction | F1 NotMentioned | F1 Contradiction (paper style) | Evidence precision | Evidence recall | Verified precision | Coverage | Per contract | P50 | P95 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| B0-F | 0.804 | 0.877 | 0.574 | 0.777 | 0.420 | 0.875 | 0.552 | 0.804 | 1.000 | $0.0052 | 34.4 s | 55.8 s |
| B0-P | 0.809 | 0.890 | 0.579 | 0.772 | 0.457 | 0.893 | 0.568 | 0.809 | 1.000 | $0.0189 | 56.0 s | 106.1 s |

1. **The accuracy difference is not significant.** Pro minus Flash is +0.0048, with a 95% confidence interval of [−0.0087, +0.0174]. The method is a paired bootstrap by contract: 1,037 findings, 10,000 resamples, seed 20261021.
2. **The two models make errors on the same items.** Of 1,037 items, both are correct on 815, only Flash is correct on 19, only Pro is correct on 24, and both are wrong on 179. Of the 179 items where both are wrong, 175 have the same wrong answer.
3. **The main error is the same: NotMentioned is given as Entailment or Contradiction.** Flash does this on 139 items and Pro on 146. That is 33% and 35% of the gold NotMentioned items (423). Contradiction precision is only 0.45 for both models, and recall is 0.78 and 0.81.

   | Gold answer \ model answer | Flash: Entailment | Flash: Contradiction | Flash: NotMentioned | Pro: Entailment | Pro: Contradiction | Pro: NotMentioned |
   |---|---|---|---|---|---|---|
   | Entailment (519) | 476 | 20 | 23 | 485 | 17 | 17 |
   | Contradiction (95) | 20 | 74 | 1 | 17 | 77 | 1 |
   | NotMentioned (423) | 70 | 69 | 284 | 69 | 77 | 277 |

   Neither model has items with "no answer".
4. **The hard checklist items are the same.** Checklist items where both models have accuracy below 0.55: nda-1 (0.393 / 0.344), nda-16 (0.459 / 0.475), nda-20 (0.508 / 0.492). Both models get 1.000 on nda-8, nda-11, nda-12, and nda-13. The largest differences are on nda-7 and nda-10, where Pro is 0.066 higher on both. The full per-item table is in the output of this analysis and is not in this document.
5. **Cost and speed.** Pro produces 1.06 times as many output tokens as Flash (average 8,248 vs 7,787 per contract), and 1.04 times as many reasoning tokens (7,257 vs 7,012). Total cost is $1.152, which is 3.60 times the Flash cost. P50 latency is 1.6 times that of Flash.

**Conclusion:** On B0, Pro costs 3.6 times as much but gives no measurable improvement. The errors of the two models overlap strongly, and most of them are in the NotMentioned decision. One hypothesis: the B0 bottleneck is not model size. It is the task method (17 items in one pass, no verification), the `guidance` of the checklist items, or the scope of the gold answers. A1 in Stage 3 and the error analysis on the buffer day check this hypothesis. By the project rules, the B0 prompt does not change because of this result.

---

### Stage 2: storage base (Day 3, 10/23)

**Goal:** All data structures that the agent needs are in place. Findings can go into the database, and invalid findings are blocked when they are written.

**Technology choice (2026-10-23):** See Technical decisions Section 3.5. For build details, see [Stage 2 spec](stage2-spec.md). There are 2 differences from the tasks below: the database has 6 tables (`review_items` is added to store the state of each checklist item); and no dev evaluation runs at the end of the stage, because Stage 2 does not change B0. The Stage 2 numbers are the N1 statistics.

**Tasks:**
1. `db.py`: create 5 tables (Detailed design M2). Turn on WAL mode. Add a unique index on `findings.operation_id`. Changed on 2026-10-23: add a 6th table, `review_items`.
2. `fsm.py`: two transition tables, one for the review level and one for the checklist item level. `transition()` checks if a transition is valid. If it is not valid, it raises an error; if it is valid, it writes one `events` row.
3. `ledger.py`: `record_finding()` applies the 2 record rules of Detailed design N3, and uses `operation_id` so that a repeated write gives only 1 row.
4. Rule part of `parse.py` (N1):
   - Section detection: `1.`, `1.1`, `Section 5`, `Article III`.
   - Definition detection: `"Confidential Information" means ...`, `(the "Agreement")`.
   - Cross-reference detection: `Section 5`, `paragraph 3(b)`.
   - Put the results into `Span.section`, `Span.is_definition`, and `Span.refs`.

> **Note:** The 14-day plan in the Detailed design gives no time to the N1 section, definition, and cross-reference detection. But the `lookup_definition` and `get_section` tools on Day 4 and L2 on Day 7 all depend on it. So this roadmap puts it on Day 3.

**Acceptance criteria:**
- [x] Test: an invalid transition raises an error; each valid transition writes 1 `events` row. (`tests/test_fsm.py`: every valid edge in both tables, and every invalid edge at the review level)
- [x] Test: write the same `operation_id` 2 times, and the table has only 1 row. (`tests/test_ledger.py`)
- [x] Test: a finding of Entailment with empty `span_ids` returns an error; a finding of NotMentioned with non-empty `span_ids` returns an error; a `span_id` that does not exist returns an error. (`tests/test_ledger.py`)
- [x] Test: the regular expressions for sections, definitions, and cross-references have at least 1 case for each writing form. (`tests/test_parse_rules.py`)
- [x] Numbers: the share of dev spans with a section number; the average number of definitions found per contract. Spot-check by hand with the 5 contracts read in Stage 0. (See the status below)

**Status (2026-10-24):** New files `db.py`, `fsm.py`, and `ledger.py`. `parse.py` got the rule part, and `stats.py` got `--n1`. There are 250 offline tests, and all pass (the 75 from Stages 0 and 1 are unchanged; after the review, 2 reference rules were fixed and 7 cases were added. See Technical decisions Section 3.5 items 12 and 13). The B0 `prompt_hash` is still `50fb0745f4aa`. Stage 2 did not call the model.

N1 statistics (`uv run python -m provenclause.eval.stats --n1 --split dev`):

| Number | dev (61 contracts) | train (423 contracts, reference) |
|---|---|---|
| Sentences with a section number | 4,296 / 5,102 = 84.2% | 80.5% |
| Contracts with at least 1 section number | 58 | 382 |
| Defined terms per contract (unique) | average 7.3, max 23; 3 contracts with 0 | average 6.2, max 50; 28 with 0 |
| References written into `refs` per contract | average 1.90, 116 in total | average 1.70 |
| Dropped references (found by `find_refs()`, not found by `section_spans()`) | 2 / 118 | 62 / 781 |

Manual spot-check of 5 contracts (compared with [Notes](notes.md) Section 3):

| Contract | Sections | Defined terms (manual count in notes, error about ±2) | References |
|---|---|---|---|
| 456 | `1`–`5`; `B. GENERAL` is not a section | 0 (0) | None |
| 411 | `1`–`7` | 12 (12), includes the reverse definition S18 | `Section 2(a) hereof` → `2`; `Section 12 of the Securities Exchange Act` is excluded |
| 3 | `1`–`16`; `a)` is not recorded | 11 (11) | None |
| 563 | `1`–`22` | 9 (9); `Receiving Party` is not found and returns an empty list | `Section 3` → `3` |
| 64 | `1`–`8`; `A.`–`M.` are subsections and are not recorded | 23 (24) | `Paragraph 6K above` → `6` |

The 3 dev contracts with no section number (405, 507, 595) use unnumbered uppercase headings. The rules do not detect this kind of heading. For details, see Technical decisions Section 3.5 "Rule changes during the build".

---

### Stage 3: per-item agent (Days 4–5, 10/24–25)

**Goal:** Unlock A1-F. Answer "how much does per-item review improve the result".

**Technology choice (2026-10-25):** See Technical decisions Section 3.6. For build details, see [Stage 3 spec](stage3-spec.md). Differences from the tasks below:
- Do not turn on strict mode; `tool_choice` uses the default `auto` (in thinking mode, `required` returns 400). When the model replies with text only, append a reminder.
- How to handle `reasoning_content`: append the model reply back to the conversation without changes.
- When the output is truncated, retry 1 time with 2 times the `max_tokens`, then give up. B0 also uses this rule.
- The output token limit for each review is `ReviewConfig.max_review_output_tokens` (default 300,000; the A1-F config changed it to 100,000 after a trial run on 5 contracts).
- During evaluation, each run has one database, `runs/<run_id>/reviews.db`; with `--resume`, a half-done contract is redone in full.
- Schedule: the technology choice was done on 10/25, and the build used the 10/26 buffer day.

**Day 4: tools + single-item loop**
1. `tools.py`: 5 tools (Detailed design M5). Each tool has a JSON Schema, an execution function, and a 10-second timeout. Do not turn on DeepSeek strict mode (Technical decisions Section 3.6 A2).
   - `search_spans` uses `rank-bm25`. Build the index only 1 time per contract, and keep it in memory.
   - Before a tool result is written into `events`, use regular expressions to remove email addresses, phone numbers, and ID numbers.
2. Single-item loop in `agent.py`: call the model → run the tool → give the result back to the model, until `record_finding` succeeds.
   - At most 8 steps. Above that, the result is `abstained` with reason `budget_exceeded`.
   - `tool_choice` uses the default `auto` (in thinking mode, `required` returns 400). When the model replies with text only, append a reminder.
   - In each round, give back the `reasoning_content` that the model returned. If you omit it while you use tools, DeepSeek returns 400. Method: append the model reply back to the conversation without changes.
   - Put the full contract text in the system prompt as a prefix that all 17 items share. DeepSeek caches shared prefixes automatically, and `cache_control` is not needed. Put the checklist question and `guidance` in the user message.
3. Write a `FakeLLM`: it returns pre-written model responses from a script. The unit tests of Stages 3 to 6 all use it. It costs nothing and gives fixed results.

**Day 5: 17 items concurrently + crash recovery**
1. Run the 17 items concurrently with `asyncio.Semaphore(4)`. Set a token limit for each review.
2. **Cache warm-up:** first run item 1 alone, then run the other 16 items concurrently. The first requests sent at the same time have no cache to read yet.
3. Check `usage.cache_read_input_tokens` (DeepSeek `prompt_cache_hit_tokens`) from item 2 on. If it is 0, check if the cached prefix has content that changes each time, for example a timestamp.
4. `recovery.py`: implement it as in Detailed design M6.
5. Run A1-F on dev.

**Acceptance criteria:**
- [x] Test (FakeLLM): all 17 items of one contract finish, and `findings` has no duplicate findings. (`tests/test_agent.py::test_seventeen_items`)
- [x] Test (FakeLLM): above 8 steps, the result is `abstained` with reason `budget_exceeded`. (`test_over_max_steps_abstains`)
- [x] Test: a tool result with a fake email address and a fake phone number is replaced after it is written into `events`. (`test_events_are_redacted`)
- [x] Test (FakeLLM): raise an exception after 5 items to simulate a crash; after the recovery function runs, all 17 items are complete with no duplicates. (`tests/test_recovery.py::test_crash_after_five_items`)
- [x] Numbers: comparison table of A1-F and B0-F on dev. Add the average step count, the call count per tool, and the cache hit rate. (See the status below)

**Status (2026-10-26):** New files `tools.py`, `agent.py`, `recovery.py`, and 2 A1 configs. `llm.py` got `create()` and the doubled retry after truncation. There are 355 offline tests, and all pass (the 250 from Stages 0–2 still pass). The B0 `prompt_hash` is still `50fb0745f4aa`. The A1 `prompt_hash` is `39fa1266b5e6`. For the rules changed during the build, see Technical decisions Section 3.6 "Rule changes during the build".

1. **Trial run (5 dev contracts):** run `a1-f_dev_20261025-201026`, $0.1136 (peak price). No errors. For items 2–17, about 90% of the input hit the cache. The largest output for one contract was 16,065 tokens. Based on this result, `max_review_output_tokens` changed to 100,000. A prompt rule was also tried ("a contract that does not state a requirement is not Contradiction", run `a1-f_dev_20261025-202007`). Accuracy did not change, so the rule was removed.
2. **A1-F results on dev** (run `a1-f_dev_20261026-003459`, 61 contracts, `git_commit` `4c297f2`, `git_dirty` is `true`: the Stage 3 code was not committed yet). B0-F is the Stage 1 run `b0-f_dev_20261021-154250`.

   | Config | Accuracy | F1 Entailment | F1 Contradiction | F1 NotMentioned | F1 Contradiction (paper style) | Evidence precision | Evidence recall | Verified precision | Coverage | Per contract | P50 | P95 |
   |---|---|---|---|---|---|---|---|---|---|---|---|---|
   | B0-F | 0.804 | 0.877 | 0.574 | 0.777 | 0.420 | 0.875 | 0.552 | 0.804 | 1.000 | $0.0052 | 34.4 s | 55.8 s |
   | A1-F | 0.795 | 0.877 | 0.571 | 0.750 | 0.434 | 0.789 | 0.708 | 0.795 | 1.000 | $0.0193 (peak price; off-peak $0.0097) | 26.1 s | 55.1 s |

   | Agent numbers (A1-F) | Value |
   |---|---|
   | Model calls | 2,259 in total, 37.0 per contract; average 2.18 steps per item, max 6 steps |
   | Tool calls per item | `search_spans` 1.46, `read_spans` 0.36, `lookup_definition` 0.33, `get_section` 0.20, `record_finding` 1.00 |
   | Reminders, truncation retries, tool errors, rejected records | All 0 |
   | Abstentions (`abstained`) | 0. No item hit the step limit or the total budget |
   | Cache hit rate (hits / all input) | All items 0.862; item 1 0.552; items 2–17 0.880. B0-F is 0.203 |
   | Output tokens | Average 9,349 per contract (B0-F 7,787), max 19,436 (contract 82); max 5,440 in a single call. Reasoning tokens are 54% (B0-F 90%) |
   | Cost | All calls ran in peak hours (Monday 07:35–07:43 UTC), $1.177 in total; at off-peak price about $0.589, which is 1.84 times B0-F. Off-peak cost breakdown: output 58%, input that missed the cache 37%, input that hit the cache 5% |
   | `reviews.db` | 14.6 MB |

3. **The accuracy difference is not significant.** A1 minus B0 is −0.0096, with a 95% confidence interval of [−0.0241, +0.0039]. The method is the same as in Stage 1: paired bootstrap by contract, 10,000 resamples, seed 20261021. Of 1,037 items, both are correct on 801, only A1 is correct on 23, only B0 is correct on 33, and both are wrong on 180 (170 of them have the same wrong answer).
4. **Evidence recall increases clearly, and precision decreases.** Recall goes from 0.552 to 0.708, and precision goes from 0.875 to 0.789: A1 cites more sentences.
5. **NotMentioned errors increase.** Gold NotMentioned items (423) given as Entailment or Contradiction: B0-F 139, A1-F 163. Entailment items are more accurate: the number given as NotMentioned drops from 23 to 9.

   | Gold answer \ model answer | B0-F: Entailment | B0-F: Contradiction | B0-F: NotMentioned | A1-F: Entailment | A1-F: Contradiction | A1-F: NotMentioned |
   |---|---|---|---|---|---|---|
   | Entailment (519) | 476 | 20 | 23 | 486 | 24 | 9 |
   | Contradiction (95) | 20 | 74 | 1 | 16 | 78 | 1 |
   | NotMentioned (423) | 70 | 69 | 284 | 87 | 76 | 260 |

6. **The 3 weakest items of B0 do not improve.**
   - nda-1 (must be clearly marked): 0.393 → 0.311. Almost all errors are Contradiction (B0 36, A1 42). The model treats "the definition lists the information by content and does not require marking" as the opposite of "must be marked".
   - nda-16 (return or destroy at termination): 0.459 → 0.459. Both have 30 NotMentioned items given as Entailment.
   - nda-20 (may keep some copies after destruction): 0.508 → 0.525. Both have 22 NotMentioned items given as Contradiction.
   - The largest drop is on nda-17 (copying allowed): 0.705 → 0.574. NotMentioned items given as Entailment go from 10 to 18. The largest gains are on nda-7 and nda-10, +0.049 each.

**Conclusion:** On dev, per-item review and tools do not increase accuracy. A1-F accuracy has no significant difference from B0-F. Evidence recall increases, the cost is 1.84 times (off-peak price), and the P50 latency is in fact shorter (4 items run concurrently). The two methods make errors on the same items. The main error is still the NotMentioned decision, and A1 is worse at it: it more often infers Entailment or Contradiction from related clauses. This supports the Stage 1 hypothesis: the bottleneck is not "17 items in one pass". It is the scope of the checklist items (`guidance`) and the way the gold answers define NotMentioned. The Stage 4 verifier (L2 rules, L3 second check) targets exactly this kind of finding. The error analysis is done on the buffer day.

---

### Buffer day 1 (Day 6, 10/26)

1. Put the A1-F errors on dev into the 7 classes of Detailed design Section 8.4, and write them to `docs/error_analysis_dev.md`.
2. Write `eval/hard_cases.py`: select H1–H5 as in Detailed design Section 8.3. It uses only the gold answers and the contracts and does not call the model, so you can write it now.
3. Add typical errors (especially cases that miss an exception clause) to `eval/regression.jsonl`.
4. If Stage 3 has unfinished tasks, finish them today.

---

### Stage 4: verifier (Days 7–8, 10/27–28)

**Goal:** Unlock A2-F. **At the end of this stage, the core system is complete.**

**L3 options to compare during the technology choice (the user decided on 2026-10-26):** The Day 8 tasks below are written for "L3 as a single call". During the Stage 4 technology choice, also discuss "L3 as an agent with read-only tools" (see the N4 row in Technical decisions Section 3). Compare the two options together, and the user decides.

**Day 7: L1 + L2 (program only)**
1. Write 2 pure functions in `verify.py`. They do not call the model:
   - `l1_check(finding, contract) -> CheckResult`
   - `l2_context(finding, contract, item) -> L2Context` (flags + extra spans to give to L3)
2. Unit tests use a small hand-written contract: one sentence with `except`, one definition, and one `Section 5` reference.
3. Numbers: run L1 and L2 on the A1-F findings on dev. Count the findings that fail L1, and how many times each flag such as `has_exception` occurs.

**Day 8: L3 + retry + three outcomes**
1. `l3_review(item, label, span_ids, l2_context) -> L3Result`. Like B0, it uses JSON mode + Pydantic validation.
   - **The function parameters do not include the agent conversation history.** Thus the code makes sure that the verifier cannot see the reasoning.
2. Retry: when L1 or L3 fails, give the specific problem back to the agent conversation as a user message, at most 2 times.
3. Give `verified`, `violated`, or `abstained` by the rules of Detailed design N4.
4. Run A2-F on dev. If coverage is too low, tune the L3 prompt on dev only.

**Acceptance criteria:**
- [ ] The L1 and L2 unit tests pass.
- [ ] Test (FakeLLM): L3 returns `fail` the 1st time and `pass` the 2nd time. The result is `verified` and `attempt` is 2.
- [ ] Test (FakeLLM): L3 returns `fail` 3 times in a row. The result is `violated`.
- [ ] Numbers: comparison of A2-F and A1-F on dev. Add verified precision, coverage, catch rate, and false-alarm rate.
- [ ] Add the tag `v0.4`. From here until Stage 7, do not change the core logic of the agent and the verifier. Only tune prompts.

---

### Stage 5: service (Day 9, 10/29)

**Goal:** Show that the system is a service. This stage is only for the demo and is not in the evaluation.

**Tasks:**
1. `report.py` (N5): output JSON and Markdown. List the "needs human review" items first, then the high-risk items, then the other items. Put a disclaimer on the first page.
2. `api.py` (M1): 3 endpoints. Start reviews with FastAPI `BackgroundTasks`. Call `recovery.py` when the service starts.
3. PDF part of `parse.py`: extract text with PyMuPDF → split it into spans by sentence and list item → run the Stage 2 section and definition detection.
4. Real crash recovery test:
   1. Start `uvicorn`, and call `POST /reviews`.
   2. Kill the process in the middle of the review. On Windows, use `taskkill /F /PID <pid>`.
   3. Restart the service.
   4. Check that the report has 17 items and no duplicate findings.

> **Note:** The 14-day plan in the Detailed design gives no time to the N5 report. `GET /reviews/{id}/report` depends on it, so this roadmap puts it on Day 9.

**Acceptance criteria:**
- [ ] Use `curl` to go through the flow "create → check progress → get report", 1 time with a dataset contract and 1 time with the demo PDF.
- [ ] The real-process crash recovery test passes.
- [ ] Save the Markdown report of the demo PDF in `docs/demo_report.md`. The README uses it.

---

### Stage 6: verifier trustworthiness (Day 10, 10/30)

**Goal:** Use numbers to show which errors the verifier can catch.

**Tasks:**
1. `eval/inject.py`: from the A2-F findings on dev that are correct, generate four types of bad findings, E1–E4, about 40 in total. Then mix in 40 correct findings.
2. Run only the verifier (L1 → L2 → L3), not the agent. This keeps the cost low, and the result shows only the verifier.
3. `eval/gate.py`:
   ```bash
   uv run python -m provenclause.eval gate --baseline runs/<baseline run_id>
   ```
   - Run once with Flash on `eval/regression.jsonl`, and compare with the baseline result.
   - If verified precision drops by more than 2 percentage points, or Contradiction F1 drops by more than 3 percentage points, return a non-zero exit code.

**Acceptance criteria:**
- [ ] Error injection result table: the catch rate for each error type, and the false-alarm rate for correct findings.
- [ ] Break the prompt on purpose one time, and `eval gate` returns a non-zero exit code.

---

### Stage 7: formal experiments and analysis (Days 11–12, 10/31–11/01)

**Before Day 11 starts:**
1. Add the tag `v0.7`. Record the prompt hash. From here on, do not change any code or prompt.
2. Run each config on 2 dev contracts first. Make sure that Flash and Pro requests have no 400 errors, and that the cost matches the estimate.

**Day 11: run the experiments**
1. Run the cheap configs first: B0-F, A1-F, A2-F.
2. Then run the expensive configs: B0-P, A1-P, A2-P. If an error occurs, continue with `--resume`.
3. Extra experiments: run A2-F on the full test set. Optional: C5.

**Day 12: analysis**
1. `eval/analyze.py` reads the results in `runs/` and generates:
   - Main table (Detailed design Section 7.3)
   - Cost-quality figure
   - Risk-coverage curves (one figure each for A2-F and A2-P, 2 curves per figure)
   - Hard-case table (H1–H5 × 6 configs)
   - Error taxonomy table
2. Save the tables and figures in `docs/figures/`. The README uses them.

**Acceptance criteria:**
- [ ] All 6 core configs have `metrics.json` on test-40, with 680 findings each.
- [ ] 1 command can regenerate all tables and figures from `runs/`.

---

### Stage 8: delivery (Days 13–14, 11/02–03)

1. README: architecture diagram, evidence example, main table, figures, error analysis, related work, data source (ContractNLI, CC BY 4.0).
2. Push to GitHub. Add the tag `v0.8`.

---

## 4. 5 decisions to make before you start

While reading the Detailed design, the author found 5 places that are not clear. Each has a recommendation. Confirm it before you start the related stage. #1, #4, and #5 were confirmed as recommended on 2026-10-21 (see Technical decisions Section 3.2); #2 and #3 are confirmed in Stages 3 and 4.

| # | Question | Affected stage | Recommendation |
|---|---|---|---|
| 1 | B0 and A1 have no verifier. How is their "verified precision" calculated in the main table? | Stage 1 | With no verifier, all findings count as `verified`. Coverage is 100%, and verified precision equals accuracy. State this in a footnote of the main table |
| 2 | N4 says that verified requires "L2 has no unhandled flags". What counts as "handled"? | Stage 4 | A flag is handled when the L2 context was given to L3 and L3 returned `pass`. Then the condition for verified is "L1 passes and L3 passes" |
| 3 | Crash recovery does `attempt + 1`. Does this count toward "at most 2 retries"? | Stage 3 | No. After recovery, the item starts again and the retry count goes back to zero. `attempt` is used only to generate a new `operation_id` |
| 4 | B0 outputs 17 findings at one time. What happens when one of them has a `span_id` that does not exist? | Stage 1 | Keep it as it is, and score it as usual. B0 has no L1, and this is exactly the error to measure. Also count the number of "span_id values that do not exist" |
| 5 | The design has no `models.py`, `llm.py`, or `configs/` | Stage 1 | Add these 3. They let B0, A1, and A2 share the same data structures, model calls, and config format |

---

## 5. What to cut when the schedule falls behind

Cut in the order of Detailed design Section 11. The table maps it to the stages of this roadmap:

| Order | What to cut | Stage |
|---|---|---|
| 1 | Optional config C5 | Stage 7 |
| 2 | Extra experiment of A2-F on the full test set | Stage 7 |
| 3 | Demo PDF parsing (the API supports only contracts in the dataset) | Stage 5 |
| 4 | Reduce error injection to E2 and E4 | Stage 6 |
| 5 | Reduce the formal experiments to test-20 | Stage 7 |

**Do not cut:** any content of Stages 1–4, the 6 core configs, the risk-coverage curves, and the hard-case analysis.

**Checkpoints to decide if the schedule is behind:**
- No B0-F result table at the end of Day 2 → use the Day 6 buffer day to catch up.
- A2-F does not run at the end of Day 8 → Stage 5 does only the API and the report, with no PDF.
- Stage 6 is not done at the end of Day 10 → reduce error injection to E2 and E4, and run the formal experiments on Day 11 as planned.
