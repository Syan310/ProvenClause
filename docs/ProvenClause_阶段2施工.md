# ProvenClause 阶段 2 施工文档：存储底座

写于 2026-10-23。读者：负责施工的 Agent。用户不逐行审代码，但要能看懂每个模块。

---

## 0. 开工前必读

### 0.1 先读这些文件

1. [CLAUDE.md](../CLAUDE.md)：项目规则。
2. [技术决策记录](ProvenClause_技术决策记录.md)的第 2.3 节（全局约定）和第 3.5 节（阶段 2 的选型）。**全局约定不能违反。** 本文是第 3.5 节的施工细节。两者不一致时，以第 3.5 节为准，并告诉用户。
3. [实现路线](ProvenClause_实现路线.md)第 2 节（接口）和第 3 节"阶段 2"。
4. [详细设计](ProvenClause_详细设计.md)的 N1、N3、M2、M3、M6。
5. [notes.md](notes.md)第 3 节：5 份 dev NDA 里章节、定义、交叉引用的真实写法。正则表达式的用例从这里和 dev 里找。
6. 现有代码：`src/provenclause/` 下的全部文件和 `tests/`。阶段 0、1 的代码不改行为。

### 0.2 规则

1. **阶段 2 不调用模型，不花钱。** 不要运行 `eval run` 和 `pytest -m live`。
2. 第 2.3 节和第 3.5 节的决定不能改。如果你认为某个决定行不通，停下来，向用户说明原因，由用户决定。
3. 本文写成"默认做法"的地方，可以直接照做。本文没有覆盖的设计问题，先问用户。
4. **不要用 test 和 test-40。** 正则表达式只看 train 和 dev。统计数字只在 dev 上算。
5. 不要加新的运行依赖。阶段 2 只用标准库、Pydantic 和已有的依赖。
6. 不要推送到 GitHub，不要打 tag，不要删除文件。提交前先问用户。
7. 输出到控制台的合同文本要先调用 `sys.stdout.reconfigure(encoding="utf-8")`（见 CLAUDE.md）。

### 0.3 阶段 2 的目标

阶段 3 的 Agent 需要的数据结构全部就位：

1. 合同、句子、审核、检查项、结论和事件能存进 SQLite，也能读出来。
2. 不合法的状态跳转被拦下；合法的跳转写一条事件。
3. 不合法的结论在登记时被拦下；同一次登记写 2 次，表里只有 1 条。
4. 每个句子标上所属章节、它定义的术语、它引用的章节。

阶段 2 不改 B0。`review_contract()` 不写数据库。数据库在阶段 3 才接进审核流程。

---

## 1. 要新建和修改的文件

| 文件 | 内容 | 新建 / 修改 |
|---|---|---|
| `src/provenclause/models.py` | `Span.defines`；状态类型 `ReviewStatus`、`ItemStatus` | 修改 |
| `src/provenclause/parse.py` | N1 规则部分：`annotate_contract()` 和辅助函数；`load_contractnli()` 默认调用它 | 修改 |
| `src/provenclause/db.py` | M2：连接、建表、读写函数 | 新建 |
| `src/provenclause/fsm.py` | M3：两张跳转表、`transition_review()`、`transition_item()` | 新建 |
| `src/provenclause/ledger.py` | N3：`operation_id()`、`validate_finding()`、`record_finding()` | 新建 |
| `src/provenclause/eval/stats.py` | 加 `--n1`：打印 N1 的统计 | 修改 |
| `tests/test_parse_rules.py` | N1 的用例 | 新建 |
| `tests/test_db.py`、`tests/test_fsm.py`、`tests/test_ledger.py` | M2、M3、N3 的用例 | 新建 |

依赖关系（不能有循环导入）：`models` ← `parse` ← `db` ← `fsm`、`ledger`。`fsm` 和 `ledger` 互相不导入。

---

## 2. `models.py`

已有的字段不改，只加：

```python
ReviewStatus = Literal["created", "parsing", "reviewing", "completed", "failed"]
ItemStatus = Literal["pending", "drafting", "verifying", "verified", "violated", "abstained"]

class Span(BaseModel):
    ...                                # 已有字段不变
    defines: list[str] = []            # 阶段 2：这个句子定义的术语
```

- 状态类型放在 `models.py`，因为 `db.py` 和 `fsm.py` 都要用。
- `is_definition` 保留，规定为 `is_definition == bool(defines)`。

---

## 3. N1 规则部分（`parse.py`）

### 3.1 接口

```python
def annotate_contract(contract: Contract) -> Contract
    # 返回新的 Contract，填好每个 span 的 section、is_definition、defines、refs。不改输入对象
def detect_sections(spans: list[Span]) -> list[str | None]
def find_definitions(text: str) -> list[str]          # 一个句子定义了哪些术语
def find_refs(text: str) -> list[str]                 # 一个句子里的章节引用，规范化后的编号，过滤前
def section_spans(contract: Contract, key: str) -> list[Span]
def definition_spans(contract: Contract, term: str) -> list[Span]
def load_contractnli(split, data_dir=CONTRACTNLI_DIR, *, annotate: bool = True) -> list[Contract]
```

- 全部是纯函数。正则表达式在模块级用 `re.compile` 编译一次。
- `format_contract()` **不改**。它只输出 `[S{id}] {text}`，所以 B0 的 `prompt_hash` 仍然是 `50fb0745f4aa`。要加一个测试检查这一点。
- `section_spans()` 和 `definition_spans()` 是阶段 3 `get_section`、`lookup_definition` 工具的底层函数。阶段 2 先写好、测好。

### 3.2 章节识别：`detect_sections()`

**`Span.section` 记什么：** 最深一层的"数字点号"编号，例如 `"2"`、`"2.06"`、`"1.1"`。`ARTICLE 1`、`Section 5` 记为 `"1"`、`"5"`。括号编号 `(a)`、`(i)`、`a)` 不记。标题之后的句子沿用这个编号，直到下一个被接受的标题。第一个标题之前的句子是 `None`。

**第 1 步：找候选标题。** 句子开头（去掉前导空白）匹配下面某一种写法：

| 写法 | 例子（dev） | 编号 |
|---|---|---|
| `N.` 后面跟空白或结尾，`N` 是 1–2 位数字 | 合同 3 S15 `1. Definition and interpretation` | `"1"` |
| `N.M` 或 `N.MM`，后面可以有 `.`，再跟空白或结尾 | 合同 7 S22 `1.1 'Purpose' shall mean`；合同 10 S12 `1.01 Definitions` | `"1.1"`、`"1.01"` |
| `ARTICLE` / `Article` / `SECTION` / `Section` + 数字或罗马数字，后面跟 `.`、`:`、`-`、`–`，或者空白加大写字母，或者结尾 | 合同 10 S11 `ARTICLE 1 - DEFINITIONS`；合同 398 S22 `ARTICLE II` | `"1"`、`"II"` |
| `A.`–`Z.` 后面跟空白 | 合同 9 S11 `A. DEFINITIONS` | `"A"` |

- 罗马数字的 `ARTICLE` 编号保留罗马数字本身（`"II"`），不换成阿拉伯数字。引用里写的也是罗马数字。
- **不是标题：** `Section 4(b) of this Agreement and ...`（合同 605 S44）。数字后面紧跟 `(`，不符合上表第 3 行。
- 编号最大到 60。更大的数字不是章节号。

**第 2 步：按顺序过滤（默认做法）。** 对"顶层编号"（`N.` 和 `ARTICLE N` 的 `N`，`N.M` 的 `N`）依次检查：

1. 第一个顶层编号必须 ≤ 2（有的合同从第 2 页开始，例如合同 456 S0 `(page 2.)`）。
2. 之后的顶层编号必须等于上一个、等于上一个加 1，或者等于 1（重新编号，例如合同 9 在 `B.` 下面从 `1.` 重新开始）。
3. `N.M` 的 `N` 必须等于当前顶层编号，或者等于当前顶层编号加 1（这时也把顶层编号推进到 `N`）。
4. 不符合的候选不算标题，这个句子沿用上一个编号。

**第 3 步：字母标题。** 合同里有被接受的数字标题时，`A.`、`B.` 不作为章节（合同 10 的 `A.`–`C.` 是前言）。没有任何数字标题时，字母标题按 `A`、`B`、`C` 的顺序过滤，规则同第 2 步。罗马数字 `I.`、`II.` 不单独作为标题写法：合同 64 S110 的 `I.` 是 `A.`–`I.` 字母序列里的 `I`。

**已知的不准确（写进测试的注释，不用修）：** 合同 9 的 `A.` 下面有 `1.`–`4.` 的列表项，`B.` 下面又从 `1.` 开始。两组都记成 `"1"`–`"4"`，所以 `section_spans(c, "1")` 会返回两处。

你可以在 dev 和 train 上调整这些规则。每改一条，在交付汇报里写明改了什么、为什么改、统计数字变了多少。

### 3.3 定义识别：`find_definitions()`

**引号：** 直引号 `"`、弯引号 `“”`、单引号 `'`、`‘’` 都要匹配（合同 64 两种双引号都有，合同 7 用单引号）。为了不把 `Party's` 里的撇号当成引号：开引号前面必须是句首、空白或 `(`；术语以大写字母或数字开头；闭引号后面不能是字母。术语长度 1–60 个字符，最多 8 个词。

**写法（全部来自 notes 第 3.2 节和 dev）：**

| 写法 | 例子 |
|---|---|
| `"X"` + `means` / `shall mean` / `shall have the meaning` / `refers to` / `shall refer to` / `includes` / `shall include` | `"Confidential Information" shall mean ...` |
| `"X"` + `does not include` / `shall not include`（反向定义，notes 第 3.2 节） | 合同 411 S18 `The term "Confidential Information" does not include` |
| 括号里的引号术语，前面可以有 `the`、`a`、`an`、`each`、`collectively`、`hereinafter`、`hereinafter referred to as`、`individually`、`or`，用逗号隔开 | `(the "Agreement")`、`(collectively, "Representatives")`、`(hereinafter referred to as the "Agreement")` |
| `is / are (hereinafter) (collectively) referred to as (the) "X"` | `is referred to as "Buyer"`、`is hereinafter collectively referred to as the "Information"` |

- 一个句子可以定义多个术语，例如 `(each a "Party" and together the "Parties")`。都放进 `defines`，按出现顺序，去重。
- 术语原样保存（保留大小写），去掉首尾空白，内部连续空白合成 1 个空格。

**`definition_spans(contract, term)`：** 返回 `defines` 里含有这个术语的句子，比较时不分大小写。查不到返回空列表，不报错（notes 第 3.2 节：合同 563 用了 "Receiving Party"，但只定义了 "Recipient"）。单复数不处理，这是阶段 3 工具的事。

### 3.4 交叉引用识别：`find_refs()`

**写法：** `Section`、`Sections`、`Paragraph`、`Paragraphs`、`Clause`、`Clauses`、`Article`、`Articles`（不分大小写），后面跟编号：

| 编号写法 | 例子（dev） | 规范化后 |
|---|---|---|
| 数字和点号 | `Section 6`、`section 2.06`、`Clause 14.1` | `"6"`、`"2.06"`、`"14.1"` |
| 后面有括号 | `Clause 1.2(a)`、`Section 2(a) hereof` | `"1.2"`、`"2"` |
| 后面紧跟大写字母 | `Paragraph 4E`、`Paragraph 6K above` | `"4"`、`"6"` |
| 罗马数字 | `Article II` | `"II"` |

- 规范化：去掉括号部分和紧跟的大写字母，只留"数字点号"部分或罗马数字。
- 复数写法（`Sections 3 and 4`）只取第一个编号。这是已知的不完整，写进测试的注释。
- **排除指向合同外面的引用：** 编号后面（跳过括号部分）紧跟 `of`，并且后面不是 `this Agreement`、`this NDA`、`hereof` 时，排除。例如 `Section 12 of the Securities Exchange Act of 1934`（合同 411 S15）、`Section A of this Attachment A`（合同 456 S2）。
- `find_refs()` 返回过滤前的结果。`annotate_contract()` 只把 `section_spans()` 能找到句子的编号写进 `Span.refs`，去重，按出现顺序。丢掉的条数在第 6 节的统计里输出。

### 3.5 章节查找：`section_spans(contract, key)`

1. 规范化 `key`，规则同第 3.4 节。所以 `"2(a)"` 和 `"Section 2(a)"` 都变成 `"2"`。
2. 返回 `section == key` 或者 `section` 以 `key + "."` 开头的句子。所以 `"2"` 返回 `"2"`、`"2.01"`、`"2.06"` 的全部句子。
3. 一个都没有，并且 `key` 有点号时，去掉最后一段再查一次。所以 `"2.9"` 不存在时返回 `"2"` 的句子。
4. 还是没有，返回空列表。

### 3.6 `load_contractnli()`

加关键字参数 `annotate: bool = True`。为 `True` 时，对每份合同调用 `annotate_contract()`。阶段 0 的测试不改，仍然要通过。

---

## 4. M2 数据库（`db.py`）

### 4.1 连接

```python
SCHEMA_VERSION = 1

def connect(path: str | Path) -> sqlite3.Connection
@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]
```

- `connect()`：
  1. 打开连接，设置 `row_factory = sqlite3.Row`。
  2. 执行 `PRAGMA foreign_keys = ON`。不是 `":memory:"` 时执行 `PRAGMA journal_mode = WAL`。
  3. 建表（第 4.2 节，`CREATE TABLE IF NOT EXISTS`）。
  4. `PRAGMA user_version` 为 0 时设为 `SCHEMA_VERSION`；不等于 `SCHEMA_VERSION` 时抛出错误。
- **事务（默认做法）：** 用 Python 3.12 起的 `sqlite3.connect(..., autocommit=True)`，单条语句自动提交。多条语句要一起提交时，用 `transaction()`：执行 `BEGIN IMMEDIATE`，成功时 `COMMIT`，出错时 `ROLLBACK` 并重新抛出。先读 Python 3.13 的 `sqlite3` 文档确认 `autocommit` 的行为，再写代码。
- **全部函数是同步的**（技术决策记录 F4 的例外）。不要加 `async`，不要用线程。
- 每个函数的第一个参数是 `conn`。`db.py` 不保存全局连接。

### 4.2 表结构

6 张表，全部用 `STRICT`。时间是 UTC 的 ISO 8601 文本，`datetime.now(UTC).isoformat(timespec="milliseconds")`。列表和字典存成 JSON 文本（`json.dumps(..., ensure_ascii=False)`）。

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
  char_start    INTEGER NOT NULL,      -- Span.start（END 是 SQL 关键字，所以列名不用 start / end）
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
  model_config TEXT NOT NULL,          -- JSON：ReviewConfig.model_dump()
  status       TEXT NOT NULL,
  created_at   TEXT NOT NULL,
  updated_at   TEXT NOT NULL,
  error        TEXT
) STRICT;

CREATE TABLE IF NOT EXISTS review_items (   -- 第 6 张表，技术决策记录第 3.5 节缺口 1
  review_id  TEXT NOT NULL REFERENCES reviews(review_id),
  item_id    TEXT NOT NULL,
  status     TEXT NOT NULL,
  attempt    INTEGER NOT NULL,         -- 从 1 开始
  retries    INTEGER NOT NULL,         -- 从 0 开始，只在核对不通过后重试时加 1
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
  checks          TEXT NOT NULL,       -- JSON dict[str, str]，对应 Finding.checks
  support_score   INTEGER,
  outcome         TEXT,
  created_at      TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS events (
  review_id TEXT NOT NULL REFERENCES reviews(review_id),
  seq       INTEGER NOT NULL,          -- 每个审核从 1 开始递增
  ts        TEXT NOT NULL,
  type      TEXT NOT NULL,
  item_id   TEXT,
  payload   TEXT NOT NULL,             -- JSON
  PRIMARY KEY (review_id, seq)
) STRICT;
```

- `UNIQUE` 已经给 `operation_id` 建了唯一索引（实现路线阶段 2 任务 1）。
- 和详细设计 M2 的不同（技术决策记录第 3.5 节缺口 1、7）：多了 `review_items`；`spans` 多了 `defines`，起止列改名；`findings` 用 `checks` 一列代替 `l1`、`l2`、`l3`，并多了 `reason`。交付时把详细设计 M2 改成实际的表结构。

### 4.3 读写函数

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

- `ReviewRow`、`ItemRow`、`StoredFinding`、`EventRow` 是 `db.py` 里的小 Pydantic 模型，字段和表的列对应。`ReviewRow` 里放配置的字段叫 `config: ReviewConfig`，不叫 `model_config`（这是 Pydantic 的保留名）。`StoredFinding` 包含 `finding: Finding`，另有 `operation_id`、`review_id`、`attempt`、`created_at`。
- `save_contract()`：在一个事务里写 `contracts` 和全部 `spans`。合同已经存在并且 `text` 相同时，什么都不做；`text` 不同时抛出错误。
- `load_contract()`：读出的 `Contract` 要和存进去的完全相等（包括 `section`、`defines`、`refs`）。要加一个测试检查这一点。
- `create_review()`：在一个事务里做 3 件事：
  1. 写 `reviews`，`review_id = uuid4().hex`，`status = "created"`。
  2. 按检查清单的顺序，每个检查项写一行 `review_items`：`status = "pending"`、`attempt = 1`、`retries = 0`。
  3. 写一条事件 `type = "review_created"`。
- `add_event()`：`seq` 取这个审核已有的最大 `seq` 加 1。返回 `seq`。它不自己开事务，由调用方放进事务里。因为所有调用都在一个线程里，并且是同步的，两个调用不会同时算出同一个 `seq`。
- 找不到行时抛出 `KeyError`，信息里写明找的是什么。

### 4.4 事件类型

阶段 2 只用下面 5 种。阶段 3 再加模型调用和工具调用。

| `type` | 谁写 | `payload` |
|---|---|---|
| `review_created` | `create_review()` | `{"contract_id", "checklist_id", "config_name"}` |
| `review_status` | `transition_review()` | `{"from", "to"}`，到 `failed` 时加 `"error"` |
| `item_status` | `transition_item()` | `{"from", "to", "attempt", "retries"}`（跳转后的值） |
| `finding_recorded` | `record_finding()` | `{"operation_id", "label", "span_ids", "duplicate"}` |
| `finding_rejected` | `record_finding()` | `{"attempt", "errors"}` |

---

## 5. M3 状态机（`fsm.py`）

### 5.1 跳转表

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

- 和详细设计 M3 相比，加了 3 条边（技术决策记录第 3.5 节缺口 2）：`drafting → abstained`、`drafting → pending`、`verifying → pending`。交付时更新详细设计 M3 的图。
- A1 没有核对器，也走 `drafting → verifying → verified`。

### 5.2 函数

```python
class InvalidTransition(ValueError): ...

def transition_review(conn, review_id: str, from_: ReviewStatus, to: ReviewStatus,
                      *, error: str | None = None) -> None
def transition_item(conn, review_id: str, item_id: str,
                    from_: ItemStatus, to: ItemStatus) -> ItemRow
```

两个函数都按下面的顺序做：

1. `to` 不在 `TRANSITIONS[from_]` 里：抛出 `InvalidTransition`，信息写明 `from_` 和 `to`。数据库不变。
2. 在一个事务里：
   1. **比较后再写：** `UPDATE ... SET status = :to, updated_at = ... WHERE ... AND status = :from_`。
   2. 改了 0 行：查出当前状态，抛出 `InvalidTransition`，信息写明期望的 `from_` 和实际的状态。事务回滚，事件不写。
   3. 写一条事件（第 4.4 节）。
3. `transition_review()` 到 `failed` 时，同一条 `UPDATE` 写入 `error`。其他跳转不传 `error`；传了就抛出 `ValueError`。
4. `transition_item()` 的计数，在同一条 `UPDATE` 里改：

   | 跳转 | `attempt` | `retries` | 含义 |
   |---|---|---|---|
   | `verifying → drafting` | +1 | +1 | 核对不通过，重试 |
   | `drafting → pending`、`verifying → pending` | +1 | 不变 | 断点恢复（M6） |
   | 其他 | 不变 | 不变 | |

   返回跳转后的 `ItemRow`。
5. `fsm.py` 不检查"最多重试 2 次"。这是阶段 4 的事：调用方看 `retries`。

---

## 6. N3 结论账本（`ledger.py`）

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

纯函数，不读数据库。阶段 4 的 L1 也调用它。返回全部问题，没有问题时返回空列表。错误信息用英文写，因为阶段 3 会把它交回模型。每条信息要写明具体的值，模型才能改。

| 规则 | 错误信息（默认写法） |
|---|---|
| `label` 是 Entailment 或 Contradiction，`span_ids` 为空 | `label Entailment requires at least one span_id` |
| `label` 是 NotMentioned，`span_ids` 不为空 | `label NotMentioned must have no span_ids, got [3, 7]` |
| `span_ids` 里有不存在的编号 | `span_ids [99, 120] do not exist; valid ids are 0-76` |
| `span_ids` 里有重复的编号 | `span_ids contain duplicates: [5]` |
| `self_confidence` 不是 `None`，并且不在 0–100 之间 | `self_confidence must be 0-100, got 140` |

前两条是详细设计 N3 的登记规则 1，第 3 条是规则 2。后两条是本文加的输入检查。

### 6.2 `record_finding()`

1. 用 `db.get_item()` 读出这个检查项的 `attempt`。检查项不存在时抛出 `KeyError`。
2. 调用 `validate_finding()`。有错误时：写一条 `finding_rejected` 事件，返回 `ok=False` 和错误列表。`findings` 表不写。
3. 没有错误时，在一个事务里：
   1. `INSERT ... ON CONFLICT(operation_id) DO NOTHING`。
   2. 插入了 1 行：`duplicate=False`。插入了 0 行：读出已有的那一行，`duplicate=True`。**返回已有的那一条，不用这次的内容覆盖它。**
   3. 写一条 `finding_recorded` 事件。
4. 返回 `ok=True`、`operation_id`、`duplicate` 和 `stored`。
- `record_finding()` 不改检查项的状态。改状态是调用方（阶段 3 的 Agent）的事。
- 被拒绝的登记不占用 `operation_id`。所以 Agent 改正以后，在同一个 `attempt` 里再登记一次，可以成功。

---

## 7. N1 的统计（`eval/stats.py --n1`）

```bash
uv run python -m provenclause.eval.stats --n1 --split dev
```

打印下面的数字。阶段 2 的"实验数字"就是这些（技术决策记录第 3.5 节缺口 5）：

1. 有章节号的句子占全部句子的比例；至少有 1 个章节号的合同数。
2. 每份合同的定义术语数：平均值、最大值；0 个定义的合同数。
3. 每份合同的引用数：写进 `refs` 的平均条数；被丢掉的条数（`find_refs()` 找到，但 `section_spans()` 找不到）。
4. notes 第 3 节的 5 份合同（456、411、3、563、64）逐份列出：章节号列表、识别出的术语列表、`refs` 不为空的句子。

---

## 8. 测试

依赖数据的测试用已有的 `contractnli_available` fixture。数据库测试用 `tmp_path` 里的文件，另加 1 个 `":memory:"` 的测试。

| 文件 | 至少要测的内容 |
|---|---|
| `test_parse_rules.py` | **章节：** `1.`、`1.1`、`1.01`、`ARTICLE 1 -`、`ARTICLE II`、`Section 5.`、`A.`（没有数字标题时）各 1 个用例；`Section 4(b) of this Agreement` 不是标题；乱序的 `7.` 不是标题；有数字标题时 `A.` 不是章节；从 `1.` 重新编号被接受。**定义：** 第 3.3 节表里每种写法至少 1 个用例；直引号、弯引号、单引号各 1 个；`Party's` 不被当成引号；1 个句子定义 2 个术语；`definition_spans()` 不分大小写，查不到返回空列表。**引用：** 第 3.4 节表里每种写法至少 1 个；`Section 12 of the Securities Exchange Act` 和 `Section A of this Attachment A` 被排除；`Section 2(a) hereof` 不被排除。**查找：** `section_spans("2")` 包括 `"2.06"`；`"2.9"` 退回 `"2"`。**数据（用 fixture）：** dev 的 `prompt_hash` 仍然是 `50fb0745f4aa`；dev 全部合同 `is_definition == bool(defines)`；每个 `refs` 都能被 `section_spans()` 找到 |
| `test_db.py` | 建表后 `user_version` 是 1；版本不同时报错；WAL 已打开（文件数据库）；`save_contract()` → `load_contract()` 完全相等；同一份合同存 2 次没有报错、没有重复；`text` 不同时报错；`create_review()` 写 1 行 `reviews`、17 行 `review_items`、1 条事件；`add_event()` 的 `seq` 从 1 递增；外键：不存在的 `contract_id` 不能建审核 |
| `test_fsm.py` | 审核走完 `created → parsing → reviewing → completed`，写 3 条 `review_status` 事件；任一非终止状态能到 `failed`，`error` 被写入；终止状态不能再跳转；跳转表外的跳转抛出错误，状态和事件数不变；`from_` 和实际状态不同时抛出错误，事件数不变；`verifying → drafting` 让 `attempt` 和 `retries` 都加 1；`drafting → pending` 只让 `attempt` 加 1；`drafting → abstained` 合法；用 `parametrize` 把两张表里每一条合法的边和若干条非法的边都测一遍 |
| `test_ledger.py` | 第 6.1 节每条规则 1 个用例，错误信息里有具体的值；一个结论同时违反 2 条规则，返回 2 条错误；同一个 `operation_id` 写 2 次，表里只有 1 条，第 2 次 `duplicate=True`，返回的是第 1 次的内容；被拒绝后在同一个 `attempt` 里改正再登记，成功；重试（`verifying → drafting`）后再登记，得到新的 `operation_id`，表里有 2 条；被拒绝和成功的登记各写 1 条事件 |

---

## 9. 施工顺序和检查点

1. `models.py` 的改动 → `uv run pytest` 仍然通过（阶段 0、1 的 75 个离线测试）。
2. N1：`parse.py` 和 `test_parse_rules.py`。
3. `stats.py --n1`。在 dev 上跑，对照 notes 第 3 节读 5 份合同的输出。调整规则，直到你能解释每一处不一致。
4. **检查点 1：** 向用户汇报第 7 节的数字和 5 份合同的抽查结果。说明哪些已知的不准确没有修，以及原因。不需要等用户回复，可以继续。
5. `db.py` 和 `test_db.py`。
6. `fsm.py` 和 `test_fsm.py`。
7. `ledger.py` 和 `test_ledger.py`。
8. **检查点 2：** `uv run pytest` 全部通过。更新第 11 节的文档。
9. 按 CLAUDE.md 工作流程第 3 条，推送前要运行 `neat-freak`。阶段 2 由用户决定何时提交和推送，你只在汇报里提醒这一点。

---

## 10. 验收标准

来自实现路线第 3 节"阶段 2"：

- [ ] 测试：非法跳转抛出错误；每次合法跳转写 1 条 `events`。
- [ ] 测试：同一个 `operation_id` 写 2 次，表里只有 1 条。
- [ ] 测试：结论为"支持"但 `span_ids` 为空，返回错误；结论为"没提到"但 `span_ids` 不为空，返回错误；`span_id` 不存在，返回错误。
- [ ] 测试：章节、定义、交叉引用的正则表达式，每种写法至少 1 个用例。
- [ ] 数字：dev 上有章节号的 span 占多少；每份合同平均识别出多少个定义。用阶段 0 读过的 5 份合同人工抽查。

本文另加：

- [ ] B0 的 `prompt_hash` 仍然是 `50fb0745f4aa`。
- [ ] 阶段 0、1 的全部测试仍然通过。

---

## 11. 交付时要更新的文档

1. [实现路线](ProvenClause_实现路线.md)第 3 节"阶段 2"：勾选验收标准，写一段完成情况，附第 7 节的数字。
2. [详细设计](ProvenClause_详细设计.md)：
   - M2：改成实际的 6 张表（第 4.2 节）。
   - M3：检查项级的图加上第 5.1 节的 3 条新边，并说明 `attempt` 和 `retries` 什么时候加 1。
   - N1：写明 `section` 记什么、`defines` 字段、引用的排除规则。
   - N3：表格改成实际的列（`checks`、`reason`），登记规则加上第 6.1 节的 2 条输入检查。
3. [技术决策记录](ProvenClause_技术决策记录.md)：
   - 第 3 节表格里把 M2、M3、N3、N1 规则部分的状态改成"已实现"。
   - **N3 是核心模块。** 按 CLAUDE.md 工作流程第 2 条，在本地讲解文档 `docs/讲解/` 里加一节"N3 结论账本"：它做什么、为什么这样设计、考虑过哪些其他方案、可能有哪些追问。不要写进技术决策记录：模块讲解从 2026-10-24 起只放在本地，`docs/讲解/` 不提交。如果你在 git worktree 里工作，看不到这个目录（被忽略的文件不在 worktree 里），就把这一节写进交付汇报，由用户放进本地文件。至少讲清楚：为什么规则写成纯函数而不是 Pydantic 校验器；`operation_id` 怎么保证重复写入只有 1 条；为什么重复时返回旧的一条而不是覆盖；被拒绝的登记为什么不占用 `operation_id`；重试和恢复为什么都让 `attempt` 加 1。
   - 如果你改了第 3.2–3.4 节的规则，把改动写进第 3.5 节。
4. [README](../README.md)：加 `stats --n1` 的命令；目录里加 `db.py`、`fsm.py`、`ledger.py`。
5. [CLAUDE.md](../CLAUDE.md)：更新"当前进度"。

---

## 12. 交付汇报的格式

1. 做了什么：新建和修改的文件。
2. 验收标准：每一条是否通过，附证据（测试输出、第 7 节的数字）。
3. 5 份合同的人工抽查结果：识别对了什么、错了什么、为什么没修。
4. 和本文不同的地方，以及原因。
5. 发现的问题和需要用户决定的事。
6. 提交了没有。没有用户同意，不提交。
