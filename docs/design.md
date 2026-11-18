# ProvenClause: An Evidence-Grounded, Verifiable Contract Review Agent — Detailed Design

Version: v1.0 (2026-10-21, short version)
Development period: 2026-10-21 to 2026-11-03 (14 days)
Project name: ProvenClause is a working name. A GitHub search on 2026-10-21 found no repository with the same name. The name can change after the project is done.

---

## 0. Project Positioning

### One-Sentence Summary

ProvenClause reviews a contract against a checklist. Each finding comes with evidence from the contract text, and both a program and an independent model verify it. When it is not sure, it says "needs human review" and does not guess an answer.

**Core selling point:** It does not "read the contract and give a summary". Instead, "every finding points back to the source text, and the system knows when it is not reliable".

### The Problem

1. Contract review takes a lot of time. Lawyers, in-house legal teams, and bank compliance staff check contract clauses one by one every day.
2. When an LLM reads a contract directly, it makes 3 types of errors:
   - **Invented clauses:** It quotes sentences that are not in the contract.
   - **Missed exception clauses:** The contract says "may do X, except in case Y", and the model sees only the first half.
   - **It treats "not mentioned" as "permitted" or "prohibited".**
3. The user finds these 3 types of errors only when the user goes back to the source text and checks it sentence by sentence.

### Core Research Question

> **Can a verifier make a cheap model as reliable as an expensive model?**

- Cheap model: DeepSeek Flash (`deepseek-flash`, off-peak input $0.15 / million tokens).
- Expensive model: DeepSeek V4 Pro (`deepseek-v4-pro`, off-peak input $0.66 / million tokens). Its input price is about 4.4 times the Flash price. Its output price is about 3.3 times.
- On 2026-10-21 the project changed from Claude to DeepSeek. See Technical decisions Section 3.3.
- The project runs the same Agent and verifier with different models. It compares accuracy, the precision of "verified" answers, and the cost per contract.

**Why ask this question:**
- Any result gives a clear finding. If the cheap model catches up, the verifier can replace the expensive model. If it does not catch up, the project can show which error type causes the gap.
- It matches a real concern of Hong Kong banks: cost, and running small models on local servers.

### Why NDA and ContractNLI

| Reason | Details |
|---|---|
| It has ground truth | ContractNLI has 607 NDAs. Each NDA has labeled findings and evidence sentences for 17 checklist questions |
| Its structure matches the design | The dataset format is "checklist question + three labels + evidence sentences". No conversion is necessary |
| Evaluation needs no LLM grading | The finding is 1 of 3 labels and the evidence is sentence IDs, so a program can compute the score directly |
| It has a known hard part | The dataset authors say that "negation expressed through exception clauses" is the main hard part |
| The license is permissive | CC BY 4.0. A public project can use it if it credits the source |
| It is easy to learn | The 17 checklist questions are in plain English. No legal background is necessary |

### Relation to the Salus Internship

| Experience | What it makes reliable | Methods |
|---|---|---|
| Salus internship | The Agent's **execution** | Fault injection, idempotent writes, crash recovery, access control |
| ProvenClause | The Agent's **findings** | Evidence ledger, three-layer verifier, risk-coverage analysis, model comparison experiment |

The runtime (Agent loop, tool system, state machine, crash recovery) reuses the backend design from the bootcamp. This way, most of the time goes to review accuracy and evaluation. **The focus of the project is the reliability of findings and the experiment results, not the runtime.**

---

## 1. Goals, Non-Goals, and Scope Principles

### Goals

1. Review one NDA against the 17-item checklist. Give each item a finding: **Entailment / Contradiction / NotMentioned**, or **needs human review**.
2. Attach evidence sentence IDs and source text to each "Entailment" or "Contradiction" finding.
3. Three-layer verification: Is the quote real? Rule checks (exception clauses, definitions, cross-references). Independent model re-check.
4. Answer the core research question: compare 6 configurations on the same test set.
5. Do hard-case analysis, a risk-coverage curve, and error injection tests.
6. One review is one Run. It has an API, database records, a state machine, and crash recovery.

### Non-Goals (Out of Scope)

- No legal advice. The first page of the report says "A reading aid. It is not legal advice."
- No contract edit suggestions and no negotiation suggestions.
- No web UI, no login, and no multi-user support.
- No queue, real-time push, Docker, or monitoring (see Section 15 "Future extensions").
- No model training.

### Scope Principles

1. **Each component must have an experiment number to support it.** A component without a supporting number goes into "Future extensions".
2. **The author must understand each core module.** An Agent writes the code, and the author does not review it line by line. For each core module (M4 Agent loop, M6 crash recovery, N3 finding ledger, N4 verifier, M7 evaluation), the author must answer 3 questions: what it does, why it has this design, and which other options the author considered. The code size stays at about 2,000–2,500 lines (tests included, rough estimate). Less code is easier to explain.
3. **Measure first, then add complexity.**
4. **Use the cheap model by default during development.** Flash costs very little, so it is fine to try things freely. The expensive model can run reference results on dev. The formal experiments run only at the end (changed on 2026-10-21, see roadmap Section 0, item 4).

---

## 2. User Scenario and Output Example

### 2.1 User Flow

1. Call `POST /reviews`. Upload one NDA (or select a contract from the dataset), and select the checklist `nda_17` and a model configuration.
2. The system returns a `review_id` and starts the review in the background.
3. Call `GET /reviews/{id}` to see progress: how many of the 17 items are done.
4. When the review is done, call `GET /reviews/{id}/report` to get the report.

### 2.2 One Finding in the Report

```json
{
  "item_id": "nda-5",
  "item_name": "Sharing with employees",
  "hypothesis": "Receiving Party may share some Confidential Information with some of Receiving Party's employees.",
  "label": "Entailment",
  "label_display": "The contract supports this statement",
  "outcome": "verified",
  "evidence": [
    {"span_id": 23, "section": "3.2", "text": "Recipient may disclose Confidential Information to its employees who have a need to know ..."}
  ],
  "rationale": "Section 3.2 permits disclosure to employees who need to know.",
  "support_score": 92,
  "checks": {"L1": "pass", "L2": "pass", "L3": "pass"}
}
```

`outcome` has 3 values: `verified` (verified), `violated` (verification failed, needs human review), and `abstained` (abstention, needs human review).

---

## 3. Overall Architecture

### 3.1 The Life of One Review

```
M1 Create    POST /reviews  →  returns review_id
   │
M2 Persist   add one row to reviews table (status = created); save the contract
   │
   │      (Queue: not built. Start a background task directly)
   │
M3 State     created → parsing
   │      N1 Contract parsing: split into sentences (spans), sections, definitions; write to spans table
   │
M3 State     parsing → reviewing
   │
M4 Loop + M5 Tools   17 checklist items, at most 4 at the same time. For each item:
   │      N2 read checklist → Agent reads contract, looks up definitions, follows cross-references
   │      → N3 record finding (write to findings table)
   │      → N4 verifier L1 / L2 / L3 → on failure, send back to redo (at most 2 times)
   │      ← M6 Recovery: save a checkpoint when each item ends. After an interrupt, restart and do only unfinished items
   │
M3 State     reviewing → completed (or failed)
   │      N5 Generate report
   │
   │      (Real-time push: not built. Progress events go to events table; query them with GET)
   │
M7 Feedback  Evaluation: ContractNLI scoring; error cases go into the regression set
```

### 3.2 Module List (12 Modules)

**7 modules ported from the bootcamp backend framework:**

| ID | Module | Bootcamp stage | Size |
|---|---|---|---|
| M1 | API | 01 Create | Minimal: 3 endpoints |
| M2 | Database (SQLite) | 02 Persist | Minimal: 6 small tables |
| M3 | State machine | 04 State machine | Minimal: review level + checklist-item level, about 80 lines |
| M4 | Agent loop (with budget, retry) | 05 Execute | Core |
| M5 | Tool system (with timeout, log redaction) | 06 Call tools | Core |
| M6 | Crash recovery | 08 Recover | Minimal: checkpoint per checklist item |
| M7 | Evaluation and regression testing | 10 Feedback | Core |

**5 new modules for contract review:**

| ID | Module | What it does |
|---|---|---|
| N1 | Contract parsing | Contract → sentences (spans), sections, definitions |
| N2 | Checklist | Config file for the 17 checklist questions |
| N3 | Finding ledger | Binds each finding to evidence sentences and writes it to the database |
| N4 | Verifier | L1 quote check, L2 rule checks, L3 independent model re-check, three outcomes |
| N5 | Report generation | Organizes the report by risk and outcome |

**3 stages not built:** 03 Queue, 07 Push events, 09 Production hardening. The budget, timeout, and log redaction parts of 09 go into M4 and M5.

### 3.3 Design Principles

1. **A finding must have evidence.** "Entailment" and "Contradiction" must quote at least 1 sentence. "NotMentioned" must not quote any sentence.
2. **Program verification first, then model verification.** Program checks are cheap, deterministic, and explainable.
3. **The verifier cannot see the Agent's reasoning.** It sees only the checklist question, the finding, the evidence, and the context that the program adds automatically.
4. **Abstain when not sure.** It is better to say "needs human review" than to give a finding that can be wrong.

---

## 4. Data

### 4.1 ContractNLI (Main Evaluation Set)

Facts checked after downloading the official data package on 2026-10-21:

| Item | Value |
|---|---|
| Total contracts | 607 NDAs |
| Split | train 423 / dev 61 / test 123 |
| Contract length | About 11,000 characters on average, 54,571 characters at most (at 4 characters ≈ 1 token, about 2,800 tokens on average) |
| Sentences (spans) per contract | About 77–81 on average |
| Checklist questions per contract | 17, the same for all contracts |
| Label distribution in test | Entailment 968, NotMentioned 903, Contradiction 220 (123 × 17 = 2,091 in total) |
| Checklist items with the most "Contradiction" in test | nda-2 (88), nda-1 (36), nda-20 (30), nda-7 (25), nda-17 (25) |
| License | CC BY 4.0, plus terms of use from Hitachi America |
| Download URL | https://stanfordnlp.github.io/contract-nli/ |

**Format:** Each contract has the full text `text` and sentence boundaries `spans`. The label for each checklist question is `choice` (Entailment / Contradiction / NotMentioned) plus evidence sentence IDs `spans`.

**Note:** `kiddothe2b/contract-nli` on Hugging Face is a "sentence-level" rewrite (one passage and one question), not full contracts. **Use the official data package.**

**Important finding: NDAs are short.** They are about 2,800 tokens on average and about 14,000 tokens at most, so a full contract fits in the context directly. So the Agent's value is not "finding the relevant passage". Its value is:
1. It reviews item by item, and each item focuses on one question.
2. It looks up definitions and follows cross-references.
3. When the verifier sends a finding back, it checks again based on the feedback.

The B0 baseline (review all 17 items on the full contract in one call) tests this claim.

### 4.2 The 17 Checklist Questions

| ID | Name | Checklist question (original text) |
|---|---|---|
| nda-1 | Explicit identification | All Confidential Information shall be expressly identified by the Disclosing Party. |
| nda-2 | None-inclusion of non-technical information | Confidential Information shall only include technical information. |
| nda-3 | Inclusion of verbally conveyed information | Confidential Information may include verbally conveyed information. |
| nda-4 | Limited use | Receiving Party shall not use any Confidential Information for any purpose other than the purposes stated in Agreement. |
| nda-5 | Sharing with employees | Receiving Party may share some Confidential Information with some of Receiving Party's employees. |
| nda-7 | Sharing with third-parties | Receiving Party may share some Confidential Information with some third-parties (including consultants, agents and professional advisors). |
| nda-8 | Notice on compelled disclosure | Receiving Party shall notify Disclosing Party in case Receiving Party is required by law, regulation or judicial process to disclose any Confidential Information. |
| nda-10 | Confidentiality of Agreement | Receiving Party shall not disclose the fact that Agreement was agreed or negotiated. |
| nda-11 | No reverse engineering | Receiving Party shall not reverse engineer any objects which embody Disclosing Party's Confidential Information. |
| nda-12 | Permissible development of similar information | Receiving Party may independently develop information similar to Confidential Information. |
| nda-13 | Permissible acquirement of similar information | Receiving Party may acquire information similar to Confidential Information from a third party. |
| nda-15 | No licensing | Agreement shall not grant Receiving Party any right to Confidential Information. |
| nda-16 | Return of confidential information | Receiving Party shall destroy or return some Confidential Information upon the termination of Agreement. |
| nda-17 | Permissible copy | Receiving Party may create a copy of some Confidential Information in some circumstances. |
| nda-18 | No solicitation | Receiving Party shall not solicit some of Disclosing Party's representatives. |
| nda-19 | Survival of obligations | Some obligations of Agreement may survive termination of Agreement. |
| nda-20 | Permissible post-agreement possession | Receiving Party may retain some Confidential Information even after the return or destruction of Confidential Information. |

The IDs are not continuous (there is no nda-6, 9, or 14). These are the original IDs from the dataset.

### 4.3 How the Data Is Used

| Data | Use | Size |
|---|---|---|
| dev (61 contracts) | Development and prompt tuning; material for error injection; source of the regression set | 61 × 17 = 1,037 items |
| **test-40** (40 contracts sampled from test with a fixed random seed) | **Formal experiments for the core research question**. All 6 configurations run on this set | 40 × 17 = 680 items |
| Full test (123 contracts) | Only the cheap-model configurations run here, as extra results | 2,091 items |
| train | Not used for training. Used only to pick a few examples for the prompts | — |
| Demo contracts (2–3) | Public NDA or rental contract PDFs, used to demo PDF parsing and the API | — |

**Rule:** Tune prompts on dev only. Run test only for the formal experiments on day 11.

---

## 5. New Modules

### N1 Contract Parsing

**Two input sources, one output structure:**

| Source | Method |
|---|---|
| ContractNLI | Read `text` and `spans` directly. No new sentence split is necessary. This way the evidence IDs match the ground truth |
| Demo PDFs | PyMuPDF extracts the text → split into spans by sentence and list item |

**Steps for both sources (built in stage 2 as `annotate_contract()`; `load_contractnli()` calls it by default):**
1. **Section detection:** A regular expression detects numbers at the start of a sentence, such as `1.`, `1.1`, `1.01`, `ARTICLE 1 -`, `Article II`, and `Section 5.`. `Span.section` records the deepest "dotted number", for example `"2"` or `"2.06"`. `ARTICLE 1` becomes `"1"`, and Roman numerals stay as they are (`"II"`). Bracket numbers such as `(a)`, `(i)`, and `a)` are not recorded. Sentences after a heading keep this number until the next heading. Numbers must appear in order: equal to the previous one, plus 1, a restart from 1, or a forward jump that the next heading confirms. When a contract has numeric headings, `A.` and `B.` are not sections.
2. **Definition detection:** Detect sentences such as `"Confidential Information" means ...`, `... (the "Agreement")`, and `is referred to as "Buyer"`. Straight quotes, curly quotes, and single quotes all match. The terms that a sentence defines go into `Span.defines` (in order of appearance, without duplicates). `is_definition == bool(defines)`. `definition_spans(contract, term)` finds definition sentences by term, case-insensitive, and returns an empty list when it finds none.
3. **Cross-reference detection:** Detect references such as `Section 5`, `Clause 1.2(a)`, `Paragraph 4E`, and `Article II`, and normalize them to section numbers (`"5"`, `"1.2"`, `"4"`, `"II"`). When `of ...` follows the number (but not `of this ...`, such as `of this Agreement` or `of this letter agreement`), the reference points outside the contract and is excluded, for example `Section 12 of the Securities Exchange Act`. `Span.refs` stores only the numbers that `section_spans()` can find in this contract.

**Output:** Write to the `spans` table. Each span has: `span_id`, start and end positions, text, section number, whether it is a definition, the defined terms, and the referenced sections.

### N2 Checklist

One YAML file, `checklists/nda_17.yaml`. Each item contains:

```yaml
- id: nda-5
  name: Sharing with employees
  hypothesis: "Receiving Party may share some Confidential Information with some of Receiving Party's employees."
  guidance: >
    Entailment if the contract permits disclosure to employees (even with conditions such as need-to-know).
    Contradiction if it forbids disclosure to all employees.
    NotMentioned if employees are not addressed.
  risk_label_for_recipient: Contradiction   # which label is high risk for the receiving party
```

- The author writes `guidance` in the author's own words. The paper appendix does not publish annotation guidelines, so the author wrote it from the original checklist questions and the evidence examples in train (see [Notes](notes.md) Section 1.3).
- For a different contract type, change only this file. The code does not change.
- **So the core code does not hard-code any specific checklist item** (added on 2026-10-25). The stage 3 Agent prompt, the stage 4 L2 rules, and the stage 5 report can use only `ChecklistItem` fields (for example `guidance` and `risk_label_for_recipient`). They must not contain rules that work for only one question, such as "if it is nda-2, then ...". Special instructions for one question go into its `guidance`. Only the evaluation code (`eval/`) can hard-code `nda_17`, because the ground truth covers only these 17 items. With this rule, "users upload a checklist" in Section 15 needs only one new entry point later.

### N3 Finding Ledger

Each attempt on each checklist item writes one record (`findings` table):

| Field | Description |
|---|---|
| review_id, item_id, attempt | Which review, which checklist item, which attempt |
| label | Entailment / Contradiction / NotMentioned |
| span_ids | Evidence sentence IDs (list) |
| rationale | The reason, in one sentence |
| reason | The abstention reason. Matches `Finding.reason` |
| self_confidence | The Agent's self-reported confidence (0–100). Used to compare with the verifier's score |
| checks | Results of the three-layer verification, JSON: `{"L1": ..., "L2": ..., "L3": ...}`. Matches `Finding.checks` (decision on 2026-10-23, replaces the three columns `l1`, `l2`, `l3`) |
| support_score | The support score from L3 (0–100) |
| outcome | verified / violated / abstained |
| operation_id | A readable string `"{review_id}:{item_id}:{attempt}"` with a unique index. A repeated write does not create two records |
| finding_id, created_at | Auto-increment ID and write time |

**Record rules** (pure function `validate_finding()`. It returns all problems at once, in English. The stage 4 L1 also calls it):
1. "Entailment" and "Contradiction" must have at least 1 `span_id`. "NotMentioned" must have none. If a finding breaks this rule, `record_finding` returns an error directly.
2. Each `span_id` must exist in this contract.
3. `span_ids` must not contain duplicate IDs (input check added in stage 2).
4. When `self_confidence` is not null, it must be between 0 and 100 (input check added in stage 2).

**Record process (`record_finding()`):** For an invalid finding, it writes a `finding_rejected` event and does not write to the `findings` table, so the finding does not use an `operation_id`. For a valid finding, it uses `INSERT ... ON CONFLICT(operation_id) DO NOTHING`. A repeated write returns the existing record with `duplicate=True` and does not overwrite it. `record_finding()` does not change the state of the checklist item.

### N4 Verifier

#### L1 Quote Check (Program)

1. Does each `span_id` exist in this contract?
2. Does the finding follow the rules for the number of evidence sentences (see N3 record rules)?
3. If `rationale` quotes the source text, does the quoted text really appear in that span?

#### L2 Rule Checks (Program)

L2 does not decide right or wrong directly. Instead, it **finds the context that L3 must see**, and it flags risks:

| Check | Rule | Result |
|---|---|---|
| Exception clause | An evidence sentence, or another sentence in the same section, contains `except`, `provided that`, `unless`, `notwithstanding`, `subject to`, `other than`, or `save that` | Add these sentences to the L3 input and set the `has_exception` flag |
| Definition | An evidence sentence uses a defined term that starts with a capital letter (for example Confidential Information) | Add the definition sentence of this term to the L3 input |
| Cross-reference | An evidence sentence references another section (for example "Section 5") | Add the sentences of that section to the L3 input |
| "NotMentioned" coverage | When the finding is "NotMentioned", search the full text with keywords from the checklist question | Add the high-scoring sentences found to the L3 input, so that L3 confirms that the contract really does not mention it |

#### L3 Independent Model Re-check

- **Input:** The checklist question, `guidance`, the Agent's finding, the evidence sentences, and the context from L2 (exception clauses, definitions, cross-references, search results).
- **Not in the input:** The Agent's reasoning and the conversation history.
- **Output (structured):**

```json
{"verdict": "pass" | "fail" | "uncertain", "support_score": 0-100, "issue": "the problem, in one sentence"}
```

- **Default model:** The same model as the Agent. This way the total cost of "cheap model + verifier" stays low. The optional configuration C5 makes L3 use the expensive model (see Section 7).

#### Three Outcomes

| Outcome | Condition |
|---|---|
| **verified** | L1 and L3 pass; L2 has no unhandled flags |
| **violated (verification failed)** | After 2 retries, L1 or L3 still fails |
| **abstained (abstention)** | L3 returns `uncertain`; or the budget runs out; or the model refuses to answer (reason `refusal`) |

#### Failure Handling

1. L1 or L3 fails → send the specific problem back to the Agent, for example "span 41 contains the exception clause 'except as required by law', and your finding does not consider it".
2. Retry at most 2 times.
3. Still fails → the outcome is `violated`. The report marks it "needs human review" and gives the reason.

### N5 Report Generation

- Output two formats: JSON and Markdown.
- Order: first the "needs human review" items, then the high-risk items by `risk_label_for_recipient`, then the other items.
- Each item comes with the evidence source text and section number.
- The first page of the report says: a reading aid, not legal advice.

---

## 6. Ported Modules

The modules below are **rewritten as short versions** that follow the ideas of the bootcamp framework. They do not copy code from the bootcamp or Salus directly (see Section 16).

### M1 API (FastAPI)

| Endpoint | Purpose |
|---|---|
| `POST /reviews` | Upload a PDF, or select a `contract_id` from the dataset. Parameters include `checklist_id` and `model_config`. Returns `202` and a `review_id`, and starts the review as a background task |
| `GET /reviews/{id}` | Returns the status and progress (the count of each state across the 17 items) |
| `GET /reviews/{id}/report` | Returns the report (JSON or Markdown) |

The evaluation scripts **do not use the API**. They call the core functions directly, which is faster and easier to debug. The API is for demos, and it also shows that the system is a service.

### M2 Database (SQLite, 6 Tables)

Stage 2 built 6 tables (the decision on 2026-10-23 added `review_items`, see Technical decisions Section 3.5). For the table schema, see `src/provenclause/db.py`:

```
contracts    (contract_id, source, title, text, n_spans, created_at)
spans        (contract_id, span_id, char_start, char_end, text, section, is_definition, defines, refs)
reviews      (review_id, contract_id, checklist_id, model_config, status, created_at, updated_at, error)
review_items (review_id, item_id, status, attempt, retries, updated_at)   -- checklist item state, used by M3 and M6
findings     (finding_id, operation_id, review_id, item_id, attempt, label, span_ids, rationale,
              reason, self_confidence, checks, support_score, outcome, created_at)   -- see N3, operation_id is unique
events       (review_id, seq, ts, type, item_id, payload)   -- audit log: state changes, records; stage 3 adds model calls, tool calls
```

- All tables are `STRICT` tables. WAL and `foreign_keys` are on. The schema version is in `PRAGMA user_version` (now 1).
- The start and end columns of `spans` are `char_start` and `char_end`, because `END` is an SQL keyword.
- Lists and dicts (`defines`, `refs`, `span_ids`, `checks`, `payload`) are stored as JSON text. Times are UTC ISO 8601 text.
- The code uses the standard library `sqlite3`, with synchronous calls and autocommit. To commit several statements together, it uses `transaction()` (`BEGIN IMMEDIATE`).
- Event types (stage 2): `review_created`, `review_status`, `item_status`, `finding_recorded`, `finding_rejected`.

### M3 State Machine

**Review level:**

```
created → parsing → reviewing → completed
   └───────┴──────────┴──────→ failed
```

**Checklist-item level:**

```
pending → drafting → verifying → verified
             ↑           │    ├→ violated
             └─ retry ───┘    └→ abstained

drafting → abstained                    (added in stage 2)
drafting, verifying → pending (resume)  (added in stage 2)
```

| From | Can go to |
|---|---|
| pending | drafting |
| drafting | verifying, abstained, pending |
| verifying | verified, violated, abstained, drafting, pending |
| verified, violated, abstained | (final states) |

- Stage 2 added 3 edges: `drafting → abstained` (budget overrun or refusal before the record); `drafting → pending` and `verifying → pending` (M6 recovery). A1 has no verifier, but it also goes `drafting → verifying → verified`.
- **Counters:** `verifying → drafting` (verification failed, retry) adds 1 to both `attempt` and `retries`. A return to `pending` (recovery) adds 1 to `attempt` only. Other state transitions do not change the counters. The new `attempt` gives the next record a new `operation_id`. The caller reads `retries` to apply "retry at most 2 times".
- The two functions `transition_review()` and `transition_item()` first check the transition table and raise `InvalidTransition` for an invalid transition. For a valid transition, they use "compare, then write": `UPDATE ... WHERE status = :from`. If 0 rows change, the actual state is not `from`, and they also raise `InvalidTransition`.
- The state change and the `events` write happen in the same transaction. Each state transition writes 1 event.

### M4 Agent Loop

Built in stage 3 (`agent.py`). For the technology choice, see Technical decisions Section 3.6.

- Each checklist item runs one Agent loop: call the model → run tools → send results back to the model, until `record_finding` records a finding successfully.
- **The full contract goes into the system prompt**, with an ID before each sentence, for example `[S23] Recipient may disclose ...`. system = general instructions + full contract. It is identical for all 17 items and contains no timestamp, `review_id`, or checklist item ID. DeepSeek caches this shared prefix automatically. The checklist question and `guidance` go into the user message.
- **Concurrency and cache warm-up:** Item 1 runs alone first, so that the contract goes into the cache. Then the other 16 items run with `asyncio.Semaphore(4)`, at most 4 at the same time.
- **Reminder (C2):** In thinking mode, `tool_choice` cannot be `required`. When the model replies with text only and calls no tool, the loop adds a user message that asks it to call `record_finding`. A reminder counts as 1 step.
- **Steps (C3):** 1 model call counts as 1 step. Each item has at most `max_steps` (8) steps. Several tool calls in one reply run in order and count as 1 step. The loop stops after `record_finding` succeeds, and later calls in the same reply do not run. When the record is rejected, the error goes back to the model as a tool result, and the step still counts. Step limit exceeded: `abstained`, reason `budget_exceeded`.
- **Total budget per review (C4):** `max_review_output_tokens` limits the total output tokens of one review (100,000 in the A1-F configuration). The loop checks it before each model call. When the budget runs out, all unfinished items become `abstained`, reason `budget_exceeded`. Events use `kind` to tell `steps` and `review_tokens` apart.
- **Truncation (C6):** When `finish_reason` is `length`, the loop redoes the same call 1 time with 2 times the `max_tokens`. The truncated reply does not go into the conversation history, but its cost still counts. The redo counts in the same step. When the total budget is used up, there is no redo. If the second call is also truncated: `abstained`, reason `max_tokens`.
- **Refusal:** `content_filter` → `abstained`, reason `refusal`.
- **The difference between `failed` and `abstained`:** `abstained` means the model gave no usable answer (refusal, truncation, budget overrun). It affects only this item. `failed` means an infrastructure problem: a server abort (`insufficient_system_resource`, `aborted`), or the SDK still fails after 4 retries (429, 5xx, network errors). Then the whole review becomes `failed`, the other items are cancelled, and the exception goes to the caller. Infrastructure failures do not count in coverage.
- Each model call and tool call is written to `events` (finding, thinking content, tool arguments and results, usage), and the text is redacted before the write. The run statistics of each item (steps, call count per tool, tool errors, rejected records, reminders, truncation redos) go into `Finding.stats`. The evaluation computes them from `findings.jsonl`.

### M5 Tool System (5 Read-Only Tools)

| Tool | Input | Output | Purpose |
|---|---|---|---|
| `search_spans` | query, top_k=8 | Sentence IDs + text + scores | BM25 search. Used to confirm "NotMentioned" and to find related clauses in different places |
| `read_spans` | span_ids, window=1 | These sentences plus 1 sentence before and after each | See context |
| `lookup_definition` | term | Definition sentences | Look up a term's definition |
| `get_section` | section | All sentences of the section | Follow cross-references |
| `record_finding` | label, span_ids, rationale, self_confidence | Success, or the error reason | Record the finding (N3), then go to the verifier |

Built in stage 3 (`tools.py`). For the technology choice, see Technical decisions Section 3.6.

- **Result format:** Text, one sentence per line, `[S23] (§2.06) ...`. Each line starts with `[S{id}]`, the same as the contract in the system prompt. When there is no section number, `(§...)` is left out. An error starts with `ERROR:`.
- **Limits:** `top_k` 1–20; `window` 0–3; `read_spans` takes at most 10 IDs; `get_section` returns at most 40 sentences, followed by one line that says how many sentences remain.
- **`search_spans`:** `BM25Okapi` from `rank-bm25`. Tokenization: lowercase, `[a-z0-9]+`, remove 31 English stop words, no stemming. The index is built 1 time per contract and shared by the 17 items. Only sentences with a score above 0 are returned.
- **`lookup_definition`:** First look up the exact term. If not found, try without a leading `the`, then try singular and plural forms. If still not found, list the defined terms in the contract (at most 40).
- **`get_section`:** `Section 2(a)` finds `2`. When `2.9` does not exist, it falls back to `2`. If not found, list the section numbers of the contract.
- **Argument validation:** Each tool has one Pydantic argument model (`extra="forbid"`), and the same model generates the JSON Schema. DeepSeek strict mode is not on. For arguments that are not JSON, missing fields, extra fields, values out of range, IDs that do not exist, and tool names that do not exist: the error goes back to the model as a tool result and is counted. `record_finding` has no `item_id` argument, so the model cannot record a finding for another item.
- **Timeout:** The 4 read-only tools use `asyncio.wait_for(asyncio.to_thread(...), 10)`. A thread cannot be stopped, so after a timeout it finishes in the background and its result is discarded. `record_finding` needs the SQLite connection, so it runs directly in the event loop thread.
- **Redaction:** All text written to `events` is redacted, including model replies, thinking content, tool arguments, and tool results. The code replaces emails, Chinese ID numbers, US SSNs, and phone numbers (US format, international format with `+`, Chinese mobile numbers). The model sees the original text. In the 61 dev contracts, redaction matched 7 emails and 24 phone numbers. A manual check showed that all of them are real contact details.

### M6 Crash Recovery

Built in stage 3 (`recovery.py`). For the technology choice, see Technical decisions Section 3.6.

- When a checklist item enters a final state (verified / violated / abstained), the code writes it to the database. An abandoned item also gets a placeholder finding row (`NotMentioned`, no quotes, with the reason).
- When the service restarts, `recover()`:
  1. Finds reviews with status `created`, `parsing`, or `reviewing`. `created` also needs recovery. Otherwise a review that crashes right after creation never starts.
  2. Skips checklist items that are already done.
  3. Moves checklist items stuck in `drafting` or `verifying` back to `pending`, with `attempt + 1` and `retries` unchanged. So the new record has a new `operation_id` and does not conflict with the old finding.
  4. Changes only the database and calls no model. If it is called 2 times in a row, the second call changes nothing.
- `resume_review()` moves the review forward to `reviewing`, then runs only the `pending` items. The final result uses the finding of each item's current `attempt`.
- **Crash after the finding is recorded but before the state changes:** This item stays in `drafting`, so it also goes back and is redone. The finding of the old `attempt` stays in the table and is not used.
- **What counts as a crash:** The review loop treats only `Exception` as failure (the review becomes `failed`). A `BaseException` such as `KeyboardInterrupt` or `CancelledError` does not change the state. This is the same as a killed process: the review stays in `reviewing`, and M6 recovers it.
- **Evaluation resume does not use M6:** When `eval run --resume` finds a half-done contract, it redoes the whole contract with a new `review_id`. Reason: cost and latency must be computed for one complete review. These are the numbers in the stage 7 main table. The stage 5 API calls M6 at startup.
- Test method (FakeLLM): after 5 items finish, raise a `BaseException` to simulate a killed process. After recovery, check that all 17 items are complete and that there are no duplicate findings.

### M7 Evaluation and Regression Testing

- `eval run --config <config> --split dev|test40|test`: runs the evaluation and writes results to `runs/<run_id>/`, including metrics, each finding, and `manifest.json` (git commit, model, effort, prompt hash, random seed).
- **Regression set:** Each typical error found on dev (for example a case with a missed exception clause), plus the error injection cases from Section 8.5, goes into `eval/regression.jsonl`.
- **Regression gate:** After a change to a prompt or the code, run Flash once on the regression set. Compare with the previous baseline result. If verified precision drops by more than 2 percentage points, or the "Contradiction" F1 drops by more than 3 percentage points, the change is a "regression" and is not accepted. One Flash run costs only a few cents.

---

## 7. Core Research Question: Experiment Design

### 7.1 Configurations

| Configuration | Method | Model | Question it answers |
|---|---|---|---|
| B0-F | Review all 17 items on the full contract in one call, no tools, no verification | DeepSeek Flash | How well does the simplest method work? |
| B0-P | Same as above | DeepSeek V4 Pro | Same as above, expensive model |
| A1-F | Item-by-item Agent + tools, no verifier | DeepSeek Flash | How much does item-by-item review improve results? |
| A1-P | Same as above | DeepSeek V4 Pro | Same as above, expensive model |
| **A2-F** | Item-by-item Agent + tools + three-layer verifier | DeepSeek Flash | **How much can the verifier improve the cheap model?** |
| **A2-P** | Same as above | DeepSeek V4 Pro | Does the verifier still help the expensive model? |
| C5 (optional) | Agent uses Flash, L3 uses Pro | Mixed | Is it the best value when the cheap model does the work and the expensive model checks it? |

Configuration name suffixes: `-F` is Flash, `-P` is Pro. All 6 core configurations run on **test-40**. A2-F also runs once on the full test set.

### 7.2 Controlled Variables

- Prompts, tools, checklist, and verification rules are identical in all 6 configurations. Only the model and "verifier on or off" change.
- Both models set `effort: high` explicitly, and `manifest.json` records it.
- The random seed is fixed.

### 7.3 Findings to Report

1. **Main table:** For the 6 configurations: accuracy, "Contradiction" F1, verified precision, coverage, cost per contract, and P50 / P95 latency.
2. **Core comparison:** A2-F against A1-P, that is, "cheap model + verifier" against "expensive model without verification".
3. **Cost-quality chart:** The x-axis is the cost per contract, and the y-axis is verified precision. Each of the 6 configurations is one point.
4. **Where the gap is:** If A2-F does not catch up with A2-P, use the hard-case categories from Section 8.3 to show which category causes the gap.

---

## 8. Evaluation Plan

### 8.1 Metrics (No LLM Grading)

| Metric | Definition |
|---|---|
| Accuracy | The share of findings that match the ground truth (all 680 items) |
| Per-label F1 | Computed separately for Entailment, Contradiction, and NotMentioned. **The "Contradiction" F1 is the focus** |
| Evidence precision / recall | Computed only for items whose ground truth is "Entailment" or "Contradiction": how many of the sentences the Agent quotes are in the gold evidence; how many of the gold evidence sentences the Agent quotes |
| Verified precision | Among items with outcome verified, the share with a correct finding. **The most important metric** |
| Coverage | Items with outcome verified ÷ all items |
| False accepts | The number of items that are verified but have a wrong finding. Each one goes into the error analysis |
| Catch rate / false-alarm rate | Wrong findings the verifier stops ÷ all wrong findings; correct findings the verifier stops ÷ all correct findings |
| Cost and latency | US dollar cost per contract; P50 / P95 seconds per contract |

**Existing baselines:**
- The original ContractNLI paper: fine-tuned BERT-large, accuracy .875, "Contradiction" F1 .357 (Table 4 of the original paper checked on 2026-10-21). Its evaluation setup is different from ours, see [Notes](notes.md) Section 1.
- A 2026 paper that compares small models: GPT-4o-mini about 76.6% (the task format can be different).

### 8.2 Risk-Coverage Curve

**Purpose:** Show "how many items the system can cover if it must reach X% precision".

**Method:**
1. Each finding has two scores: the Agent's self-reported `self_confidence`, and the `support_score` from L3.
2. Sort by score from high to low. Keep only the top 10%, 20%, ... 100%, and compute the accuracy of each.
3. Draw two curves: the x-axis is coverage, and the y-axis is accuracy. One curve sorts by `self_confidence`, and one sorts by `support_score`.
4. Draw one chart each for A2-F and A2-P.

**Questions to answer:**
- Is the verifier's score more reliable than the model's self-reported confidence? (If it is, the L3 curve is higher.)
- At a required accuracy of 95%, how many items can the cheap model and the expensive model each cover?

### 8.3 Hard-Case Analysis

Split the test-40 items into 5 hard-case categories, and report the accuracy of the 6 configurations for each category:

| ID | Hard-case category | How to select |
|---|---|---|
| H1 | Ground truth is "Contradiction" | Label |
| H2 | The evidence contains an exception clause | Gold evidence sentences contain an L2 exception keyword |
| H3 | Ground truth is "NotMentioned" | Label. Focus on cases wrongly labeled "Entailment" or "Contradiction" |
| H4 | nda-2 ("only includes technical information") | The checklist item with the most "Contradiction" in test |
| H5 | Long contracts | Contracts in the top 25% by character count |

### 8.4 Error Taxonomy

After each evaluation, put each error into one of the categories below, and write them into the README:

1. Missed exception clause
2. Wrong understanding of a definition
3. Did not follow a cross-reference
4. Treated "NotMentioned" as "Entailment" or "Contradiction"
5. Wrong evidence (the finding is right, but the evidence is wrong)
6. Wrong understanding of the checklist question
7. The ground truth itself has a problem

### 8.5 Error Injection Tests

**Method:** Take findings that A2-F got right on dev, and break them with a program to make about 40 "bad findings". Mix in the same number of correct findings, and let the verifier check them.

| ID | Error | How to change | Which layer must stop it |
|---|---|---|---|
| E1 | Fake evidence | Replace a `span_id` with an ID that does not exist, or change the quoted text | L1 |
| E2 | Flipped finding | Keep the evidence, and change "Entailment" to "Contradiction", or the reverse | L3 |
| E3 | Swapped evidence | Replace it with a sentence from the same contract that contains the same keywords but does not support the finding | L3 |
| E4 | Removed exception | When the finding depends on an exception clause, remove the exception sentence from the evidence and change the finding to the opposite | L2 + L3 |

**Report:** The catch rate of each error type, and the false-alarm rate on correct findings.

---

## 9. Models and Cost

### 9.1 Models

| Role | Cheap configuration | Expensive configuration |
|---|---|---|
| Agent | `deepseek-flash` (DeepSeek-V4.1-Flash) | `deepseek-v4-pro` (DeepSeek-V4-Pro) |
| L3 verification | Same as the Agent (except C5) | Same as the Agent |

Prices (DeepSeek official, checked on 2026-10-21, US dollars / million tokens, off-peak / peak):

| Model | Input (cache miss) | Input (cache hit) | Output |
|---|---|---|---|
| `deepseek-flash` | 0.15 / 0.30 | 0.003 / 0.006 | 0.60 / 1.20 |
| `deepseek-v4-pro` | 0.66 / 1.32 | 0.022 / 0.044 | 1.98 / 3.96 |

- Peak hours are 01:00–04:00 and 06:00–10:00 UTC, Monday to Friday. The peak price is 2 times the off-peak price.
- Cache writes are free.
- Both models have a context of 1 million tokens and a maximum output of 384,000 tokens.

### 9.2 Cost Estimate (Updated with Measured Values from B0-F on 2026-10-21 and A1-F on 2026-10-26)

**Measured (B0-F, 61 dev contracts, run `b0-f_dev_20261021-154250`, off-peak):**

| Item | Value |
|---|---|
| Total cost | $0.320, $0.0052 per contract ($0.0106 at most) |
| Input tokens | Cache miss 229,346; cache hit 58,368 (20%) |
| Output tokens | 474,985, of which thinking 427,714 (90%) |
| Cost breakdown | Output about 89%, input about 11% |
| Latency | P50 34.4 seconds, P95 55.8 seconds; maximum output 17,649 tokens |

**Measured (B0-P, 61 dev contracts, run `b0-p_dev_20261021-162737`, off-peak):** Total cost $1.152, $0.0189 per contract ($0.0359 at most). Output 503,152 tokens, of which thinking 442,697. Maximum output 17,247 tokens. P50 56.0 seconds, P95 106.1 seconds.

**The assumption "Pro uses about as many thinking tokens as Flash" holds.** On the same 61 contracts, Pro's output tokens are 1.06 times Flash's, and its thinking tokens are 1.04 times. So Pro's cost is 3.60 times Flash's. This is a little higher than the 3.3 times from the output price alone, because Pro's input price is 4.4 times Flash's and its output is also 6% larger. The Pro estimates in the table below use 3.6 times.

**Measured (A1-F, 61 dev contracts, run `a1-f_dev_20261026-003459`, 2026-10-26, all in peak hours):** Total cost $1.177, which is $0.589 at off-peak prices (exactly half the price). That is $0.0097 per contract, 1.84 times B0-F. Each contract makes 37.0 calls. Input: cache miss 1,460,429, cache hit 9,151,739 (86%). Output 570,307 tokens, of which thinking 308,591 (54%). Breakdown at off-peak prices: output 58%, cache-miss input 37%, cache-hit input 5%. The maximum output for one contract is 19,436 tokens. P50 26.1 seconds, P95 55.1 seconds.

**The Pro multiplier for A1 is 3.9, not 3.6.** Cache-miss input is 37% of the A1 cost (only 11% for B0), and Pro's input price is 4.4 times Flash's. With the A1-F token counts at Pro prices, the cost is 3.9 times. This assumes that Pro uses the same number of tokens as Flash (1.06 times on B0).

**Estimate (off-peak prices; multiply by 2 for peak hours):** test-40 contracts are 14% longer than dev on average (13,848 against 12,095 characters, see [Notes](notes.md) Section 2), so the test-40 estimates are multiplied by 1.15. The A2 verification and retries are not measured yet. The estimate uses A1 × 1.5 and will be updated after the stage 4 measurement.

| Experiment | Estimate | Basis |
|---|---|---|
| Development: one B0-F run on dev | About $0.32 | Measured |
| Development: one B0-P run on dev (reference result) | About $1.15 | Measured |
| Development: one A1-F run on dev | About $0.59 | Measured ($1.18 in peak hours) |
| B0-F (test-40) | About $0.25 | $0.0052 × 40 × 1.15 |
| B0-P (test-40) | About $0.9 | B0-F × 3.6 |
| A1-F (test-40) | About $0.45 | $0.0097 × 40 × 1.15 |
| A1-P (test-40) | About $1.7 | A1-F × 3.9 |
| A2-F (test-40, with verification and retries) | About $0.7 | A1-F × 1.5 |
| A2-P (test-40) | About $2.6 | A2-F × 3.9 |
| A2-F (full test, 123 contracts) | About $2.1 | A2-F (test-40) × 123 / 40 |
| **Total for formal experiments** | **About $9** | Prepare $18 for a 2× margin. The estimate on 2026-10-21 was $32, which assumed about 4,000 output tokens per A1 item; the measured value is about 550 tokens |

Ways to save money:
1. Use Flash by default during development. Pro can run reference results on dev (changed on 2026-10-21, see roadmap Section 0, item 4).
2. Run the formal experiments on day 11, one run per configuration.
3. The full contract does not change across the 17 items, so it goes at the start of the prompt as a shared prefix. DeepSeek caches this prefix automatically. Use `usage.cache_read_input_tokens` (DeepSeek's `prompt_cache_hit_tokens`) to confirm that the cache works.
4. Run the formal experiments in off-peak hours when possible. The peak price is 2 times.
5. If the budget is tight, shrink test-40 to test-20. The user decides this.

### 9.3 API Usage Notes

- Both models have thinking mode on by default. In thinking mode, `temperature` has no effect.
- `reasoning_effort` accepts `low`, `high`, and `max`. DeepSeek treats `medium` and `xhigh` as `high`. The default is `high`. **The config file sets `effort: high` explicitly**, and `manifest.json` records it.
- DeepSeek has no structured output that enforces a schema. B0 and L3 use JSON mode (`response_format={"type": "json_object"}`), which makes sure the output is valid JSON. The schema is in the prompt, and Pydantic validates the output. When validation fails, the outcome is `abstained`, reason `parse_error`.
- Each call checks `finish_reason`:
  - `content_filter`: treated as a refusal. The outcome is `abstained`, reason `refusal`.
  - `length`: redo 1 time with 2 times the `max_tokens`. If the second call is also truncated, the outcome is `abstained`, reason `max_tokens`. B0 and the Agent use the same function, `call_with_length_retry()`.
  - `insufficient_system_resource`, `aborted`: the server aborted the request. The code raises `ModelInterrupted`, and the review becomes `failed`.
- Tool calls (built in stage 3 and measured on dev, Technical decisions Section 1.2 and Section 3.6):
  - In thinking mode, `tool_choice` cannot be `required` or a named tool (it returns 400), so the code does not send `tool_choice` (default `auto`). When the model replies with text only, the Agent adds a reminder.
  - Strict mode is Beta, so it is not on, and `base_url` does not change. Pydantic validates the tool arguments.
  - With tools, the `reasoning_content` of each earlier turn must be sent back, or the API returns 400. The Agent uses `message.model_dump(exclude_none=True)` to add the reply back to the conversation, which keeps `reasoning_content` and `tool_calls`. `test_llm.py` has a test case that locks this in.

---

## 10. Technology Stack and Directory Layout

### 10.1 Technology Stack

| Layer | Choice |
|---|---|
| Language | Python 3.13 (decision on 2026-10-21, see Technical decisions F1) |
| LLM | `openai` Python SDK (`openai>=3.36,<4`), calling the DeepSeek OpenAI-compatible API (`base_url="https://api.deepseek.com"`); the key is stored in `DEEPSEEK_API_KEY` in `.env` |
| API | FastAPI |
| Database | SQLite (standard library `sqlite3`) |
| PDF parsing | PyMuPDF (only for demo contracts) |
| Search | `rank-bm25` |
| Data validation | Pydantic v2 |
| Config files | YAML (PyYAML `safe_load`) |
| Tests | pytest |
| Charts | matplotlib (risk-coverage curve, cost-quality chart) |
| Environment | `uv`; `.env` stores the API key and is not committed to Git |

### 10.2 Directory Layout

```
ProvenClause/
├── checklists/
│   └── nda_17.yaml               # N2
├── configs/                      # experiment configs: b0-f.yaml, a2-p.yaml, etc.
├── data/                         # not committed to Git; README has the download steps
│   ├── contract-nli/             # official data package
│   └── demo/                     # demo contract PDFs
├── src/provenclause/
│   ├── models.py                 # shared data structures
│   ├── llm.py                    # the only place that calls models: DeepSeek, cost, FakeLLM
│   ├── baseline.py               # B0
│   ├── review.py                 # review_contract(): single entry point for B0, A1, A2
│   ├── api.py                    # M1
│   ├── db.py                     # M2: create tables, read and write
│   ├── fsm.py                    # M3
│   ├── agent.py                  # M4: loop, budget, concurrency
│   ├── tools.py                  # M5
│   ├── recovery.py               # M6
│   ├── parse.py                  # N1
│   ├── checklist.py              # N2
│   ├── ledger.py                 # N3
│   ├── verify.py                 # N4: L1, L2, L3, three outcomes
│   ├── report.py                 # N5
│   └── eval/                     # M7: run evaluation, metrics, hard cases, curves, error injection, regression gate
├── tests/
├── runs/                         # results and logs of each evaluation (not committed)
├── docs/
└── README.md
```

Each module is a single file. Split a file only when it grows past about 400 lines.

---

## 11. 14-Day Plan

| Day | Date | Tasks | Deliverables of the day |
|---|---|---|---|
| 1 | 10/21 | Create the repository; download the official ContractNLI data package; write data loading code; compute data statistics; read 5 NDAs and the 17 checklist questions by hand; sample test-40; **check the baseline numbers in the original paper** | Data statistics; test-40 list; notes on the 17 questions |
| 2 | 10/22 | Metrics module (accuracy, per-label F1, evidence precision and recall); B0 baseline (review the full contract in one call); run it with Flash on dev; **measure the cost** | First results table |
| 3 | 10/23 | Database (6 tables) + state machine + checklist YAML + finding ledger | Unit tests pass |
| 4 | 10/24 | Agent loop + 5 tools + budget + audit log; 1 checklist item runs end to end | 1 item gives a finding |
| 5 | 10/25 | Concurrent orchestration of 17 items + crash recovery; run A1-F on dev | A1-F results |
| 6 | 10/26 | **Buffer day 1**; error analysis on dev; fix the hard-case categories | Error taxonomy table |
| 7 | 10/27 | Verifier L1 + L2 (exceptions, definitions, cross-references, "NotMentioned" coverage) + unit tests | Verifier tests pass |
| 8 | 10/28 | Verifier L3 + retries + three outcomes; run A2-F on dev. **Core system complete** | A2-F results; catch rate, false-alarm rate |
| 9 | 10/29 | API (3 endpoints); demo PDF parsing; one end-to-end run on a demo contract; crash recovery test (kill the process midway) | API works; recovery test passes |
| 10 | 10/30 | Error injection (4 types, about 40 cases); regression set + regression gate | Error injection results table |
| 11 | 10/31 | **Formal experiments**: 6 configurations on test-40; A2-F on the full test set; optional C5 | Raw results |
| 12 | 11/01 | Analysis: main table, cost-quality chart, risk-coverage curve, hard-case table, error taxonomy | All charts and tables |
| 13 | 11/02 | README: architecture diagram, evidence examples, results tables, charts, error analysis, related work | Complete README |
| 14 | 11/03 | Push to GitHub; **buffer day 2** | Public repository |

**If the schedule slips, cut items in this order:**
1. Optional configuration C5.
2. The extra A2-F experiment on the full test set.
3. Demo PDF parsing (the API supports only contracts from the dataset).
4. Shrink error injection to 2 types (E2 and E4).
5. Shrink the formal experiments to test-20.
6. **Do not cut:** the finding ledger, the three-layer verifier, the 6 core configurations, the risk-coverage curve, and the hard-case analysis. They are the core of the project.

---

## 12. Risks and Mitigations

| Risk | Mitigation |
|---|---|
| A large model already has high accuracy on its own, so there is little room to improve | Report the "Contradiction" F1, verified precision, and hard cases, not only overall accuracy. The core question is "can the cheap model catch up", and this question does not depend on how high the overall score is |
| L3 and the Agent use the same model and make the same errors | L3 cannot see the Agent's reasoning; L2 adds context; C5 tests the effect of a switch to the expensive model |
| L3 stops too many findings, so coverage is too low | Report the false-alarm rate and the risk-coverage curve; tune prompts on dev only |
| The ground truth itself has errors | List it as a separate category in the error taxonomy, and explain it in the README |
| Pro cost goes over budget | Measure on day 2; use Flash by default during development, and run Pro only for reference results on dev; run each formal experiment once, in off-peak hours when possible; shrink to test-20 if necessary |
| The sentence split of demo PDFs does not match the dataset | Demos only show features and are not part of the evaluation |
| Not enough time | Cut features in the order from Section 11 |

---

## 15. Future Extensions

| Extension | Why not now | When to do it |
|---|---|---|
| Task queue and workers (bootcamp 03) | There is only one user | Multiple users, high concurrency |
| Real-time progress push (bootcamp 07) | There is no UI | When a web UI is built |
| Docker, monitoring, rate limiting (bootcamp 09) | No production deployment | Deployment for real users |
| One sub-Agent per checklist item | asyncio concurrency is enough for now | Checklist items need different tools or models |
| CUAD commercial contracts (510 contracts, 41 clause types) | Do solid work on one dataset first | To show that the method transfers to long contracts |
| Hong Kong bilingual (Chinese-English) leases | Needs the author's own labels | To localize for Hong Kong |
| Open-source models (Qwen / DeepSeek) for L3 | Get one model family working first | To show that local deployment is possible |
| Contract edit suggestions | Involves legal advice | When legal professionals take part |
| Web UI | Low return on effort | When real users try it |
| Users upload their own checklist (Playbook) | The user's checklist items have no ground truth, so the system cannot measure accuracy; `guidance` goes into the prompt as it is, so checks for prompt injection, file size, and question count must come first. The core code already reads only the 5 fields of `ChecklistItem`, and the `reviews` table has a `checklist_id` column, so later work needs only an upload entry point, storage, and checks (added on 2026-10-25) | After the method is validated on the 17 checklist items with ground truth; the report must say "the results of this checklist are not evaluated" |

---

## 16. Code Source Notes

- The bootcamp materials and the Salus internship code can both have copyright or confidentiality limits. **Do not copy them directly.**
- Rewrite short versions that follow the same ideas. An Agent writes the code. The core loop is about 300–500 lines. The author must explain what it does and why it has this design (see Section 1, scope principle 2).
- The README states the data source: ContractNLI (Koreeda & Manning, 2021), CC BY 4.0.
