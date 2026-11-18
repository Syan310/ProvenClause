# ProvenClause 阶段 1 施工文档：MVP（B0 单次审核 + 打分）

写于 2026-10-21。读者：负责施工的 Agent。用户不逐行审代码，但要能看懂每个模块。

> **2026-10-21 修改：模型换成 DeepSeek。** 本文按 Claude 写成。施工时用户决定只用 DeepSeek Flash 和 DeepSeek V4 Pro（见技术决策记录第 1.2、3.3 节）。实际做法和本文不同的地方：
>
> | 本文 | 实际 |
> |---|---|
> | `anthropic` SDK、`AnthropicLLM`、`ANTHROPIC_API_KEY` | `openai>=3.36,<4`、`DeepSeekLLM`（原生接口 `https://api.deepseek.com`）、`DEEPSEEK_API_KEY` |
> | `messages.parse` 结构化输出，返回 `ParsedMessage` | JSON 模式 + 提示词里的 Schema + Pydantic 校验，返回 `ParsedChatCompletion` |
> | `ModelStopped(reason, message)` | `ModelStopped(reason, completion)`。`stop_reason` 换成 `finish_reason`：`content_filter` → `refusal`，`length` → `max_tokens` |
> | — | 新增 `ModelInterrupted`：`finish_reason` 是 `insufficient_system_resource` 或 `aborted` 时抛出，运行器把它当成 API 错误 |
> | Haiku 的 10 万 token 两档价格 | DeepSeek 的峰谷价格，按 `completion.created` 的时间选 |
> | `call_cost(model, sdk_usage)`、`add_usage(total, model, sdk_usage)` | 多一个参数 `at`（调用时间） |
> | `configs/b0-h.yaml`、`configs/b0-o.yaml`；B0-H | `configs/b0-f.yaml`、`configs/b0-p.yaml`；B0-F |
> | `manifest.json` 记 `anthropic` 版本 | 记 `openai_version`，另加 `checklist_hash` 和 `resumes` |
> | `--resume` 比较 `prompt_hash`、模型、`split` | 比较 `prompt_hash`、完整配置、`split` |
> | — | `Usage` 加 `reasoning_tokens` |

---

## 0. 开工前必读

### 0.1 先读这些文件

1. [CLAUDE.md](../CLAUDE.md)：项目规则。
2. [技术决策记录](ProvenClause_技术决策记录.md)的第 1 节（已核实的事实）、第 2.3 节（全局约定）和第 3.2 节（阶段 1 的选型）。**全局约定不能违反。**
3. [实现路线](ProvenClause_实现路线.md)：第 2 节的接口和第 3 节"阶段 1"。
4. [详细设计](ProvenClause_详细设计.md)第 8.1 节（指标）、第 9.3 节（API 使用要点）。
5. 现有代码：`src/provenclause/` 下的全部文件和 `tests/`。阶段 0 的代码不改行为。

### 0.2 规则

> **注意：真实调用 API 要花钱。** 运行任何真实的模型调用之前，先告诉用户要调用什么、调用多少次、估计多少钱，等用户明确同意。这包括 `pytest -m live` 和 `eval run`。

1. 写调用 Claude API 的代码之前，先用 `/claude-api` skill 确认用法。不要凭记忆写 SDK 代码。
2. 第 2.3 节的决定不能改。如果你认为某个决定行不通，停下来，向用户说明原因，由用户决定。
3. 本文写成"默认做法"的地方，可以直接照做。本文没有覆盖的设计问题，先问用户。
4. **不要用 test 和 test-40。** 阶段 1 只用 dev。
5. 不要推送到 GitHub，不要打 tag，不要删除文件。提交前先问用户。
6. 输出到控制台的合同文本要先调用 `sys.stdout.reconfigure(encoding="utf-8")`（见 CLAUDE.md）。

### 0.3 阶段 1 的目标

输入一份合同，B0 一次调用模型，输出 17 条带证据句子编号的结论。评估程序拿它和标准答案比较打分。B0-H（Haiku 5.5）在 dev 61 份合同上跑完，产出第一张结果表和实测成本。

---

## 1. 要新建和修改的文件

| 文件 | 内容 | 新建 / 修改 |
|---|---|---|
| `pyproject.toml` | 加依赖、pytest 配置 | 修改 |
| `.env.example` | `ANTHROPIC_API_KEY=` 占位 | 新建 |
| `src/provenclause/models.py` | `Finding`、`ReviewConfig`、`Usage`、`ReviewResult` | 修改 |
| `src/provenclause/parse.py` | `format_contract()` | 修改 |
| `src/provenclause/llm.py` | `LLM` 协议、`AnthropicLLM`、`FakeLLM`、价格和成本计算 | 新建 |
| `src/provenclause/baseline.py` | B0 | 新建 |
| `src/provenclause/review.py` | `review_contract()`：按 `config.method` 分派 | 新建 |
| `src/provenclause/eval/metrics.py` | 指标 | 新建 |
| `src/provenclause/eval/run.py` | 评估运行器 | 新建 |
| `src/provenclause/eval/__main__.py` | 命令行入口 `python -m provenclause.eval run ...` | 新建 |
| `configs/b0-h.yaml`、`configs/b0-o.yaml` | 两组 B0 配置。b0-o 只写好，阶段 1 不跑 | 新建 |
| `tests/` | 见第 8 节 | 新建 |

---

## 2. 依赖和配置

1. 加运行依赖：`uv add "anthropic>=1.17,<2" python-dotenv`。
2. 加开发依赖：`uv add --dev pytest-asyncio`。
3. `pyproject.toml` 的 `[tool.pytest.ini_options]` 加：
   ```toml
   asyncio_mode = "auto"
   markers = ["live: calls the real Claude API and costs money"]
   addopts = "-m 'not live'"
   ```
4. `.env` 已经在 `.gitignore` 里。新建 `.env.example`，只写一行 `ANTHROPIC_API_KEY=`，提交到 Git。

---

## 3. `models.py`

按实现路线第 2 节加 `Outcome`、`Finding`、`ReviewConfig`、`Usage`、`ReviewResult`。已有的 4 个类不改。

**补充规定：**

- `ReviewConfig` 和 `ChecklistItem` 一样设置 `extra="forbid"`。配置文件里写错的键会报错。
- `ReviewConfig.effort` 的类型是 `Literal["low", "medium", "high", "xhigh", "max"]`。
- 加一个常量：

  ```python
  # 模型没有给出结论时的 reason。打分时这些条目算"没有回答"，见第 6 节
  NO_ANSWER_REASONS = frozenset({"missing", "refusal", "max_tokens", "parse_error"})
  ```

- **没有结论时 `label` 怎么填（默认做法）：** `label` 是必填字段，不改成可选。模型没有给出结论时，`label` 填 `"NotMentioned"`、`span_ids` 为空、`outcome="abstained"`、`reason` 填上面 4 个值之一。打分时按 `reason` 判断，不看这个占位的 `label`。

---

## 4. `llm.py`（F6）

`llm.py` 是唯一调用模型的地方。阶段 1 只实现结构化输出（`parse`）。阶段 3 再加工具调用（`create`）。

### 4.1 接口

```python
class LLM(Protocol):
    async def parse(
        self, *, model: str, system: str, messages: list[dict],
        output_format: type[T], max_tokens: int, effort: str,
    ) -> ParsedMessage[T]: ...
```

- 返回值是 SDK 自己的结构化输出消息类型。用已安装 SDK 里的真实类名，不要自己定义同样的类。
- `AnthropicLLM(client: AsyncAnthropic | None = None)`：`client` 为空时创建 `AsyncAnthropic(max_retries=4)`。凭证由 SDK 从环境变量读取。
- **要核对的一点：** 调用时同时传 `output_format=...` 和 `output_config={"effort": effort}`。先读已安装 SDK 的 `messages.parse` 源码，确认两者会合并，不会互相覆盖。如果不会合并，找到 SDK 支持的写法，并在交付时说明。
- **不传 `thinking` 参数。** 两个模型默认打开自适应思考；Opus 5.5 不能关闭思考。两边保持相同，只用 `effort` 控制。
- 响应的 `content` 里可能先有 `thinking` 块。读取内容时按 `block.type` 判断，或者直接用 `parsed_output`。

### 4.2 停止原因

`AnthropicLLM.parse()` 检查每次响应，下面 3 种情况抛出 `ModelStopped(reason, message)`。异常要带上原始消息，因为这些调用也要计费，调用方要从 `message.usage` 记账。

| 情况 | `reason` |
|---|---|
| `stop_reason == "refusal"` | `"refusal"` |
| `stop_reason == "max_tokens"` | `"max_tokens"` |
| `parsed_output` 为 `None`，或者解析失败 | `"parse_error"` |

API 错误（400、401、重试后仍失败的 429 和 5xx、网络错误）不要捕获，直接抛出。重试已经由 SDK 完成。

### 4.3 价格和成本

```python
@dataclass(frozen=True)
class Price:          # 美元 / 百万 token
    input: float
    output: float
    cache_read: float
    cache_write: float
```

| 模型 | input | output | cache_read | cache_write |
|---|---|---|---|---|
| `claude-haiku-5-5`（提示词 ≤ 10 万 token） | 0.10 | 0.50 | 要核对 | 要核对 |
| `claude-haiku-5-5`（提示词 > 10 万 token） | 0.50 | 2.50 | 要核对 | 要核对 |
| `claude-opus-5-5` | 4.00 | 20.00 | 0.20 | 要核对 |

- "要核对"的价格用 `/claude-api` skill 或官方价格页确认。确认不了时，按 cache_read = 0.1 × input、cache_write = 1.25 × input 计算，并在代码注释和交付汇报里写明这是估算值。
- "提示词"按 `input_tokens + cache_read_input_tokens + cache_creation_input_tokens` 计算。NDA 都远小于 10 万 token，但代码要处理这种情况。
- 写 2 个纯函数：
  - `call_cost(model, sdk_usage) -> float`
  - `add_usage(total: Usage, model, sdk_usage) -> Usage`：累加 4 种 token、`n_calls + 1`、`cost_usd`。
- 未知的模型名抛出错误，不要按 0 计价。

### 4.4 `FakeLLM`

- `FakeLLM(script)`：`script` 是一个列表，每一项是下面 3 种之一，按调用顺序依次使用：
  1. Pydantic 对象：正常返回，`stop_reason="end_turn"`。
  2. `ModelStopped` 或其他异常实例：抛出它。
  3. 函数 `(request: dict) -> 上面两种之一`：根据请求内容决定返回什么。
- 每次调用把请求参数存进 `fake.calls`，测试可以检查提示词。
- 用量固定（例如 input 1000、output 200），也可以在构造时指定。
- 写一个辅助函数生成 SDK 的消息对象，`FakeLLM` 和 `AnthropicLLM` 的测试都用它。
- 脚本用完后再被调用，抛出 `AssertionError`。

---

## 5. B0：`parse.py`、`baseline.py`、`review.py`

### 5.1 `format_contract(contract) -> str`

每个句子一行，格式是 `[S{span_id}] {text}`，行与行之间用 `\n` 连接。数据集的句子里没有换行符（阶段 0 已在 dev 上检查过）。B0、A1、A2 共用这个函数。

### 5.2 输出结构

```python
class B0Item(BaseModel):
    item_id: str
    label: Label
    span_ids: list[int]
    rationale: str

class B0Output(BaseModel):
    findings: list[B0Item]
```

`item_id` 用 `str`，不用 `Literal`。不认识的 ID 在后处理里丢掉。结构化输出不支持数组长度约束，所以条目数量也在后处理里检查。

### 5.3 提示词

- 用英文写。
- `system` 放固定的说明：任务、3 种结论的含义、证据规则（"支持"和"相反"至少引用 1 个句子，"没提到"不引用）、只能引用 `[S..]` 里的编号、每个检查项输出 1 条、`rationale` 写 1 句话。
- `user` 消息放 `format_contract()` 的全文，然后列出 17 个检查项的 `id`、`hypothesis` 和 `guidance`。
- 不加示例。dev 和 test 的内容绝不能进提示词。
- 提示词里不能有每次都变的内容，例如时间和随机数。

### 5.4 提示词哈希

`prompt_hash()` 返回 SHA-256 的前 12 位。输入是 `system` 文本、`user` 消息的模板（不含合同和检查清单的具体内容）和 `B0Output.model_json_schema()` 的 JSON（`sort_keys=True`）。合同不同，哈希不变；提示词改一个字，哈希就变。写进 `manifest.json`。

### 5.5 后处理

1. 按 `item_id` 建表。同一个 ID 出现多次时，只保留第一条。
2. 每个检查项：
   - 模型给出了：`Finding(outcome="verified", reason=None)`。按实现路线第 4 节 #1，B0 的结论全部算 `verified`。
   - 模型没给：按第 3 节的规定填，`reason="missing"`。
3. 不存在的 `span_id` **保留原样**（实现路线第 4 节 #4），由指标单独计数。
4. 不认识的 `item_id`、重复的条目：丢掉，用 `logging.warning` 记下数量。
5. 结果按检查清单的顺序排列。

**整次调用失败：**

- 抛出 `ModelStopped`：17 条都按第 3 节填，`reason` 用异常的 `reason`。用量照常从 `exc.message.usage` 累加。
- API 错误：不捕获，交给运行器处理（第 7.4 节）。

### 5.6 `review_contract()`

```python
async def review_contract(contract, checklist, config, llm) -> ReviewResult
```

- 放在 `review.py`。按 `config.method` 分派：`"B0"` 调用 `baseline.py`，`"A1"`、`"A2"` 抛出 `NotImplementedError`。
- 用 `time.perf_counter()` 计算 `latency_s`，包括 SDK 重试的时间。

---

## 6. `eval/metrics.py`

只写纯函数，不调用模型，不读写文件。输入是 `list[ReviewResult]`、标准答案（`load_gold()` 的结果）和合同（用于检查 `span_id` 是否存在）。只对结果里出现的合同打分。

### 6.1 定义

"没有回答"：`reason` 属于 `NO_ANSWER_REASONS` 的条目。它在准确率和 F1 里算作一个不等于任何标准答案的预测。

| 指标 | 定义 |
|---|---|
| `accuracy` | 预测结论等于标准答案的条目 ÷ 全部条目。"没有回答"算错 |
| `f1_{label}`、`precision_{label}`、`recall_{label}` | 3 类分别算，全部条目合在一起算（合并计算）。分母为 0 时这一项为 0 |
| `f1_contradiction_paper` | 论文式：对每个检查问题单独算"相反"类 F1，再取平均。只包含这批结果里至少有 1 条标准"相反"的检查问题。同时输出用了哪些检查问题 |
| `evidence_precision`、`evidence_recall` | 只用标准答案是"支持"或"相反"的条目，合并计算：Σ\|预测 ∩ 标准\| ÷ Σ\|预测\|，Σ\|预测 ∩ 标准\| ÷ Σ\|标准\| |
| `verified_precision` | `outcome == "verified"` 的条目里，结论正确的比例 |
| `coverage` | `verified` 的条目 ÷ 全部条目 |
| `false_accepts` | `verified` 但结论错误的条目数 |
| `n_abstained_by_reason` | 按 `reason` 分别计数 |
| `n_invalid_span_ids` | 引用了不存在的 `span_id` 的次数 |
| `cost_total_usd`、`cost_per_contract_usd` | 来自 `ReviewResult.usage` |
| `latency_p50_s`、`latency_p95_s` | 每份合同的 `latency_s`。用 `statistics.quantiles(data, n=100, method="inclusive")` 的第 50 和第 95 个分位点；只有 1 份合同时两者都等于它 |
| `tokens`、`cache_read_ratio` | 4 种 token 的合计；`cache_read_input_tokens` ÷ 全部输入 token |
| `n_contracts`、`n_items` | 数量 |

### 6.2 打印结果表

再写一个函数，把指标打印成一张 Markdown 表：准确率、3 类 F1、论文式"相反"类 F1、证据精确率和召回率、已核实精确率、覆盖率、每份合同的成本、P50 / P95 延迟。

---

## 7. 评估运行器：`eval/run.py` 和 `eval/__main__.py`

### 7.1 命令

```bash
uv run python -m provenclause.eval run --config configs/b0-h.yaml --split dev --limit 5
uv run python -m provenclause.eval run --config configs/b0-h.yaml --split dev --resume runs/<run_id>
```

| 参数 | 说明 |
|---|---|
| `--config` | YAML 配置文件，用 `ReviewConfig.model_validate()` 读取 |
| `--split` | `dev`、`test40`、`test`。**`test40` 和 `test` 要另加 `--allow-test` 才能运行**，否则报错退出。阶段 1 不加这个参数 |
| `--limit N` | 按 `contract_id` 的数字顺序取前 N 份 |
| `--workers` | 同时审核几份合同，默认 4 |
| `--resume DIR` | 在已有的结果目录上继续 |
| `--runs-dir` | 结果放在哪里，默认 `runs/` |

- 用标准库 `argparse`。子命令是 `run`。阶段 6 会加 `gate`，所以现在就用子命令结构。
- 命令行入口先调用 `dotenv.load_dotenv()`，再创建 `AnthropicLLM`。
- 核心逻辑写成一个异步函数，`llm` 作为参数传入。测试直接调用它，传入 `FakeLLM`。

### 7.2 结果目录

`runs/<run_id>/`，`run_id` 是 `{config.name 小写}_{split}_{YYYYmmdd-HHMMSS}`，例如 `b0-h_dev_20261022-101500`。

| 文件 | 内容 |
|---|---|
| `manifest.json` | 见 7.3 |
| `findings.jsonl` | 每行是一份合同的完整 `ReviewResult`（`model_dump_json()`） |
| `errors.jsonl` | 失败的合同：`contract_id`、异常类型、信息、时间 |
| `metrics.json` | 全部完成后写入 |

### 7.3 `manifest.json`

必须有这 5 个字段（实现路线的验收标准）：

| 字段 | 内容 |
|---|---|
| `git_commit` | `git rev-parse HEAD`；另加 `git_dirty: bool` |
| `model` | `agent_model` 和 `verifier_model` |
| `effort` | `config.effort` |
| `prompt_hash` | 第 5.4 节 |
| `seed` | `eval.sample.SEED` |

另外记录：完整的配置、`split`、`limit`、合同编号列表、`anthropic` 版本、Python 版本、开始时间。

### 7.4 并发、错误和断点续跑

1. 用 `asyncio.Semaphore(workers)` 控制并发。
2. 每份合同完成后，立即往 `findings.jsonl` 追加 1 行，然后 `flush()` 和 `os.fsync()`。写文件只在一个地方做，避免两个任务同时写。
3. 一份合同抛出 API 错误：写进 `errors.jsonl`，继续跑其他合同。全部结束后，只要有错误，退出码就不为 0。`--resume` 会重跑这些合同。
4. `--resume`：
   - 读取已有的 `manifest.json`。配置的 `prompt_hash`、模型或 `split` 和这次不同时，报错退出。
   - 读取 `findings.jsonl`。最后一行不完整时（例如进程被强制结束），丢掉这一行，并把文件重写成只含完整的行。
   - 跳过已有的 `contract_id`，只审核剩下的合同。
5. 按 Ctrl+C 时，已经写入的行保留。不要在退出时写半行。
6. 全部完成后，用 `findings.jsonl` 里的全部结果计算 `metrics.json`，并打印结果表。

---

## 8. 测试

依赖数据的测试用已有的 `contractnli_available` fixture。除了 `live` 测试，其他测试都用 `FakeLLM`，不花钱。

| 文件 | 至少要测的内容 |
|---|---|
| `test_models.py` | `configs/b0-h.yaml` 能读取；`effort` 写错被拒绝；配置文件多一个键被拒绝 |
| `test_llm.py` | 用手算的用量核对 `call_cost`；Haiku 超过 10 万 token 用另一档价格；`add_usage` 能累加；未知模型报错。给 `AnthropicLLM` 传一个假的 client，检查 3 种 `ModelStopped` 的情况 |
| `test_baseline.py` | 正常的 17 条；缺 1 项 → `missing`；重复的条目只留第一条；不认识的 ID 被丢掉；不存在的 `span_id` 被保留；拒绝回答 → 17 条都 `abstained`，用量照常累加；提示词里有 `[S0]`；换一份合同 `prompt_hash` 不变，改提示词后变 |
| `test_metrics.py` | 手写 2–3 份小例子，手算出准确率、各类 F1、论文式 F1（包含一个没有"相反"的检查问题）、证据精确率和召回率、已核实精确率、覆盖率、分位数。"没有回答"算错 |
| `test_run.py` | 用 `FakeLLM` 在 3 份 dev 合同上跑完：3 行结果，`manifest.json` 有 5 个字段。**中断测试（用 `workers=1`，结果不受并发时序影响）：** 第 2 份合同时抛出异常，再用 `--resume` 跑完，结果是 3 行，没有重复，`FakeLLM` 只被调用了缺少的那几次。最后一行不完整时能续跑。不加 `--allow-test` 时 `test40` 报错 |
| `test_live.py` | 标记 `live`：用真实的 Haiku 对 dev 里最短的合同（`contract_id` 456，2,270 个字符）跑一次 B0，检查有 17 条结论、成本大于 0。**运行前先问用户** |

---

## 9. 施工顺序和检查点

1. 依赖和配置（第 2 节）→ `uv run pytest` 仍然通过。
2. `models.py`、`format_contract()` 和它们的测试。
3. `llm.py` 和 `test_llm.py`。
4. `baseline.py`、`review.py` 和 `test_baseline.py`。
5. `metrics.py` 和 `test_metrics.py`。
6. `run.py`、`__main__.py`、配置文件和 `test_run.py`。
7. **检查点 1：** 全部离线测试通过。停下来，向用户汇报，并请用户确认 `.env` 里有密钥。
8. **检查点 2：** 用户同意后，运行 `uv run pytest -m live`。
9. **检查点 3：** 用户同意后，跑 5 份合同：`--split dev --limit 5`。汇报每份合同的成本和延迟，估算跑完整个 dev 的成本。
10. **检查点 4：** 用户同意后，跑完整个 dev（61 份）。
11. 中途按 Ctrl+C 一次，再用 `--resume` 跑完，确认结果条数正确、没有重复（实现路线的验收标准）。可以和第 10 步合并：跑一半时按 Ctrl+C。

---

## 10. 验收标准

来自实现路线第 3 节"阶段 1"：

- [ ] 指标的单元测试通过，用手写的小例子算出已知答案。
- [ ] B0-H 跑完 dev 61 份合同，产出第一张结果表：准确率、3 类 F1、证据精确率和召回率、每份合同的成本。
- [ ] `manifest.json` 里有全部 5 个字段。
- [ ] 中途按 Ctrl+C，再用 `--resume` 重跑，结果条数正确，没有重复。

---

## 11. 交付时要更新的文档

1. [实现路线](ProvenClause_实现路线.md)第 3 节"阶段 1"：勾选验收标准，写一段完成情况。
2. [详细设计](ProvenClause_详细设计.md)第 9.2 节：用实测成本更新估算。test-40 的合同比 dev 平均长，估算时要考虑（见 [notes.md](notes.md) 第 2 节）。
3. [技术决策记录](ProvenClause_技术决策记录.md)：
   - 第 3 节表格里更新 `llm.py`、M7 的状态。
   - **M7 是核心模块。** 按 CLAUDE.md 工作流程第 2 条，加一节"M7 评估（阶段 1 部分）的模块讲解"：它做什么、为什么这样设计、考虑过哪些其他方案、可能有哪些追问。至少讲清楚：为什么"相反"类 F1 用合并计算、为什么 `findings.jsonl` 每行一份合同、`--resume` 怎么保证不重复、`manifest.json` 怎么保证结果可以复现。
   - 第 4.3 节的价格如果是估算值，在第 1.2 节写明。
4. [README](../README.md)：加 `eval run` 的命令和 `.env` 的设置步骤；更新目录。
5. [CLAUDE.md](../CLAUDE.md)：更新"当前进度"和运行命令。
6. 结果表贴进汇报里。`runs/` 不提交。

---

## 12. 交付汇报的格式

1. 做了什么：新建和修改的文件。
2. 验收标准：每一条是否通过，附证据（测试输出、结果表、run_id）。
3. B0-H 在 dev 上的结果表和实际花费。
4. 和本文不同的地方，以及原因（例如 4.1 节核对出的 SDK 写法）。
5. 发现的问题和需要用户决定的事。
6. 提交了没有。没有用户同意，不提交。
