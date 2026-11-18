# ProvenClause Stage 2 build spec: storage foundation

Written on 2026-10-23. Readers: the build agent. The user does not review the code line by line, but must be able to understand each module.

---

## 0. Read before you start

### 0.1 Read these files first

1. [CLAUDE.md](../CLAUDE.md): project rules.
2. [Technical decisions](decisions.md) Section 2.3 (global conventions) and Section 3.5 (Stage 2 technology choices). **Do not break the global conventions.** This document gives the build details of Section 3.5. If the two do not agree, Section 3.5 is correct, and you tell the user.
3. [Roadmap](roadmap.md) Section 2 (interfaces) and "Stage 2" in Section 3.
4. N1, N3, M2, M3, and M6 in the [Detailed design](design.md).
5. [notes.md](notes.md) Section 3: the real forms of sections, definitions, and cross-references in 5 dev NDAs. Find the test cases for the regular expressions here and in dev.
6. Existing code: all files under `src/provenclause/` and `tests/`. Do not change the behavior of the Stage 0 and Stage 1 code.

### 0.2 Rules

1. **Stage 2 does not call the model and costs no money.** Do not run `eval run` or `pytest -m live`.
2. Do not change the decisions in Section 2.3 and Section 3.5. If you think a decision does not work, stop, tell the user why, and let the user decide.
3. Where this document says "default approach", you can follow it directly. For a design question that this document does not cover, ask the user first.
4. **Do not use test and test-40.** Look only at train and dev for the regular expressions. Calculate statistics only on dev.
5. Do not add new runtime dependencies. Stage 2 uses only the standard library, Pydantic, and the existing dependencies.
6. Do not push to GitHub, do not create tags, and do not delete files. Ask the user before you commit.
7. Before you print contract text to the console, call `sys.stdout.reconfigure(encoding="utf-8")` (see CLAUDE.md).

### 0.3 Stage 2 goal

All data structures that the Stage 3 agent needs are in place:

1. Contracts, sentences, reviews, checklist items, findings, and events can be stored in SQLite and read back.
2. An invalid state transition is blocked. A valid transition writes one event.
3. An invalid finding is blocked at record time. When the same record is written 2 times, the table has only 1 row.
4. Each sentence is tagged with its section, the terms it defines, and the sections it cites.

Stage 2 does not change B0. `review_contract()` does not write to the database. The database joins the review flow only in Stage 3.

---

## 1. Files to create and change

| File | Content | Create / change |
|---|---|---|
| `src/provenclause/models.py` | `Span.defines`; state types `ReviewStatus`, `ItemStatus` | Change |
| `src/provenclause/parse.py` | N1 rule part: `annotate_contract()` and helper functions; `load_contractnli()` calls it by default | Change |
| `src/provenclause/db.py` | M2: connection, table creation, read and write functions | Create |
| `src/provenclause/fsm.py` | M3: two transition tables, `transition_review()`, `transition_item()` | Create |
| `src/provenclause/ledger.py` | N3: `operation_id()`, `validate_finding()`, `record_finding()` | Create |
| `src/provenclause/eval/stats.py` | Add `--n1`: print the N1 statistics | Change |
| `tests/test_parse_rules.py` | N1 test cases | Create |
| `tests/test_db.py`, `tests/test_fsm.py`, `tests/test_ledger.py` | M2, M3, N3 test cases | Create |

Import order (no circular imports): `models` ← `parse` ← `db` ← `fsm`, `ledger`. `fsm` and `ledger` do not import each other.

---

## 2. `models.py`

Do not change the existing fields. Only add:

```python
ReviewStatus = Literal["created", "parsing", "reviewing", "completed", "failed"]
ItemStatus = Literal["pending", "drafting", "verifying", "verified", "violated", "abstained"]

class Span(BaseModel):
    ...                                # existing fields do not change
    defines: list[str] = []            # Stage 2: the terms that this sentence defines
```

- Put the state types in `models.py`, because both `db.py` and `fsm.py` use them.
- Keep `is_definition`, with the rule `is_definition == bool(defines)`.

---

## 3. N1 rule part (`parse.py`)

### 3.1 Interface

```python
def annotate_contract(contract: Contract) -> Contract
    # Returns a new Contract with section, is_definition, defines, refs filled for each span. Does not change the input object
def detect_sections(spans: list[Span]) -> list[str | None]
def find_definitions(text: str) -> list[str]          # which terms one sentence defines
def find_refs(text: str) -> list[str]                 # section references in one sentence, normalized IDs, before filtering
def section_spans(contract: Contract, key: str) -> list[Span]
def definition_spans(contract: Contract, term: str) -> list[Span]
def load_contractnli(split, data_dir=CONTRACTNLI_DIR, *, annotate: bool = True) -> list[Contract]
```

- All of these are pure functions. Compile each regular expression once at module level with `re.compile`.
- **Do not change** `format_contract()`. It outputs only `[S{id}] {text}`, so the B0 `prompt_hash` stays `50fb0745f4aa`. Add a test that checks this.
- `section_spans()` and `definition_spans()` are the base functions of the Stage 3 `get_section` and `lookup_definition` tools. Write and test them in Stage 2.

### 3.2 Section detection: `detect_sections()`

**What `Span.section` records:** the deepest "digits and dots" number, for example `"2"`, `"2.06"`, `"1.1"`. `ARTICLE 1` and `Section 5` are recorded as `"1"` and `"5"`. Bracket numbers `(a)`, `(i)`, `a)` are not recorded. The sentences after a heading keep this number until the next accepted heading. Sentences before the first heading are `None`.

**Step 1: find candidate headings.** The start of the sentence (after leading whitespace is removed) matches one of the forms below:

| Form | Example (dev) | Number |
|---|---|---|
| `N.` followed by whitespace or the end, where `N` has 1–2 digits | Contract 3 S15 `1. Definition and interpretation` | `"1"` |
| `N.M` or `N.MM`, optionally followed by `.`, then whitespace or the end | Contract 7 S22 `1.1 'Purpose' shall mean`; Contract 10 S12 `1.01 Definitions` | `"1.1"`, `"1.01"` |
| `ARTICLE` / `Article` / `SECTION` / `Section` + a number or Roman numeral, followed by `.`, `:`, `-`, `–`, or whitespace plus a capital letter, or the end | Contract 10 S11 `ARTICLE 1 - DEFINITIONS`; Contract 398 S22 `ARTICLE II` | `"1"`, `"II"` |
| `A.`–`Z.` followed by whitespace | Contract 9 S11 `A. DEFINITIONS` | `"A"` |

- An `ARTICLE` number in Roman numerals keeps the Roman numeral itself (`"II"`). Do not convert it to Arabic digits. References also use the Roman numeral.
- **Not a heading:** `Section 4(b) of this Agreement and ...` (Contract 605 S44). A `(` comes right after the number, so it does not match row 3 of the table.
- The maximum number is 60. A larger number is not a section number.

**Step 2: filter by order (default approach).** Check the "top-level numbers" one by one (the `N` of `N.` and `ARTICLE N`, and the `N` of `N.M`):

1. The first top-level number must be ≤ 2 (some contracts start from page 2, for example Contract 456 S0 `(page 2.)`).
2. Each later top-level number must equal the previous one, equal the previous one plus 1, or equal 1 (renumbering; for example, Contract 9 starts again from `1.` under `B.`).
3. The `N` of `N.M` must equal the current top-level number, or equal the current top-level number plus 1 (in that case, also move the top-level number forward to `N`).
4. A candidate that does not match is not a heading. The sentence keeps the previous number.

**Step 3: letter headings.** When the contract has accepted number headings, `A.` and `B.` are not sections (`A.`–`C.` in Contract 10 are the preamble). When there are no number headings, filter letter headings in the order `A`, `B`, `C`, with the same rules as Step 2. Roman numerals `I.` and `II.` are not a separate heading form: the `I.` in Contract 64 S110 is the `I` in the letter sequence `A.`–`I.`.

**Known inaccuracy (write it in a test comment; do not fix it):** under `A.` in Contract 9 there are list items `1.`–`4.`, and under `B.` the numbering starts again from `1.`. Both groups are recorded as `"1"`–`"4"`, so `section_spans(c, "1")` returns two places.

You can adjust these rules on dev and train. For each rule you change, state in the delivery report what you changed, why, and how much the statistics changed.

### 3.3 Definition detection: `find_definitions()`

**Quotes:** match straight quotes `"`, curly quotes U+201C and U+201D, single quotes `'`, and `‘’` (Contract 64 has both types of double quotes, and Contract 7 uses single quotes). To prevent the apostrophe in `Party's` from counting as a quote: the opening quote must come after the start of the sentence, whitespace, or `(`; the term starts with a capital letter or a digit; and no letter can come right after the closing quote. The term length is 1–60 characters, with at most 8 words.

**Forms (all from notes Section 3.2 and dev):**

| Form | Example |
|---|---|
| `"X"` + `means` / `shall mean` / `shall have the meaning` / `refers to` / `shall refer to` / `includes` / `shall include` | `"Confidential Information" shall mean ...` |
| `"X"` + `does not include` / `shall not include` (negative definition, notes Section 3.2) | Contract 411 S18 `The term "Confidential Information" does not include` |
| A quoted term in brackets, optionally after `the`, `a`, `an`, `each`, `collectively`, `hereinafter`, `hereinafter referred to as`, `individually`, `or`, separated by commas | `(the "Agreement")`, `(collectively, "Representatives")`, `(hereinafter referred to as the "Agreement")` |
| `is / are (hereinafter) (collectively) referred to as (the) "X"` | `is referred to as "Buyer"`, `is hereinafter collectively referred to as the "Information"` |

- One sentence can define more than one term, for example `(each a "Party" and together the "Parties")`. Put all of them in `defines`, in order of appearance, without duplicates.
- Store the term as it is (keep the letter case). Remove leading and trailing whitespace, and replace each run of internal whitespace with 1 space.

**`definition_spans(contract, term)`:** returns the sentences whose `defines` contains this term. The comparison ignores letter case. When nothing matches, return an empty list and do not raise an error (notes Section 3.2: Contract 563 uses "Receiving Party" but defines only "Recipient"). Do not handle singular and plural forms. That is the job of the Stage 3 tool.

### 3.4 Cross-reference detection: `find_refs()`

**Forms:** `Section`, `Sections`, `Paragraph`, `Paragraphs`, `Clause`, `Clauses`, `Article`, `Articles` (case-insensitive), followed by a number:

| Number form | Example (dev) | After normalization |
|---|---|---|
| Digits and dots | `Section 6`, `section 2.06`, `Clause 14.1` | `"6"`, `"2.06"`, `"14.1"` |
| Followed by brackets | `Clause 1.2(a)`, `Section 2(a) hereof` | `"1.2"`, `"2"` |
| Followed right away by a capital letter | `Paragraph 4E`, `Paragraph 6K above` | `"4"`, `"6"` |
| Roman numerals | `Article II` | `"II"` |

- Normalization: remove the bracket part and the capital letter that follows right away. Keep only the "digits and dots" part or the Roman numeral.
- For the plural form (`Sections 3 and 4`), take only the first number. This is a known gap. Write it in a test comment.
- **Exclude references that point outside the contract:** exclude a reference when `of` comes right after the number (after any bracket part is skipped), and `this Agreement`, `this NDA`, or `hereof` does not follow. Examples: `Section 12 of the Securities Exchange Act of 1934` (Contract 411 S15), `Section A of this Attachment A` (Contract 456 S2).
- `find_refs()` returns the result before filtering. `annotate_contract()` writes into `Span.refs` only the numbers for which `section_spans()` finds sentences, without duplicates, in order of appearance. The statistics in Section 6 output the number of dropped references.

### 3.5 Section lookup: `section_spans(contract, key)`

1. Normalize `key` with the same rules as Section 3.4. So `"2(a)"` and `"Section 2(a)"` both become `"2"`.
2. Return the sentences where `section == key` or `section` starts with `key + "."`. So `"2"` returns all sentences of `"2"`, `"2.01"`, and `"2.06"`.
3. If there are none and `key` contains a dot, remove the last part and look up again. So when `"2.9"` does not exist, return the sentences of `"2"`.
4. If there are still none, return an empty list.

### 3.6 `load_contractnli()`

Add the keyword argument `annotate: bool = True`. When it is `True`, call `annotate_contract()` on each contract. Do not change the Stage 0 tests. They must still pass.

---

## 4. M2 database (`db.py`)

### 4.1 Connection

```python
SCHEMA_VERSION = 1

def connect(path: str | Path) -> sqlite3.Connection
@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]
```

- `connect()`:
  1. Open the connection and set `row_factory = sqlite3.Row`.
  2. Run `PRAGMA foreign_keys = ON`. When the path is not `":memory:"`, run `PRAGMA journal_mode = WAL`.
  3. Create the tables (Section 4.2, `CREATE TABLE IF NOT EXISTS`).
  4. When `PRAGMA user_version` is 0, set it to `SCHEMA_VERSION`. When it is not equal to `SCHEMA_VERSION`, raise an error.
- **Transactions (default approach):** use `sqlite3.connect(..., autocommit=True)`, available from Python 3.12. Each single statement commits automatically. When more than one statement must commit together, use `transaction()`: it runs `BEGIN IMMEDIATE`, then `COMMIT` on success, or `ROLLBACK` and raises the error again on failure. First read the Python 3.13 `sqlite3` documentation to confirm how `autocommit` behaves, then write the code.
- **All functions are synchronous** (the exception to Technical decisions F4). Do not add `async`, and do not use threads.
- The first parameter of each function is `conn`. `db.py` does not keep a global connection.

### 4.2 Table structure

6 tables, all with `STRICT`. Times are UTC ISO 8601 text: `datetime.now(UTC).isoformat(timespec="milliseconds")`. Lists and dicts are stored as JSON text (`json.dumps(..., ensure_ascii=False)`).

```sql
CREATE TABLE IF NOT EXISTS contracts (
  contract_id TEXT PRIMARY KEY,
  source      TEXT NOT NULL,
  title       TEXT NOT NULL,
  text        TEXT NOT NULL,
  n_spans     INTEGER NOT NULL,
  created_at  TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS spans (
  contract_id   TEXT NOT NULL REFERENCES contracts(contract_id),
  span_id       INTEGER NOT NULL,
  char_start    INTEGER NOT NULL,      -- Span.start (END is an SQL keyword, so the column names are not start / end)
  char_end      INTEGER NOT NULL,      -- Span.end
  text          TEXT NOT NULL,
  section       TEXT,
  is_definition INTEGER NOT NULL,      -- 0 / 1
  defines       TEXT NOT NULL,         -- JSON list[str]
  refs          TEXT NOT NULL,         -- JSON list[str]
  PRIMARY KEY (contract_id, span_id)
) STRICT;

CREATE TABLE IF NOT EXISTS reviews (
  review_id    TEXT PRIMARY KEY,
  contract_id  TEXT NOT NULL REFERENCES contracts(contract_id),
  checklist_id TEXT NOT NULL,
  model_config TEXT NOT NULL,          -- JSON: ReviewConfig.model_dump()
  status       TEXT NOT NULL,
  created_at   TEXT NOT NULL,
  updated_at   TEXT NOT NULL,
  error        TEXT
) STRICT;

CREATE TABLE IF NOT EXISTS review_items (   -- the 6th table, Technical decisions Section 3.5 gap 1
  review_id  TEXT NOT NULL REFERENCES reviews(review_id),
  item_id    TEXT NOT NULL,
  status     TEXT NOT NULL,
  attempt    INTEGER NOT NULL,         -- starts at 1
  retries    INTEGER NOT NULL,         -- starts at 0; adds 1 only on a retry after verification fails
  updated_at TEXT NOT NULL,
  PRIMARY KEY (review_id, item_id)
) STRICT;

CREATE TABLE IF NOT EXISTS findings (
  finding_id      INTEGER PRIMARY KEY,
  operation_id    TEXT NOT NULL UNIQUE,
  review_id       TEXT NOT NULL REFERENCES reviews(review_id),
  item_id         TEXT NOT NULL,
  attempt         INTEGER NOT NULL,
  label           TEXT NOT NULL,
  span_ids        TEXT NOT NULL,       -- JSON list[int]
  rationale       TEXT NOT NULL,
  reason          TEXT,
  self_confidence INTEGER,
  checks          TEXT NOT NULL,       -- JSON dict[str, str], maps to Finding.checks
  support_score   INTEGER,
  outcome         TEXT,
  created_at      TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS events (
  review_id TEXT NOT NULL REFERENCES reviews(review_id),
  seq       INTEGER NOT NULL,          -- starts at 1 for each review and increases
  ts        TEXT NOT NULL,
  type      TEXT NOT NULL,
  item_id   TEXT,
  payload   TEXT NOT NULL,             -- JSON
  PRIMARY KEY (review_id, seq)
) STRICT;
```

- `UNIQUE` already creates a unique index on `operation_id` (roadmap Stage 2 task 1).
- Differences from Detailed design M2 (Technical decisions Section 3.5 gaps 1 and 7): there is one more table, `review_items`; `spans` has one more column, `defines`, and the start and end columns have new names; `findings` uses one `checks` column in place of `l1`, `l2`, `l3`, and has one more column, `reason`. At delivery, change Detailed design M2 to the actual table structure.

### 4.3 Read and write functions

```python
def save_contract(conn, contract: Contract) -> None
def load_contract(conn, contract_id: str) -> Contract
def create_review(conn, contract_id: str, checklist: Checklist, config: ReviewConfig) -> str
def get_review(conn, review_id: str) -> ReviewRow
def get_items(conn, review_id: str) -> list[ItemRow]
def get_item(conn, review_id: str, item_id: str) -> ItemRow
def list_findings(conn, review_id: str, item_id: str | None = None) -> list[StoredFinding]
def add_event(conn, review_id: str, type: str, payload: dict, item_id: str | None = None) -> int
def list_events(conn, review_id: str) -> list[EventRow]
```

- `ReviewRow`, `ItemRow`, `StoredFinding`, and `EventRow` are small Pydantic models in `db.py`. Their fields match the table columns. In `ReviewRow`, the field that holds the config is `config: ReviewConfig`, not `model_config` (that is a reserved name in Pydantic). `StoredFinding` contains `finding: Finding`, and also `operation_id`, `review_id`, `attempt`, and `created_at`.
- `save_contract()`: writes `contracts` and all `spans` in one transaction. When the contract already exists with the same `text`, it does nothing. When the `text` is different, it raises an error.
- `load_contract()`: the `Contract` that it reads must be exactly equal to the one that was stored (including `section`, `defines`, `refs`). Add a test that checks this.
- `create_review()`: does 3 things in one transaction:
  1. Writes `reviews`, with `review_id = uuid4().hex` and `status = "created"`.
  2. Writes one `review_items` row for each checklist item, in checklist order: `status = "pending"`, `attempt = 1`, `retries = 0`.
  3. Writes one event with `type = "review_created"`.
- `add_event()`: `seq` is the largest existing `seq` of this review plus 1. It returns `seq`. It does not open its own transaction. The caller puts it in a transaction. All calls run in one thread and are synchronous, so two calls never calculate the same `seq` at the same time.
- When a row is not found, raise `KeyError`, with a message that states what was looked up.

### 4.4 Event types

Stage 2 uses only the 5 types below. Stage 3 adds model calls and tool calls.

| `type` | Written by | `payload` |
|---|---|---|
| `review_created` | `create_review()` | `{"contract_id", "checklist_id", "config_name"}` |
| `review_status` | `transition_review()` | `{"from", "to"}`; add `"error"` on a transition to `failed` |
| `item_status` | `transition_item()` | `{"from", "to", "attempt", "retries"}` (the values after the transition) |
| `finding_recorded` | `record_finding()` | `{"operation_id", "label", "span_ids", "duplicate"}` |
| `finding_rejected` | `record_finding()` | `{"attempt", "errors"}` |

---

## 5. M3 state machine (`fsm.py`)

### 5.1 Transition tables

```python
REVIEW_TRANSITIONS: dict[ReviewStatus, frozenset[ReviewStatus]] = {
    "created":   frozenset({"parsing", "failed"}),
    "parsing":   frozenset({"reviewing", "failed"}),
    "reviewing": frozenset({"completed", "failed"}),
    "completed": frozenset(),
    "failed":    frozenset(),
}

ITEM_TRANSITIONS: dict[ItemStatus, frozenset[ItemStatus]] = {
    "pending":   frozenset({"drafting"}),
    "drafting":  frozenset({"verifying", "abstained", "pending"}),
    "verifying": frozenset({"verified", "violated", "abstained", "drafting", "pending"}),
    "verified":  frozenset(),
    "violated":  frozenset(),
    "abstained": frozenset(),
}
```

- Compared with Detailed design M3, there are 3 new edges (Technical decisions Section 3.5 gap 2): `drafting → abstained`, `drafting → pending`, `verifying → pending`. At delivery, update the Detailed design M3 diagram.
- A1 has no verifier, but it also goes through `drafting → verifying → verified`.

### 5.2 Functions

```python
class InvalidTransition(ValueError): ...

def transition_review(conn, review_id: str, from_: ReviewStatus, to: ReviewStatus,
                      *, error: str | None = None) -> None
def transition_item(conn, review_id: str, item_id: str,
                    from_: ItemStatus, to: ItemStatus) -> ItemRow
```

Both functions do these steps in this order:

1. `to` is not in `TRANSITIONS[from_]`: raise `InvalidTransition`, with a message that states `from_` and `to`. The database does not change.
2. In one transaction:
   1. **Compare, then write:** `UPDATE ... SET status = :to, updated_at = ... WHERE ... AND status = :from_`.
   2. 0 rows changed: read the current state and raise `InvalidTransition`, with a message that states the expected `from_` and the actual state. The transaction rolls back, and no event is written.
   3. Write one event (Section 4.4).
3. When `transition_review()` goes to `failed`, the same `UPDATE` writes `error`. Other transitions do not pass `error`. If one does, raise `ValueError`.
4. `transition_item()` changes the counters in the same `UPDATE`:

   | Transition | `attempt` | `retries` | Meaning |
   |---|---|---|---|
   | `verifying → drafting` | +1 | +1 | Verification failed, retry |
   | `drafting → pending`, `verifying → pending` | +1 | No change | Crash recovery (M6) |
   | Other | No change | No change | |

   Return the `ItemRow` after the transition.
5. `fsm.py` does not check "at most 2 retries". That is a job for Stage 4: the caller looks at `retries`.

---

## 6. N3 finding ledger (`ledger.py`)

```python
def operation_id(review_id: str, item_id: str, attempt: int) -> str
    # f"{review_id}:{item_id}:{attempt}"

def validate_finding(finding: Finding, contract: Contract) -> list[str]

class RecordResult(BaseModel):
    ok: bool
    errors: list[str] = []
    operation_id: str | None = None
    duplicate: bool = False
    stored: StoredFinding | None = None

def record_finding(conn, contract: Contract, review_id: str, finding: Finding) -> RecordResult
```

### 6.1 `validate_finding()`

A pure function. It does not read the database. The Stage 4 L1 also calls it. It returns all problems, or an empty list when there are no problems. Write the error messages in English, because Stage 3 sends them back to the model. Each message must state the specific values, so that the model can fix them.

| Rule | Error message (default text) |
|---|---|
| `label` is Entailment or Contradiction, and `span_ids` is empty | `label Entailment requires at least one span_id` |
| `label` is NotMentioned, and `span_ids` is not empty | `label NotMentioned must have no span_ids, got [3, 7]` |
| `span_ids` contains IDs that do not exist | `span_ids [99, 120] do not exist; valid ids are 0-76` |
| `span_ids` contains duplicate IDs | `span_ids contain duplicates: [5]` |
| `self_confidence` is not `None` and is not between 0 and 100 | `self_confidence must be 0-100, got 140` |

The first two rules are record rule 1 of Detailed design N3. The 3rd rule is record rule 2. This document adds the last two as input checks.

### 6.2 `record_finding()`

1. Use `db.get_item()` to read the `attempt` of this checklist item. When the checklist item does not exist, raise `KeyError`.
2. Call `validate_finding()`. When there are errors: write one `finding_rejected` event, and return `ok=False` and the error list. Do not write to the `findings` table.
3. When there are no errors, in one transaction:
   1. `INSERT ... ON CONFLICT(operation_id) DO NOTHING`.
   2. 1 row inserted: `duplicate=False`. 0 rows inserted: read the existing row, `duplicate=True`. **Return the existing row. Do not overwrite it with the content of this call.**
   3. Write one `finding_recorded` event.
4. Return `ok=True`, `operation_id`, `duplicate`, and `stored`.
- `record_finding()` does not change the state of the checklist item. The caller (the Stage 3 agent) changes the state.
- A rejected record does not use up the `operation_id`. So after the agent fixes the finding, it can record again in the same `attempt` and succeed.

---

## 7. N1 statistics (`eval/stats.py --n1`)

```bash
uv run python -m provenclause.eval.stats --n1 --split dev
```

Print the numbers below. These are the Stage 2 "experiment numbers" (Technical decisions Section 3.5 gap 5):

1. The share of sentences with a section number among all sentences; the number of contracts with at least 1 section number.
2. Defined terms per contract: mean and maximum; the number of contracts with 0 definitions.
3. References per contract: the mean number written into `refs`; the number of dropped references (found by `find_refs()`, but not found by `section_spans()`).
4. For each of the 5 contracts in notes Section 3 (456, 411, 3, 563, 64), list: the section numbers, the detected terms, and the sentences with non-empty `refs`.

---

## 8. Tests

Tests that need data use the existing `contractnli_available` fixture. Database tests use a file in `tmp_path`, plus 1 test with `":memory:"`.

| File | Minimum content to test |
|---|---|
| `test_parse_rules.py` | **Sections:** 1 case each for `1.`, `1.1`, `1.01`, `ARTICLE 1 -`, `ARTICLE II`, `Section 5.`, `A.` (when there are no number headings); `Section 4(b) of this Agreement` is not a heading; an out-of-order `7.` is not a heading; `A.` is not a section when there are number headings; renumbering from `1.` is accepted. **Definitions:** at least 1 case for each form in the Section 3.3 table; 1 case each for straight quotes, curly quotes, and single quotes; `Party's` does not count as a quote; 1 sentence defines 2 terms; `definition_spans()` ignores letter case and returns an empty list when nothing matches. **References:** at least 1 case for each form in the Section 3.4 table; `Section 12 of the Securities Exchange Act` and `Section A of this Attachment A` are excluded; `Section 2(a) hereof` is not excluded. **Lookup:** `section_spans("2")` includes `"2.06"`; `"2.9"` falls back to `"2"`. **Data (with fixture):** the dev `prompt_hash` is still `50fb0745f4aa`; `is_definition == bool(defines)` for all dev contracts; `section_spans()` finds every `refs` value |
| `test_db.py` | `user_version` is 1 after table creation; a different version causes an error; WAL is on (file database); `save_contract()` → `load_contract()` gives an exactly equal result; storing the same contract 2 times gives no error and no duplicates; a different `text` causes an error; `create_review()` writes 1 `reviews` row, 17 `review_items` rows, and 1 event; `add_event()` `seq` starts at 1 and increases; foreign key: a review cannot be created for a `contract_id` that does not exist |
| `test_fsm.py` | A review goes through `created → parsing → reviewing → completed` and writes 3 `review_status` events; any non-final state can go to `failed`, and `error` is written; a final state cannot transition again; a transition outside the table raises an error, and the state and the event count do not change; when `from_` differs from the actual state, an error is raised and the event count does not change; `verifying → drafting` adds 1 to both `attempt` and `retries`; `drafting → pending` adds 1 only to `attempt`; `drafting → abstained` is valid; use `parametrize` to test every valid edge in both tables and some invalid edges |
| `test_ledger.py` | 1 case for each rule in Section 6.1, with specific values in the error message; a finding that breaks 2 rules at the same time returns 2 errors; writing the same `operation_id` 2 times gives only 1 row in the table, the 2nd call returns `duplicate=True` and the content of the 1st call; after a rejection, a fixed record in the same `attempt` succeeds; a record after a retry (`verifying → drafting`) gets a new `operation_id`, and the table has 2 rows; rejected and successful records each write 1 event |

---

## 9. Build order and checkpoints

1. The `models.py` changes → `uv run pytest` still passes (the 75 offline tests of Stages 0 and 1).
2. N1: `parse.py` and `test_parse_rules.py`.
3. `stats.py --n1`. Run it on dev, and read the output for the 5 contracts against notes Section 3. Adjust the rules until you can explain each difference.
4. **Checkpoint 1:** report the Section 7 numbers and the spot-check results for the 5 contracts to the user. State which known inaccuracies you did not fix, and why. You do not need to wait for the user's reply. You can continue.
5. `db.py` and `test_db.py`.
6. `fsm.py` and `test_fsm.py`.
7. `ledger.py` and `test_ledger.py`.
8. **Checkpoint 2:** `uv run pytest` passes completely. Update the documents in Section 11.
9. By item 3 of the CLAUDE.md workflow, run `neat-freak` before a push. For Stage 2, the user decides when to commit and push. Only remind the user of this in the report.

---

## 10. Acceptance criteria

From roadmap Section 3 "Stage 2":

- [ ] Test: an invalid transition raises an error; each valid transition writes 1 `events` row.
- [ ] Test: writing the same `operation_id` 2 times gives only 1 row in the table.
- [ ] Test: a finding of Entailment with empty `span_ids` returns an error; a finding of NotMentioned with non-empty `span_ids` returns an error; a `span_id` that does not exist returns an error.
- [ ] Test: at least 1 case for each form of the section, definition, and cross-reference regular expressions.
- [ ] Numbers: the share of spans with a section number on dev; the mean number of definitions detected per contract. Spot-check by hand with the 5 contracts read in Stage 0.

This document adds:

- [ ] The B0 `prompt_hash` is still `50fb0745f4aa`.
- [ ] All Stage 0 and Stage 1 tests still pass.

---

## 11. Documents to update at delivery

1. [Roadmap](roadmap.md) Section 3 "Stage 2": check the acceptance criteria, write a paragraph on the completion status, and attach the Section 7 numbers.
2. [Detailed design](design.md):
   - M2: change it to the actual 6 tables (Section 4.2).
   - M3: add the 3 new edges from Section 5.1 to the checklist-item diagram, and explain when `attempt` and `retries` add 1.
   - N1: state what `section` records, the `defines` field, and the reference exclusion rule.
   - N3: change the table to the actual columns (`checks`, `reason`), and add the 2 input checks from Section 6.1 to the record rules.
3. [Technical decisions](decisions.md):
   - In the Section 3 table, change the status of M2, M3, N3, and the N1 rule part to "implemented".
   - **N3 is a core module.** By item 2 of the CLAUDE.md workflow, add a section "N3 finding ledger" to the local explainer docs in `docs/explainers/`: what it does, why it has this design, which other options were considered, and which follow-up questions are possible. Do not write it into Technical decisions: from 2026-10-24, module explanations stay only on the local machine, and `docs/explainers/` is not committed. If you work in a git worktree, you cannot see this directory (ignored files are not in the worktree). In that case, write this section in the delivery report, and the user puts it into the local file. At minimum, explain: why the rules are pure functions and not Pydantic validators; how `operation_id` makes sure that duplicate writes give only 1 row; why a duplicate returns the old row and does not overwrite it; why a rejected record does not use up the `operation_id`; and why both retry and recovery add 1 to `attempt`.
   - If you changed the rules in Sections 3.2–3.4, write the changes into Section 3.5.
4. [README](../README.md): add the `stats --n1` command; add `db.py`, `fsm.py`, and `ledger.py` to the directory listing.
5. [CLAUDE.md](../CLAUDE.md): update "Current progress".

---

## 12. Delivery report format

1. What was done: the files created and changed.
2. Acceptance criteria: whether each one passes, with evidence (test output, the Section 7 numbers).
3. The hand spot-check results for the 5 contracts: what the rules detected correctly, what they got wrong, and why you did not fix it.
4. Differences from this document, and the reasons.
5. Problems found and items for the user to decide.
6. Whether you committed. Do not commit without the user's agreement.
