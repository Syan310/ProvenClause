# ProvenClause 阶段 3 施工文档：逐项 Agent

写于 2026-10-25。读者：负责施工的 Agent。用户不逐行审代码，但要能看懂每个模块。

---

## 0. 开工前必读

### 0.1 先读这些文件

1. [CLAUDE.md](../CLAUDE.md)：项目规则。
2. [技术决策记录](ProvenClause_技术决策记录.md)的第 1.2 节（DeepSeek 的约束，2026-10-25 新加了 4 行）、第 2.3 节（全局约定）和第 3.6 节（阶段 3 的选型）。**全局约定不能违反。** 本文是第 3.6 节的施工细节。两者不一致时，以第 3.6 节为准，并告诉用户。
3. [实现路线](ProvenClause_实现路线.md)第 2 节（接口）和第 3 节"阶段 3"。
4. [详细设计](ProvenClause_详细设计.md)的 M3、M4、M5、M6、N3 和第 9.3 节。
5. [阶段 2 施工文档](ProvenClause_阶段2施工.md)第 4–6 节：`db.py`、`fsm.py`、`ledger.py` 的接口。
6. 现有代码：`src/provenclause/` 下的全部文件和 `tests/`。
7. DeepSeek 文档：[思考模式](https://api-docs.deepseek.com/guides/thinking_mode)（带工具的多轮对话示例）和 [API 参考](https://api-docs.deepseek.com/api/create-chat-completion)。写 `llm.py` 之前再读一次，确认第 1.2 节的事实没有变。

### 0.2 规则

1. **真实调用要花钱。** 离线测试全部通过以前，不运行 `eval run` 和 `pytest -m live`。要运行时，先告诉用户调用次数和估计成本，等用户同意（见第 11 节检查点 3）。
2. 第 2.3 节和第 3.6 节的决定不能改。如果你认为某个决定行不通，停下来，向用户说明原因，由用户决定。
3. 本文写成"默认做法"的地方，可以直接照做。本文没有覆盖的设计问题，先问用户。
4. **不要用 test 和 test-40。** 调试和调提示词只看 dev。
5. 只加 1 个新的运行依赖：`uv add rank-bm25==0.2.2`（连带 numpy）。不要加其他依赖。
6. **B0 的提示词不改。** `baseline.prompt_hash()` 必须仍然是 `50fb0745f4aa`。B0 只改截断时的处理（第 4 节）。
7. 不要推送到 GitHub，不要打 tag，不要删除文件。提交前先问用户。
8. 输出到控制台的合同文本要先调用 `sys.stdout.reconfigure(encoding="utf-8")`（见 CLAUDE.md）。

### 0.3 阶段 3 的目标

解锁 A1-F，回答"逐项审核带来多少提升"：

1. 17 个检查项分开审。每一项是一个 Agent 循环：模型可以用 4 个只读工具查合同，最后用 `record_finding` 登记结论。
2. 17 项最多 4 项同时进行。第 1 项先单独跑，让合同全文进入缓存。
3. 结论、状态和每一次模型调用、工具调用都写进 SQLite。日志脱敏。
4. 进程中断以后，M6 能把没完成的项找回来重做，17 项完整、没有重复。
5. A1-F 在 dev 上跑一次，和 B0-F 比较。

---

## 1. 要新建和修改的文件

| 文件 | 内容 | 新建 / 修改 |
|---|---|---|
| `src/provenclause/models.py` | `AgentStats`、`Finding.stats`、`ReviewConfig.max_review_output_tokens`；`NO_ANSWER_REASONS` 加 `budget_exceeded` | 修改 |
| `src/provenclause/llm.py` | `LLM.create()`、`DeepSeekLLM.create()`、`check_tool_completion()`、`call_with_length_retry()`；`FakeLLM.create()` 和回复的辅助函数 | 修改 |
| `src/provenclause/baseline.py` | 截断时加倍重做 1 次（C6） | 修改 |
| `src/provenclause/db.py` | `set_finding_outcome()`、`list_reviews()` | 修改 |
| `src/provenclause/tools.py` | M5：BM25 索引、5 个工具、参数模型和 JSON Schema、超时、脱敏 | 新建 |
| `src/provenclause/agent.py` | M4：提示词、单项循环、审核循环、预算、事件 | 新建 |
| `src/provenclause/recovery.py` | M6：`recover()`、`resume_review()` | 新建 |
| `src/provenclause/review.py` | `review_contract()` 加 `conn` 参数，接上 A1 | 修改 |
| `src/provenclause/eval/run.py` | A1 的 `prompt_hash`；每次运行一个 `reviews.db` | 修改 |
| `src/provenclause/eval/metrics.py` | Agent 的统计（步数、工具调用等） | 修改 |
| `configs/a1-f.yaml`、`configs/a1-p.yaml` | A1 的配置 | 新建 |
| `tests/test_tools.py`、`tests/test_agent.py`、`tests/test_recovery.py` | M5、M4、M6 的用例 | 新建 |
| `tests/test_llm.py`、`tests/test_baseline.py`、`tests/test_db.py`、`tests/test_run.py`、`tests/test_metrics.py`、`tests/test_models.py` | 新功能的用例；B0 截断用例改成新规则 | 修改 |
| `tests/test_live.py` | 1 个 A1 的真实调用测试，标记 `live` | 修改 |

依赖关系（不能有循环导入）：`models` ← `llm`、`parse` ← `db` ← `fsm`、`ledger` ← `tools` ← `agent` ← `recovery`、`review`。`tools` 不导入 `agent`。

---

## 2. `models.py`

已有的字段不改，只加：

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
    ...                                 # 已有字段不变
    stats: AgentStats | None = None     # 阶段 3：A1、A2 填，B0 是 None

class ReviewConfig(BaseModel):
    ...                                 # 已有字段不变
    max_review_output_tokens: int = 300_000   # C4：一次审核累计输出 token 的上限
```

- `budget_exceeded` 加进 `NO_ANSWER_REASONS`（第 3.6 节缺口 2）。B0 不会产生这个原因，B0 的分数不变。
- `test_models.py` 加 1 个用例：以前的 `findings.jsonl`（没有 `stats`）仍然能读。

---

## 3. `llm.py`

### 3.1 `create()`

```python
class LLM(Protocol):
    async def parse(...) -> ParsedChatCompletion[T]: ...      # 不变
    async def create(
        self, *, model: str, system: str, messages: list[dict], tools: list[dict],
        max_tokens: int, effort: str,
    ) -> ChatCompletion: ...

def check_tool_completion(completion: ChatCompletion) -> ChatCompletion
```

- `DeepSeekLLM.create()`：`messages=[{"role": "system", "content": system}, *messages]`，传 `tools`、`max_tokens`、`reasoning_effort=effort`。**不传** `tool_choice`（默认 `auto`；思考模式下 `required` 会返回 400）。不传 `response_format`。不打开 strict，`base_url` 不变。
- `check_tool_completion()` 按 `finish_reason` 处理：

  | `finish_reason` | 处理 |
  |---|---|
  | `stop`、`tool_calls` | 返回 completion |
  | `content_filter` | 抛出 `ModelStopped("refusal", completion)` |
  | `length` | 抛出 `ModelStopped("max_tokens", completion)` |
  | `insufficient_system_resource`、`aborted`、其他值 | 抛出 `ModelInterrupted` |

- 不判断"有没有调用工具"。模型只回文字时，由 Agent 处理（第 6.4 节）。

### 3.2 截断后加倍重做：`call_with_length_retry()`

C6 的规则，B0 和 Agent 共用：

```python
LENGTH_RETRY_FACTOR = 2

async def call_with_length_retry(
    call: Callable[[int], Awaitable[R]], max_tokens: int, *,
    on_truncated: Callable[[ChatCompletion], None],
    allow_retry: Callable[[], bool] = lambda: True,
) -> R
```

1. `await call(max_tokens)`。
2. 抛出 `ModelStopped`，并且 `reason == "max_tokens"`：
   1. 调用 `on_truncated(exc.completion)`。调用方用它计费、写事件。
   2. `allow_retry()` 为 `False`（例如 C4 的预算用完了）：重新抛出这个异常。
   3. 否则 `await call(max_tokens * LENGTH_RETRY_FACTOR)`。这一次再抛出什么，都原样抛出。
3. 其他异常原样抛出。

被截断的回复**不进对话记录**：`call` 每次用同一份 `messages`，截断的回复只出现在 `on_truncated` 里。

### 3.3 `FakeLLM`

在现有的 `FakeLLM` 上加 `create()`，和 `parse()` 共用同一个脚本（A3）：

```python
def tool_call_reply(*calls: tuple[str, dict], content: str | None = None, **usage) -> ChatCompletion
    # finish_reason "tool_calls"；每个 (name, arguments) 生成一个 tool_call，id 是 "call_1"、"call_2"……
def text_reply(content: str, **usage) -> ChatCompletion
    # finish_reason "stop"，没有 tool_calls
```

- `make_completion()` 加参数 `tool_calls: list[dict] | None = None`。`reasoning_content` 保持 `"..."`，测试用它检查"思考内容被发回"。
- `FakeLLM.create()`：记录请求（加 `tools` 键），弹出下一步。步骤是 `ChatCompletion` 时，交给 `check_tool_completion()` 再返回；是异常时抛出；是函数时先调用它。
- **类型区分：** `ChatCompletion` 也是 Pydantic 对象。`parse()` 收到 `ChatCompletion` 步骤，`create()` 收到不是 `ChatCompletion` 的 Pydantic 对象，都抛出 `AssertionError`，信息写明脚本放错了。
- 并发测试里，调用顺序不固定。默认做法：脚本里放函数，函数从 `request["messages"][0]["content"]` 读出检查项的 `id`，按检查项返回回复。把这个写成 `tests/` 里的辅助函数。

---

## 4. B0 的截断处理（`baseline.py`）

`review_b0()` 用 `call_with_length_retry()` 包住 `llm.parse()`：

- 第 1 次用 `config.max_tokens`，截断后用 2 倍再调用 1 次。两次的用量都加进 `Usage`，`n_calls` 是 2。
- 第 2 次还截断：17 项都是 `abstained`，原因 `max_tokens`（和以前一样）。
- 拒绝回答、`parse_error`：不重做，和以前一样。
- `SYSTEM`、`USER_TEMPLATE`、`ITEM_TEMPLATE` 不改。`prompt_hash()` 仍然是 `50fb0745f4aa`。
- `test_baseline.py` 第 70 行的 `parametrize` 里，`max_tokens` 的用例要改：脚本给 2 个截断才放弃；另加"第 1 次截断、第 2 次成功"的用例。

---

## 5. M5 工具（`tools.py`）

### 5.1 接口

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

- `ContractTools` 只读合同，不碰数据库。一次审核建 1 个，17 项共用。
- `run_tool()` 是唯一执行工具的地方：解析参数 → 校验 → 执行 → 计时。它**不写事件**，写事件是 Agent 的事（第 6.6 节）。

### 5.2 参数模型和 JSON Schema

每个工具一个 Pydantic 参数模型，`ConfigDict(extra="forbid")`。`TOOL_SCHEMAS` 用 `model_json_schema()` 生成参数部分，所以 Schema 和校验永远一致。工具说明用英文写。

| 工具 | 参数 | 约束 |
|---|---|---|
| `search_spans` | `query: str`、`top_k: int = 8` | `query` 非空；`1 ≤ top_k ≤ 20` |
| `read_spans` | `span_ids: list[int]`、`window: int = 1` | `1 ≤ len(span_ids) ≤ 10`；`0 ≤ window ≤ 3` |
| `lookup_definition` | `term: str` | 非空 |
| `get_section` | `section: str` | 非空 |
| `record_finding` | `label: Label`、`span_ids: list[int]`、`rationale: str`、`self_confidence: int` | `0 ≤ self_confidence ≤ 100`。`span_ids` 不设长度约束（"没提到"时为空） |

- 不打开 strict（A2）。参数不是合法 JSON、缺字段、多字段、超出范围：`ok=False`，`error_kind="arguments"`，`text` 是 `ERROR: invalid arguments for search_spans: ...`，写明 Pydantic 指出的字段和值。
- 工具名不在这 5 个里：`error_kind="unknown_tool"`，`text` 列出可用的工具名。
- `record_finding` 没有 `item_id` 参数。检查项由 Agent 决定，模型不能登记别的项。

### 5.3 4 个只读工具

**结果格式（B3）：** 每句一行，`[S{id}] (§{section}) {text}`。`section` 是 `None` 时省略 `(§...)`。和 system 提示词里的合同全文一样以 `[S{id}]` 开头。

**`search_spans(query, top_k)`：**
- 用 `rank_bm25.BM25Okapi`，默认参数。分词：`re.findall(r"[a-z0-9]+", text.lower())`，去掉约 30 个英文停用词（`the`、`of`、`and`、`to`、`a`、`in`、`or`、`any`、`such`、`by`、`for`、`be`、`shall` 等，写成模块常量）。不做词干还原。
- 按分数从高到低返回前 `top_k` 句，只返回分数大于 0 的句子。每行末尾加分数：`[S12] (§2) ... (score 7.31)`。分数相同时，编号小的在前。
- 一句都没有：`No sentence matches "<query>".`

**`read_spans(span_ids, window)`：**
- 返回每个编号前后各 `window` 句，合并去重，按编号排序。连续的句子之间不加分隔；不连续时加一行 `...`。
- 有不存在的编号：`ok=False`，`error_kind="arguments"`，`text` 写明不存在的编号和合法范围（同 `validate_finding()` 的写法）。

**`lookup_definition(term)`：**
- 先用 `parse.definition_spans(contract, term)`。查不到时按顺序再试：去掉开头的 `the `；单复数（去掉或加上末尾的 `s`）。这是阶段 2 留给阶段 3 的"单复数"。
- 返回定义句。查不到：`No definition of "<term>" found. Terms defined in this contract: "Agreement", "Confidential Information", ...`（按出现顺序去重，最多 40 个）。

**`get_section(section)`：**
- 用 `parse.section_spans(contract, section)`（已经处理 `Section 2(a)`、`2.9` 退回 `2`）。
- 超过 40 句：只返回前 40 句，最后一行写 `... <N> more sentences in this section; use read_spans to read them.`
- 查不到：`Section "<section>" not found. Sections in this contract: 1, 2, 2.1, ...`（按出现顺序去重）。合同没有章节号时，写 `This contract has no numbered sections; use search_spans.`

### 5.4 `record_finding`

1. 用参数建 `Finding(item_id=item_id, label=..., span_ids=..., rationale=..., self_confidence=...)`。
2. 调用 `ledger.record_finding(conn, contract, review_id, finding)`。
3. 成功：`ok=True`，`text` 是 `Recorded.`，`record` 是 `RecordResult`。`duplicate=True` 也算成功。
4. 被拒绝：`ok=False`，`error_kind="rejected"`，`text` 是 `ERROR: the finding was not recorded: <errors joined by "; ">. Fix it and call record_finding again.`
5. `record_finding` 在事件循环的线程里直接调用，不放进线程（B2）。`run_tool()` 需要 `contract`，从 `tools.contract` 读。

### 5.5 超时（B2）

- 4 个只读工具：`await asyncio.wait_for(asyncio.to_thread(fn, **args), TOOL_TIMEOUT_S)`。
- 超时：`ok=False`，`error_kind="timeout"`，`text` 是 `ERROR: search_spans timed out after 10 s.` 线程停不下来，会在后台跑完，结果丢掉。
- 测试用 `monkeypatch` 把 `TOOL_TIMEOUT_S` 改成 0.05 秒，再让一个工具 `time.sleep(0.5)`。

### 5.6 脱敏（B4）

`redact(text)` 用模块级编译好的正则表达式，按下面的顺序替换（身份证号在电话之前，避免被当成电话）：

| 类型 | 替换成 | 必须替换 | 不能替换 |
|---|---|---|---|
| 邮箱 | `[EMAIL]` | `jane.doe@acme-corp.com` | `Section 2.06` |
| 中国身份证号（18 位，最后一位可以是 `X`） | `[ID]` | `11010519491231002X` | `[S123]`、`$1,000,000` |
| 美国 SSN `ddd-dd-dddd` | `[ID]` | `123-45-6789` | 日期 `2026-10-21` |
| 电话：美国写法、带 `+` 的国际写法、中国手机号 `1[3-9]` 开头 11 位 | `[PHONE]` | `+1 (212) 555-0100`、`212-555-0100`、`(212) 555-0100`、`+86 138 1234 5678`、`13812345678` | `1934`、`Securities Exchange Act of 1934`、`span_ids [12, 15]`、`10 days` |

- 只在写事件时用（第 6.6 节）。模型看到的工具结果和合同都是原文。
- 表里每一格都是 `test_tools.py` 的用例。

---

## 6. M4 Agent 循环（`agent.py`）

### 6.1 接口

```python
def prompt_hash() -> str          # SYSTEM、USER_TEMPLATE、NUDGE、TOOL_SCHEMAS 的 SHA-256 前 12 位

async def review_a1(
    conn: sqlite3.Connection, contract: Contract, checklist: Checklist,
    config: ReviewConfig, llm: LLM,
) -> tuple[list[Finding], Usage]
    # save_contract → create_review → created → parsing → reviewing → run_review()

async def run_review(
    conn: sqlite3.Connection, review_id: str, contract: Contract, checklist: Checklist,
    config: ReviewConfig, llm: LLM,
) -> tuple[list[Finding], Usage]
    # 只跑状态为 pending 的项；M6 的 resume_review() 也调用它
```

- `parsing` 这一步不做事：`load_contractnli()` 已经调用过 `annotate_contract()`。阶段 5 的 PDF 在这一步解析。
- `review_a1()` 不计时。延迟由 `review_contract()` 计算（和 B0 一样）。

### 6.2 提示词（C1）

全部用英文。写成模块常量，`prompt_hash()` 覆盖它们和 `TOOL_SCHEMAS`。

**system = `SYSTEM` + `"\n\nContract:\n"` + `format_contract(contract)`。** 同一份合同的 17 项完全相同。不放时间戳、`review_id`、检查项编号。`SYSTEM` 至少包括：

1. 任务：一次审 1 个检查项，判断合同和假设的关系。
2. 3 个标签的定义，和 B0 的 `SYSTEM` 写法一致。
3. 证据规则，和 B0 一致：句子编号写成整数；"支持"和"相反"至少引用 1 句；"没提到"不引用句子。
4. 工具的用法：大写的术语用 `lookup_definition` 查定义；看到 `Section 5` 这类引用用 `get_section` 跟过去；注意 `except`、`provided that`、`notwithstanding` 这类例外；判"没提到"之前用 `search_spans` 确认。
5. 交卷：结论只能通过 `record_finding` 登记，文字回答不算。`rationale` 写 1 句。`self_confidence` 是 0–100 的整数。

**user = `USER_TEMPLATE`：**

```
Checklist item
id: {id}
hypothesis: {hypothesis}
guidance: {guidance}

Decide the label for this item, then call record_finding.
```

**`NUDGE`（C2）：** `You have not recorded a finding. Call the record_finding tool now; a plain-text answer is not recorded.`

### 6.3 一次审核的共享状态

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
    usage: Usage                      # 这次审核的累计用量，每次调用（包括被截断的）都加
    def bill(self, completion: ChatCompletion) -> None      # add_usage()
    def budget_left(self) -> bool     # usage.output_tokens < config.max_review_output_tokens
```

- 所有项在同一个事件循环的线程里，`bill()` 是同步的，不需要锁。
- 预算在每次调用**之前**检查。正在进行的调用可能让总量超过上限一点。这是允许的，在交付汇报里写出实际最大值。

### 6.4 单项循环：`run_item()`

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
                on_truncated = bill + 写 model_call 和 length_retry 事件 + stats.n_length_retries += 1,
                allow_retry = ctx.budget_left)
        except ModelStopped as e:        # refusal，或第 2 次还截断
            bill(e.completion); abstain(item, e.reason); return stats
        bill(completion); 写 model_call 事件; stats.n_steps = step
        message = completion.choices[0].message
        messages.append(assistant_dict(message))       # 原样，含 reasoning_content 和 tool_calls
        if not message.tool_calls:                     # C2
            messages.append({"role": "user", "content": NUDGE}); stats.n_nudges += 1
            写 nudge 事件; continue
        for call in message.tool_calls:                # 按顺序执行
            outcome = await run_tool(...); 更新 stats; 写 tool_call 事件
            if outcome.record is not None and outcome.ok:
                finish_verified(item, outcome.record); return stats     # 后面的调用不执行
            messages.append({"role": "tool", "tool_call_id": call.id, "content": outcome.text})
    abstain(item, "budget_exceeded", kind="steps")
    return stats
```

- **`assistant_dict(message)`：** 把 SDK 的消息转成字典，必须保留 `reasoning_content`（SDK 把它当成额外字段）和 `tool_calls`，去掉值为 `None` 的键。2026-10-25 在本机的 openai SDK 上确认：`message.model_dump(exclude_none=True)` 保留 `reasoning_content` 和 `tool_calls`。用测试锁定这一点，SDK 的行为变了时测试会失败。
- **`abstain(item, reason, kind=None)`：** 在一个事务里：用当前 `attempt` 的 `operation_id` 写一行占位结论（`label="NotMentioned"`、`span_ids=[]`、`rationale=""`、`reason`、`outcome="abstained"`，用 `db.insert_finding()`）；`transition_item(drafting → abstained)`。`budget_exceeded` 另写一条事件 `budget_exceeded`，`payload` 有 `kind`（`steps` 或 `review_tokens`）、`step` 和这次审核的累计输出 token。
- **`finish_verified(item, record)`（A1）：** 在一个事务里：`drafting → verifying`；`verifying → verified`；`db.set_finding_outcome(conn, record.operation_id, "verified")`。阶段 4 的 A2 在 `verifying` 这里插入核对器。
- 登记被拒绝时，错误作为工具结果交回模型，循环继续。被拒绝的登记不占用 `operation_id`（阶段 2 的规则），改正后在同一个 `attempt` 里可以登记成功。

### 6.5 审核循环：`run_review()`

```
run_review(...):
    ctx = ReviewContext(..., tools=ContractTools(contract), system=build_system(contract))
    items = [checklist 里状态为 pending 的项]，按检查清单的顺序
    stats = {}
    try:
        if items:
            stats[items[0].id] = await run_item(ctx, items[0])      # C5：缓存预热
            sem = asyncio.Semaphore(config.concurrency)
            async with asyncio.TaskGroup() as tg:  其余的项各一个任务，任务里 async with sem
        transition_review(reviewing → completed)
    except Exception as exc:
        transition_review(reviewing → failed, error=f"{type(exc).__name__}: {exc}")
        raise   # TaskGroup 抛出 ExceptionGroup 时，抛出其中第 1 个异常，评估运行器才能记下它的类型
    findings = 每个检查项当前 attempt 的结论（db.get_finding(operation_id(...))），附上 stats（没有的为 None）
    return findings, ctx.usage
```

- **只把 `Exception` 当成失败（第 3.6 节缺口 4）。** `KeyboardInterrupt`、`asyncio.CancelledError` 和测试里的 `Crash(BaseException)` 不改状态，审核停在 `reviewing`，和进程被杀一样。
- 一项抛出 `Exception`（例如 `ModelInterrupted`、SDK 重试后的 `APIError`）时，`TaskGroup` 取消其他项。被取消的项停在 `drafting`，审核记为 `failed`（C6）。评估运行器记下错误，`--resume` 重跑整份合同（D4）。
- 返回的结论按检查清单的顺序，17 条。

### 6.6 事件（B4、B5）

全部事件经过 `log_event(ctx, type, payload, item_id)`：先 `tools.redact_obj(payload)`，再 `db.add_event()`。阶段 2 的事件类型不变。新加的类型：

| `type` | 什么时候写 | `payload` |
|---|---|---|
| `model_call` | 每次模型调用之后，包括被截断的 | `{"attempt", "step", "max_tokens", "finish_reason", "content", "reasoning_content", "tool_calls": [{"id", "name", "arguments"}], "usage": {"input", "cache_hit", "output", "reasoning"}, "cost_usd"}` |
| `length_retry` | 截断后决定重做时 | `{"attempt", "step", "max_tokens_from", "max_tokens_to"}` |
| `tool_call` | 每次工具调用之后 | `{"attempt", "step", "tool_call_id", "name", "arguments", "ok", "error_kind", "result", "duration_ms"}` |
| `nudge` | 追加 `NUDGE` 时 | `{"attempt", "step"}` |
| `budget_exceeded` | 超出步数或总预算时 | `{"attempt", "step", "kind", "review_output_tokens"}` |
| `review_recovered` | M6 恢复一次审核时 | `{"reset_items": [item_id, ...]}` |

- `arguments` 存模型给的原始字符串，`result` 存交给模型的完整文本。都要脱敏。
- 每一项的 `stats` 也能从这些事件重新算出来。`test_agent.py` 用 1 个用例核对两者一致。

---

## 7. M6 断点恢复（`recovery.py`）

```python
def recover(conn: sqlite3.Connection) -> list[str]
async def resume_review(
    conn: sqlite3.Connection, review_id: str, llm: LLM, *, checklist: Checklist | None = None,
) -> tuple[list[Finding], Usage]
```

**`recover()`：**
1. `db.list_reviews(conn, ("created", "parsing", "reviewing"))`。包括 `created`（第 3.6 节缺口 6）。
2. 每个审核：状态是 `drafting` 或 `verifying` 的项，`transition_item(..., → "pending")`（`attempt + 1`，`retries` 不变）。已经结束的项不动。
3. 写一条 `review_recovered` 事件。
4. 返回这些审核的 `review_id`，按 `created_at` 排序。
- 只改数据库，不调用模型。同一个数据库连续调用 2 次，第 2 次不改任何东西（只是再返回同样的 `review_id`，不再写事件也可以；默认做法：没有项被重置时不写事件）。

**`resume_review()`：**
1. 读出审核（`config` 在 `reviews` 表里）和合同（`db.load_contract()`）。`checklist` 为 `None` 时，用 `load_checklist(review.checklist_id)`。
2. 状态补齐到 `reviewing`：`created → parsing → reviewing`，或者 `parsing → reviewing`。
3. `config.method` 是 `A1` 时调用 `agent.run_review()`。`A2` 在阶段 4 加，现在抛出 `NotImplementedError`。
- 返回的用量只算恢复以后的调用。崩溃前结束的项，`stats` 是 `None`（第 3.6 节缺口 5）。

**结论已登记、状态还没改就崩溃（D3）：** 这一项停在 `drafting`，`recover()` 把它退回 `pending`，`attempt + 1`，重做。旧 `attempt` 的结论留在 `findings` 表里，`run_review()` 只读当前 `attempt` 的结论。

---

## 8. `db.py` 的补充

```python
def set_finding_outcome(
    conn, operation_id: str, outcome: Outcome, *,
    checks: dict[str, str] | None = None, support_score: int | None = None,
) -> None                          # 找不到时抛出 KeyError；阶段 4 也用它
def list_reviews(conn, statuses: Iterable[ReviewStatus]) -> list[ReviewRow]   # 按 created_at 排序
```

表结构不变，`SCHEMA_VERSION` 仍然是 1。`AgentStats` 不存数据库。

---

## 9. 入口、评估和配置

### 9.1 `review.py`

```python
async def review_contract(
    contract: Contract, checklist: Checklist, config: ReviewConfig, llm: LLM,
    *, conn: sqlite3.Connection | None = None,
) -> ReviewResult
```

- `B0`：不用 `conn`，行为不变。
- `A1`：`conn` 为 `None` 时用 `db.connect(":memory:")`，用完关闭。调用 `agent.review_a1()`。
- `A2`：仍然抛出 `NotImplementedError`（阶段 4）。

### 9.2 `eval/run.py`

- `prompt_hash(config)`：`A1` 返回 `agent.prompt_hash()`。
- `config.method != "B0"` 时，打开 `db.connect(run_dir / "reviews.db")`，传给每次 `review_contract(..., conn=conn)`，结束时关闭（`finally`）。4 份合同同时跑，共用这 1 个连接：所有数据库调用都是同步的，在同一个线程里。
- `--resume`：做到一半的合同整份重做（D4）。数据库里会留下旧审核（状态是 `reviewing` 或 `failed`），不处理。`save_contract()` 遇到同一份合同不改动。
- 其他不变。

### 9.3 `eval/metrics.py`

至少有 1 条结论带 `stats` 时，`metrics["agent"]` 包括：

| 键 | 内容 |
|---|---|
| `n_items_with_stats` | 有 `stats` 的结论数 |
| `avg_steps`、`max_steps` | 每项的平均步数、最大步数 |
| `tool_calls_total`、`tool_calls_per_item` | 每个工具的总调用次数、每项平均次数 |
| `n_tool_errors`、`n_rejected_records`、`n_nudges`、`n_length_retries` | 总数 |

`run_eval()` 打印结果表以后，再打印这几行。`cache_read_ratio` 已经有了，不用改。

### 9.4 配置

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

`configs/a1-p.yaml` 相同，`name: A1-P`、`agent_model: deepseek-v4-pro`。阶段 3 不跑 A1-P。

---

## 10. 测试

全部用 `FakeLLM`，不花钱。依赖数据的测试用 `contractnli_available` fixture。合同用手写的小合同（10 句左右，含定义、`Section 2` 引用、`except` 例外、1 个假邮箱和 1 个假电话），放进 `tests/conftest.py` 的 fixture。

| 文件 | 至少要测的内容 |
|---|---|
| `test_llm.py` | `check_tool_completion()`：第 3.1 节表的每一行；`FakeLLM.create()` 返回 `tool_call_reply()` 和 `text_reply()`；`parse()` 和 `create()` 拿到错的步骤类型时抛出 `AssertionError`；`call_with_length_retry()`：第 1 次截断、第 2 次成功，`on_truncated` 被调用 1 次，第 2 次的 `max_tokens` 是 2 倍；2 次都截断，抛出 `ModelStopped("max_tokens")`；`allow_retry` 为 `False` 时不重做；拒绝回答不重做 |
| `test_baseline.py` | 截断 1 次后成功：17 条正常结论，`n_calls` 是 2，用量是 2 次之和；截断 2 次：17 条 `abstained`，原因 `max_tokens`；`prompt_hash()` 仍然是 `50fb0745f4aa` |
| `test_tools.py` | `search_spans`：含查询词的句子排第一；`top_k` 生效；没有匹配时的提示；`top_k=50` 返回参数错误。`read_spans`：`window` 生效；合同开头和结尾不越界；不连续时有 `...`；不存在的编号返回错误。`lookup_definition`：大小写、`the `、单复数；查不到时列出已定义的术语。`get_section`：`"2(a)"` 找到 `"2"`；超过 40 句时截断并提示；查不到时列出章节号。参数：不是 JSON、缺字段、多字段、未知工具，各 1 个用例。超时（第 5.5 节）。`record_finding`：成功；被拒绝时 `text` 里有 `validate_finding()` 的错误。脱敏：第 5.6 节表的每一格 |
| `test_agent.py` | 单项：`search_spans` → `record_finding`，结果 `verified`，`stats` 正确，`findings` 里 1 行。模型只回文字：追加 `NUDGE`，`n_nudges` 为 1，之后登记成功。登记被拒绝后改正：`n_rejected_records` 为 1，同一个 `attempt` 登记成功。**超过 8 步：`abstained`，原因 `budget_exceeded`**，`findings` 里有 1 行占位结论。总预算用完：剩下的项不再调用模型，都是 `abstained`、原因 `budget_exceeded`。截断后重做成功；截断 2 次后 `abstained`、原因 `max_tokens`。拒绝回答：`abstained`、原因 `refusal`。`ModelInterrupted`：审核 `failed`，异常抛出。一次回复里有 2 个工具调用：都执行，各有 1 条 `tool` 消息；`record_finding` 成功后，同一次回复里后面的调用不执行。**`reasoning_content` 被发回：** 第 2 次调用的 `messages` 里，上一条助手消息有 `reasoning_content`。17 项的 `system` 完全相同。第 1 项的全部调用在其他项之前。**17 项全部结束，每项在 `findings` 里只有 1 行，没有重复的 `operation_id`。** 事件里没有原文的假邮箱和假电话。从事件重新算出的统计和 `stats` 一致 |
| `test_recovery.py` | **崩溃：** `concurrency=1`，前 5 项正常结束，第 6 项的第 1 次调用抛出 `Crash(BaseException)`。之后：5 项是终止状态，第 6 项是 `drafting`，其余是 `pending`，审核是 `reviewing`。`recover()` 返回这个审核，第 6 项变成 `pending`、`attempt` 为 2、`retries` 为 0。`resume_review()` 用新的 `FakeLLM` 跑完：17 项完整，审核 `completed`，每项当前 `attempt` 只有 1 条结论，前 5 项的结论没有被重写。**已登记、未改状态：** 登记成功以后、`finish_verified()` 之前抛出 `Crash`（`monkeypatch`）；恢复后这一项有 2 行结论，最终结果取 `attempt` 2 的。**`created` 的审核**被恢复并跑完。`recover()` 调用 2 次，第 2 次不改动任何项 |
| `test_db.py` | `set_finding_outcome()` 改 `outcome`；找不到时 `KeyError`。`list_reviews()` 按状态过滤、按时间排序 |
| `test_run.py` | A1 + `FakeLLM` 跑 2 份 dev 合同：`reviews.db` 存在，`findings.jsonl` 有 2 行，每份 17 条结论，`metrics.json` 有 `agent` 部分，`manifest.json` 的 `prompt_hash` 是 `agent.prompt_hash()` |
| `test_metrics.py` | 手写 2 条带 `stats` 的结论，`agent` 部分的每个数字都对；`budget_exceeded` 算"没有回答" |
| `test_live.py` | 1 个 `live` 用例：A1-F 审合同 456 的前 2 项。默认跳过，**不要自己运行** |

---

## 11. 施工顺序和检查点

1. `uv add rank-bm25==0.2.2`。`models.py` 的改动。`uv run pytest` 仍然全部通过（阶段 0–2 的 250 个离线测试）。
2. `llm.py`（第 3 节）和 `test_llm.py`。
3. B0 的截断处理（第 4 节）和 `test_baseline.py`。确认 `prompt_hash()` 不变。
4. `db.py` 的补充（第 8 节）。
5. `tools.py`（第 5 节）和 `test_tools.py`。
6. `agent.py` 的单项循环（第 6.2–6.4、6.6 节），先只测单项。
7. `agent.py` 的审核循环（第 6.5 节）、`review.py`、`test_agent.py`。
8. `recovery.py` 和 `test_recovery.py`。
9. `eval/run.py`、`eval/metrics.py`、配置、`test_run.py`、`test_metrics.py`。
10. **检查点 1：** `uv run pytest` 全部通过。向用户汇报测试数量和第 12 节离线部分的结果。
11. **检查点 2：用户同意后，跑 5 份 dev 合同。** 先告诉用户：85 个检查项，每项估计 3–5 次模型调用，共约 250–450 次调用，估计 $0.2–0.3（Flash，非高峰；高峰时段乘以 2）。用户同意后运行：
    ```bash
    uv run python -m provenclause.eval run --config configs/a1-f.yaml --split dev --limit 5
    ```
    检查：第 2 项以后 `cache_read_input_tokens` 大于 0（为 0 时，检查 system 提示词里有没有每次都变的内容）；没有 400 错误；每份合同的实际成本和输出 token；有没有项碰到 `max_steps` 或总预算。按实测值建议 `max_review_output_tokens` 的数值，由用户决定是否修改。
12. **检查点 3：用户同意后，跑完整的 dev。** 先按检查点 2 的实测成本乘以 61 / 5，告诉用户估计成本。如果在检查点 2 之后改了提示词，`prompt_hash` 会变，就开一次新的运行，不用 `--resume`。
13. 更新第 13 节的文档。
14. 按 CLAUDE.md 工作流程第 3 条，推送前要运行 `neat-freak`。阶段 3 由用户决定何时提交和推送，你只在汇报里提醒这一点。

---

## 12. 验收标准

来自实现路线第 3 节"阶段 3"：

- [ ] 测试（FakeLLM）：一份合同的 17 项全部结束，`findings` 里没有重复结论。
- [ ] 测试（FakeLLM）：超过 8 步，结果为 `abstained`、原因为 `budget_exceeded`。
- [ ] 测试：含有假邮箱和假电话号码的工具结果，写入 `events` 后被替换。
- [ ] 测试（FakeLLM）：跑完 5 项后抛出异常模拟崩溃；调用恢复函数后，17 项完整，没有重复。
- [ ] 数字：A1-F 和 B0-F 在 dev 上的对比表。增加平均步数、各工具的调用次数、缓存命中率。

本文另加：

- [ ] B0 的 `prompt_hash` 仍然是 `50fb0745f4aa`；B0 截断后加倍重做的测试通过。
- [ ] 测试：`reasoning_content` 每轮都发回；17 项的 `system` 完全相同。
- [ ] 数字：第 2 项以后的缓存命中率大于 0；每份合同的成本、P50 / P95 延迟；各放弃原因的条数；提醒、截断重做、工具错误、被拒绝的登记的次数。
- [ ] 阶段 0–2 的全部测试仍然通过。

---

## 13. 交付时要更新的文档

1. [实现路线](ProvenClause_实现路线.md)第 3 节"阶段 3"：勾选验收标准，写一段完成情况，附 A1-F 和 B0-F 的对比表（格式同阶段 1 的表，加平均步数、工具调用次数、缓存命中率）。和 B0-F 比较时，说明 A1 在哪些检查项上变好或变差，尤其是 B0 最弱的 nda-1、nda-16、nda-20，以及"没提到"被判成"支持"或"相反"的条数。
2. [详细设计](ProvenClause_详细设计.md)：
   - M4：写明提醒（C2）、步数的算法（C3）、总预算（C4）、截断后加倍重做（C6）、`failed` 和 `abstained` 的区别。
   - M5：工具结果的格式、上限、不开 strict、超时的做法、脱敏范围。
   - M6：包括 `created`；评估续跑时整份合同重做。
   - 第 9.2 节：用 A1-F 的实测值更新成本估算表（A1-F、A1-P、A2-F、A2-P 和合计）。
   - 第 9.3 节：`tool_choice`、strict、`reasoning_content` 改成已确认的结论；`length` 改成"加倍重做 1 次后再放弃"。
3. [技术决策记录](ProvenClause_技术决策记录.md)：
   - 第 3 节表格里 M4、M5、M6 的状态改成"已实现"，`llm.py` 改成"阶段 1、3 部分已实现"。
   - 施工时改了的规则或数值（例如 `max_review_output_tokens`、停用词表），写进第 3.6 节，格式同第 3.5 节的"施工时的规则调整"。
   - **M4、M6 是核心模块。** 按 CLAUDE.md 工作流程第 2 条，在本地讲解文档 `docs/讲解/` 里各加一节"模块讲解"。不要写进技术决策记录。如果你在 git worktree 里工作，看不到这个目录，就把这两节写进交付汇报，由用户放进本地文件。至少讲清楚：
     - M4：为什么逐项而不是一次 17 项；为什么合同放在 system 开头（缓存前缀，价格约 1/50）；为什么第 1 项先跑；`tool_choice` 不能用 `required` 时怎么保证交卷；`reasoning_content` 为什么要发回；为什么截断只加倍重做 1 次；为什么服务器故障记为 `failed` 而不是 `abstained`；预算为什么有两层（每项步数、每次审核的 token）。
     - M6：为什么按检查项存档；`attempt + 1` 怎么避免和旧结论冲突；为什么只把 `Exception` 当成失败；"已登记、未改状态"的情况怎么处理；评估续跑为什么不用 M6（成本和延迟的指标）。
4. [README](../README.md)：加 A1-F 的命令；目录里加 `tools.py`、`agent.py`、`recovery.py`；依赖里加 `rank-bm25`。
5. [CLAUDE.md](../CLAUDE.md)：更新"当前进度"，加上 A1-F 的数字。

---

## 14. 交付汇报的格式

1. 做了什么：新建和修改的文件。
2. 验收标准：每一条是否通过，附证据（测试输出、对比表）。
3. 实际花费：每次真实运行的 run 目录、合同数、成本。
4. 和本文不同的地方，以及原因。
5. 发现的问题和需要用户决定的事（例如 `max_review_output_tokens` 的数值，提示词要不要调）。
6. 提交了没有。没有用户同意，不提交。
