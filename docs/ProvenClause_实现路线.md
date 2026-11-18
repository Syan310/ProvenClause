# ProvenClause 实现路线：从 MVP 到完成体

版本：v1.0（2026-10-21）
依据：[详细设计 v1.0](ProvenClause_详细设计.md)。本文只讲"按什么顺序做、每一步做到什么程度"。模块的具体设计看详细设计。

---

## 0. 总原则

1. **每个阶段结束时，系统都能端到端跑通，并且多出一个新的实验数字。** 不留"做了一半、还跑不起来"的模块。
2. **先定接口，再填实现。** 第 1 阶段定下 4 个核心数据结构和 1 个核心函数。后面的阶段只加字段，不改已有字段。
3. **每个阶段结束都做 3 件事：** 跑 `pytest`；用 DeepSeek Flash 在 dev 上跑一次评估，结果存进 `runs/`；打一个 git tag（`v0.0` 到 `v0.8`）。
4. **开发阶段默认用 Flash。Pro 可以在 dev 上跑参考结果；正式实验只在第 7 阶段。** 2026-10-21 修改：原来是"开发阶段只用 Flash，Pro 只在第 7 阶段跑"。原因是 Pro 只比 Flash 贵 3–4 倍，B0-P 在 dev 上跑一次约 $1。2026-10-21 从 Claude 换成 DeepSeek，见技术决策记录第 3.3 节。配置名的后缀：`-F` 是 Flash，`-P` 是 Pro。
5. **test 只在第 7 阶段使用。** 之前所有调试都只看 dev。

---

## 1. 阶段总览

| 阶段 | 名称 | 天（日期） | 新增模块 | 解锁的配置 | 阶段结束时系统能做什么 | Tag |
|---|---|---|---|---|---|---|
| 0 | 骨架和数据 | 1（10/21） | 数据读取、N2 | — | 读取任意一份合同和标准答案，打印统计 | v0.0 |
| 1 | **MVP：单次审核 + 打分** | 2（10/22） | B0、M7 指标、评估运行器、模型调用封装 | B0-F | 在 dev 上出第一张结果表和实测成本 | v0.1 |
| 2 | 存储底座 | 3（10/23） | M2、M3、N3、N1 规则部分 | — | 结论能入库；非法结论和非法跳转被拦下 | v0.2 |
| 3 | 逐项 Agent | 4–5（10/24–25） | M5、M4、M6 | A1-F | 17 项并发审完一份合同；中断后能恢复 | v0.3 |
| — | 缓冲日 1 | 6（10/26） | 难例分类代码 | — | dev 错误分析；回归测试集的第一批用例 | — |
| 4 | 核对器 | 7–8（10/27–28） | N4（L1、L2、L3） | A2-F | **核心系统完成** | v0.4 |
| 5 | 服务化 | 9（10/29） | M1、N5、PDF 解析 | — | 通过 API 审一份 PDF，拿到报告 | v0.5 |
| 6 | 核对器的可信度 | 10（10/30） | 错误注入、回归门禁 | — | 知道核对器能拦住哪些错误 | v0.6 |
| 7 | 正式实验和分析 | 11–12（10/31–11/01） | 分析脚本 | B0-P、A1-P、A2-P、C5 | 全部结果表和图 | v0.7 |
| 8 | 交付 | 13–14（11/02–03） | README | — | 推送到 GitHub | v0.8 |

### 依赖关系

```
阶段 0 数据 + 检查清单
   │
阶段 1 B0 + 指标 + 评估运行器  ←── MVP：从这里开始每个阶段都有数字
   │
阶段 2 数据库 + 状态机 + 结论账本 + 章节/定义/交叉引用识别
   │
阶段 3 工具 + Agent 循环 + 并发 + 断点恢复        → A1-F
   │
阶段 4 核对器 L1 → L2 → L3 + 重试 + 三种结果      → A2-F
   │
   ├── 阶段 5 API + 报告 + PDF（演示用，不进评估）
   └── 阶段 6 错误注入 + 回归门禁
          │
阶段 7 正式实验（6 组配置 × test-40）+ 分析
   │
阶段 8 README
```

阶段 5 和阶段 6 互相独立。时间紧张时，先做阶段 6，因为它产出实验数字。

---

## 2. 阶段 1 就要定下的接口

后面所有阶段都基于这些接口。先把它们写对，可以避免后面返工。

```python
# src/provenclause/models.py
Label = Literal["Entailment", "Contradiction", "NotMentioned"]
Outcome = Literal["verified", "violated", "abstained"]

class Span(BaseModel):
    span_id: int
    start: int
    end: int
    text: str
    section: str | None = None        # 阶段 2 填
    is_definition: bool = False       # 阶段 2 填
    refs: list[str] = []              # 阶段 2 填
    defines: list[str] = []           # 阶段 2 填：这个句子定义的术语

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
    reason: str | None = None            # abstained 的原因，例如 "refusal"、"budget_exceeded"、"missing"
    self_confidence: int | None = None   # 阶段 3 起由 Agent 填
    outcome: Outcome | None = None       # 阶段 4 起由核对器填
    support_score: int | None = None     # 阶段 4 起由 L3 填
    checks: dict[str, str] = {}          # 阶段 4 起："L1" / "L2" / "L3" → 结果
    stats: AgentStats | None = None      # 阶段 3 起由 A1、A2 填：步数、各工具调用次数等；B0 是 None

class ReviewConfig(BaseModel):
    name: str                            # 例如 "A2-F"
    method: Literal["B0", "A1", "A2"]
    agent_model: str                     # "deepseek-flash" 或 "deepseek-v4-pro"
    verifier_model: str | None = None    # C5 时和 agent_model 不同
    effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"   # 配置文件显式写 high
    max_tokens: int = 16000
    max_steps: int = 8
    max_retries: int = 2
    concurrency: int = 4
    max_review_output_tokens: int = 300_000   # 阶段 3：一次审核累计输出 token 的上限

class Usage(BaseModel):                  # 一次审核的累计用量
    input_tokens: int = 0                # 没有命中缓存的输入 token
    output_tokens: int = 0               # 包括 reasoning_tokens
    cache_read_input_tokens: int = 0     # DeepSeek 的 prompt_cache_hit_tokens
    cache_creation_input_tokens: int = 0 # DeepSeek 不报告、不收费
    reasoning_tokens: int = 0            # output_tokens 的一部分
    n_calls: int = 0
    cost_usd: float = 0.0

class ReviewResult(BaseModel):
    contract_id: str
    findings: list[Finding]
    usage: Usage
    latency_s: float
```

**核心函数：**

```python
async def review_contract(contract: Contract, checklist: Checklist, config: ReviewConfig, llm: LLM,
                          *, conn: sqlite3.Connection | None = None) -> ReviewResult
```

- `llm` 是 `llm.py` 的 `LLM` 协议：真实调用用 `DeepSeekLLM`，测试用 `FakeLLM`（见技术决策记录 F6）。
- 2026-10-21 修改：返回值从 `list[Finding]` 改成 `ReviewResult`，`Finding` 加 `reason`，`ReviewConfig` 加 `max_tokens`（见技术决策记录 F5）。
- 2026-10-25 修改：加只能按名字传的参数 `conn`，A1、A2 把审核写进这个数据库，`None` 时用 `:memory:`；B0 不用它（见技术决策记录第 3.6 节 D2）。`Finding` 加 `stats`，`ReviewConfig` 加 `max_review_output_tokens`（C4、C7）。

- B0、A1、A2 都实现这个函数，用 `config.method` 区分。
- 评估脚本和 API 都只调用这个函数。这样 6 组配置的评估代码完全相同。
- 每组配置是一个 YAML 文件：`configs/b0-f.yaml`、`configs/b0-p.yaml`、`configs/a2-p.yaml` 等。

---

## 3. 各阶段的任务和验收标准

### 阶段 0：骨架和数据（第 1 天，10/21）

**目标：** 能读取任意一份合同和它的标准答案。

**任务：**
1. 用 `uv init` 建项目，按详细设计第 10.2 节建目录。`.gitignore` 加入 `data/`、`runs/`、`.env`。
2. 下载 ContractNLI 官方数据包，放到 `data/contract-nli/`。不要用 Hugging Face 上的句子级版本。
3. 写 `parse.py` 的 `load_contractnli(split) -> list[Contract]`。直接用数据包里的 `spans`，不重新切句。
4. 写 `eval/dataset.py` 的 `load_gold(split)`，返回 `{(contract_id, item_id): (label, span_ids)}`。
5. 写 `checklists/nda_17.yaml` 和 `checklist.py`（Pydantic 校验）。`guidance` 用自己的话写。论文附录没有公开标注说明，所以根据检查问题原文和 train 里的证据例子写（见 [notes.md](notes.md) 第 1.3 节）。
6. 用固定随机种子从 test 抽 40 份，写入 `eval/splits/test40.txt`，提交到 Git。
7. 打开原论文，核对基线数字（BERT-large 准确率、"相反"类 F1）和评估设置。记进 `docs/notes.md`。
8. 手动读 5 份 NDA，记下例外条款、定义和交叉引用的写法。阶段 2 的正则表达式要用这些笔记。

**验收标准：**
- [x] 测试：读出 607 份合同，划分为 423 / 61 / 123。
- [x] 测试：每个 span 都满足 `span.text == contract.text[span.start:span.end]`。
- [x] 测试：test 的结论分布是支持 968、没提到 903、相反 220。
- [x] 重新运行抽样脚本，`test40.txt` 的内容不变。

**完成情况（2026-10-21）：** 4 项全部通过。`test40.txt` 在 `src/provenclause/eval/splits/`，用 `uv run python -m provenclause.eval.sample --check` 检查。论文核对和 5 份 NDA 的笔记在 [notes.md](notes.md)。

---

### 阶段 1：MVP —— 单次审核 + 打分（第 2 天，10/22）

**MVP 的定义：** 输入一份合同，输出 17 条带证据句子编号的结论，然后用程序和标准答案比较打分。这就是 B0 基线。

**为什么 MVP 是 B0：**
- 它是最短的闭环：合同 → 结论 → 分数。
- 后面每加一个模块，都和它比较，看提升了多少。
- 第 2 天结束时，你已经有一个可以写进 README 的数字。

**任务：**
1. **先讨论这个阶段各模块的技术选择**，结论记进[技术决策记录](ProvenClause_技术决策记录.md)。DeepSeek API 的约束见该文件第 1.2 节。写代码前查 DeepSeek 官方文档（api-docs.deepseek.com），再确认一次。
2. 写 `llm.py`：唯一调用模型的地方。`DeepSeekLLM` 用 `openai` SDK 调用 DeepSeek 的 OpenAI 兼容 API。它负责创建客户端、设置 `reasoning_effort`、检查 `finish_reason`、记录 token 用量并换算成美元。B0、Agent 和 L3 都通过它调用模型。
3. 在 `parse.py` 写 `format_contract(contract)`，输出 `[S23] Recipient may disclose ...` 这样的带编号全文。B0、A1、A2 共用这个函数。
4. 写 `baseline.py`（B0）：一次调用，返回 17 条 `Finding`。用 JSON 模式输出，schema 写在提示词里，再用 Pydantic 校验。校验失败时，结果为 `abstained`，原因为 `parse_error`。
5. 写 `eval/metrics.py`：准确率、各类 F1、证据精确率和召回率、已核实精确率、覆盖率、成本、P50 / P95 延迟。
6. 写 `eval/run.py`：
   ```bash
   uv run python -m provenclause.eval run --config configs/b0-f.yaml --split dev --limit 5
   ```
   - 结果写入 `runs/<run_id>/`：`metrics.json`、`findings.jsonl`、`manifest.json`（git commit、模型、effort、提示词哈希、随机种子）。
   - **现在就加 `--resume`：** 已经跑完的合同从 `findings.jsonl` 读取，不重新调用模型。第 7 阶段跑 Pro 时，中途出错不会浪费钱。
7. 先跑 5 份合同，确认成本。然后跑完整 dev，更新详细设计第 9.2 节的成本估算。

**验收标准：**
- [x] 指标的单元测试通过。用手写的小例子算出已知答案。
- [x] B0-F 跑完 dev 61 份合同，产出第一张结果表：准确率、3 类 F1、证据精确率和召回率、每份合同的成本。
- [x] `manifest.json` 里有全部 5 个字段。
- [x] 中途按 Ctrl+C，再用 `--resume` 重跑，结果条数正确，没有重复。

**完成情况（2026-10-21）：** 4 项全部通过。模型在施工时换成了 DeepSeek（见技术决策记录第 3.3 节）。

1. `uv run pytest`：75 个离线测试通过。`tests/test_metrics.py` 用 2 份手写合同核对全部指标。`uv run pytest -m live` 用真实的 Flash 审核合同 456：17 条结论，$0.0017，9.6 秒。
2. B0-F 在 dev 上的结果（run `b0-f_dev_20261021-154250`）：

   | 配置 | 准确率 | F1 支持 | F1 相反 | F1 没提到 | F1 相反（论文式） | 证据精确率 | 证据召回率 | 已核实精确率 | 覆盖率 | 每份合同 | P50 | P95 |
   |---|---|---|---|---|---|---|---|---|---|---|---|---|
   | B0-F | 0.804 | 0.877 | 0.574 | 0.777 | 0.420 | 0.875 | 0.552 | 0.804 | 1.000 | $0.0052 | 34.4 秒 | 55.8 秒 |

   - 总成本 $0.32。1,037 条结论里没有弃权，没有引用不存在的 `span_id`。
   - "相反"类精确率 0.454、召回率 0.779：模型判"相反"偏多。错误接受 203 条。
   - 论文式 F1 用了 8 个检查问题，其余 9 个在 dev 上没有标准"相反"。
3. `manifest.json` 有 `git_commit`、`model`、`effort`、`prompt_hash`（`50fb0745f4aa`）、`seed`。这次运行时阶段 1 的代码还没提交，所以 `git_dirty` 是 `true`。
4. 跑到第 18 份合同时按 Ctrl+C。程序打印提示后退出，`findings.jsonl` 有 18 行完整的结果。`--resume` 只审核了剩下的 43 份。最后 61 行，61 个不同的 `contract_id`，和 `manifest.json` 的合同列表一致。

**B0-P 的 dev 参考结果（2026-10-21）：** 用 Pro 跑同一个 B0（`prompt_hash` 相同：`50fb0745f4aa`），和 B0-F 比较。这是参考结果，正式数字在阶段 7 的 test-40 上测。run `b0-p_dev_20261021-162737`，`git_commit` `ba1e2d6`，`git_dirty` 是 `false`。全部在非高峰时段，没有出错，没有续跑。

| 配置 | 准确率 | F1 支持 | F1 相反 | F1 没提到 | F1 相反（论文式） | 证据精确率 | 证据召回率 | 已核实精确率 | 覆盖率 | 每份合同 | P50 | P95 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| B0-F | 0.804 | 0.877 | 0.574 | 0.777 | 0.420 | 0.875 | 0.552 | 0.804 | 1.000 | $0.0052 | 34.4 秒 | 55.8 秒 |
| B0-P | 0.809 | 0.890 | 0.579 | 0.772 | 0.457 | 0.893 | 0.568 | 0.809 | 1.000 | $0.0189 | 56.0 秒 | 106.1 秒 |

1. **准确率差别不显著。** Pro 减 Flash 是 +0.0048，95% 置信区间 [−0.0087, +0.0174]。算法是按合同的配对 bootstrap：1,037 条结论，重抽样 10,000 次，种子 20261021。
2. **两个模型错在同一批条目上。** 1,037 条里，两个都对 815 条，只有 Flash 对 19 条，只有 Pro 对 24 条，两个都错 179 条。两个都错的 179 条里，175 条给出相同的错误答案。
3. **主要错误相同：把"没提到"判成"支持"或"相反"。** Flash 139 条，Pro 146 条，占标准"没提到"（423 条）的 33% 和 35%。"相反"类的精确率都只有 0.45，召回率是 0.78 和 0.81。

   | 标准答案 \ 模型答案 | Flash：支持 | Flash：相反 | Flash：没提到 | Pro：支持 | Pro：相反 | Pro：没提到 |
   |---|---|---|---|---|---|---|
   | 支持（519） | 476 | 20 | 23 | 485 | 17 | 17 |
   | 相反（95） | 20 | 74 | 1 | 17 | 77 | 1 |
   | 没提到（423） | 70 | 69 | 284 | 69 | 77 | 277 |

   两个模型都没有"没有回答"的条目。
4. **难的检查项相同。** 两个模型准确率都低于 0.55 的检查项：nda-1（0.393 / 0.344）、nda-16（0.459 / 0.475）、nda-20（0.508 / 0.492）。nda-8、nda-11、nda-12、nda-13 两个模型都是 1.000。差别最大的是 nda-7 和 nda-10，Pro 都高 0.066。完整的逐项表在本次分析的输出里，没有放进文档。
5. **成本和速度。** Pro 的输出 token 是 Flash 的 1.06 倍（每份合同平均 8,248 对 7,787），思考 token 是 1.04 倍（7,257 对 7,012）。总成本 $1.152，是 Flash 的 3.60 倍。P50 延迟是 Flash 的 1.6 倍。

**结论：** 在 B0 上，贵 3.6 倍的 Pro 没有带来可测量的提升。两个模型的错误高度重合，主要集中在"没提到"的判断上。一个假设：B0 的瓶颈不在模型大小，而在任务做法（一次审 17 项、没有核对）、检查项的 `guidance`，或者标准答案的口径。阶段 3 的 A1 和缓冲日的错误分析检查这个假设。按项目规则，B0 的提示词不因为这个结果修改。

---

### 阶段 2：存储底座（第 3 天，10/23）

**目标：** Agent 需要的数据结构全部就位。结论可以入库，不合法的结论在入库时被拦下。

**选型（2026-10-23）：** 见技术决策记录第 3.5 节。施工细节见[阶段 2 施工文档](ProvenClause_阶段2施工.md)。和下面任务不同的 2 处：数据库是 6 张表（加 `review_items`，存检查项的状态）；阶段结束时不跑 dev 评估，因为阶段 2 不改 B0，阶段 2 的数字是 N1 的统计。

**任务：**
1. `db.py`：建 5 张表（详细设计 M2）。打开 WAL 模式。`findings.operation_id` 加唯一索引。2026-10-23 修改：加第 6 张表 `review_items`。
2. `fsm.py`：审核级和检查项级两张跳转表。`transition()` 检查跳转是否合法，不合法就抛出错误；合法就写一条 `events`。
3. `ledger.py`：`record_finding()` 执行详细设计 N3 的 2 条登记规则，用 `operation_id` 保证重复写入只有 1 条。
4. `parse.py` 的规则部分（N1）：
   - 章节识别：`1.`、`1.1`、`Section 5`、`Article III`。
   - 定义识别：`"Confidential Information" means ...`、`(the "Agreement")`。
   - 交叉引用识别：`Section 5`、`paragraph 3(b)`。
   - 把结果填进 `Span.section`、`Span.is_definition`、`Span.refs`。

> **注意：** 详细设计的 14 天计划没有给 N1 的章节、定义和交叉引用识别排时间。但第 4 天的 `lookup_definition`、`get_section` 工具和第 7 天的 L2 都依赖它。所以本路线把它放在第 3 天。

**验收标准：**
- [x] 测试：非法跳转抛出错误；每次合法跳转写 1 条 `events`。（`tests/test_fsm.py`：两张表的每一条合法边，和审核级的每一条非法边）
- [x] 测试：同一个 `operation_id` 写 2 次，表里只有 1 条。（`tests/test_ledger.py`）
- [x] 测试：结论为"支持"但 `span_ids` 为空，返回错误；结论为"没提到"但 `span_ids` 不为空，返回错误；`span_id` 不存在，返回错误。（`tests/test_ledger.py`）
- [x] 测试：章节、定义、交叉引用的正则表达式，每种写法至少 1 个用例。（`tests/test_parse_rules.py`）
- [x] 数字：dev 上有章节号的 span 占多少；每份合同平均识别出多少个定义。用阶段 0 读过的 5 份合同人工抽查。（见下面的完成情况）

**完成情况（2026-10-24）：** 新建 `db.py`、`fsm.py`、`ledger.py`，`parse.py` 加了规则部分，`stats.py` 加了 `--n1`。离线测试 250 个，全部通过（阶段 0、1 的 75 个不变；审核后修了 2 处引用规则，加了 7 个用例，见技术决策记录第 3.5 节第 12、13 条）。B0 的 `prompt_hash` 仍然是 `50fb0745f4aa`。阶段 2 没有调用模型。

N1 的统计（`uv run python -m provenclause.eval.stats --n1 --split dev`）：

| 数字 | dev（61 份） | train（423 份，参考） |
|---|---|---|
| 有章节号的句子 | 4,296 / 5,102 = 84.2% | 80.5% |
| 至少有 1 个章节号的合同 | 58 | 382 |
| 每份合同的定义术语数（去重） | 平均 7.3，最多 23；0 个的合同 3 份 | 平均 6.2，最多 50；0 个的 28 份 |
| 每份合同写进 `refs` 的引用数 | 平均 1.90，共 116 条 | 平均 1.70 |
| 丢掉的引用（`find_refs()` 找到，`section_spans()` 找不到） | 2 / 118 | 62 / 781 |

5 份合同的人工抽查（对照 [notes.md](notes.md) 第 3 节）：

| 合同 | 章节 | 定义术语数（notes 的人工数，误差约 ±2） | 引用 |
|---|---|---|---|
| 456 | `1`–`5`；`B. GENERAL` 不是章节 | 0（0） | 无 |
| 411 | `1`–`7` | 12（12），包括反向定义 S18 | `Section 2(a) hereof` → `2`；`Section 12 of the Securities Exchange Act` 被排除 |
| 3 | `1`–`16`；`a)` 不记 | 11（11） | 无 |
| 563 | `1`–`22` | 9（9）；`Receiving Party` 查不到，返回空列表 | `Section 3` → `3` |
| 64 | `1`–`8`；`A.`–`M.` 是小节，不记 | 23（24） | `Paragraph 6K above` → `6` |

dev 上没有章节号的 3 份合同（405、507、595）用不带编号的大写标题。规则不识别这种标题，详见技术决策记录第 3.5 节"施工时的规则调整"。

---

### 阶段 3：逐项 Agent（第 4–5 天，10/24–25）

**目标：** 解锁 A1-F。回答"逐项审核带来多少提升"。

**选型（2026-10-25）：** 见技术决策记录第 3.6 节。施工细节见[阶段 3 施工文档](ProvenClause_阶段3施工.md)。和下面任务不同的地方：
- 不开 strict 模式；`tool_choice` 用默认的 `auto`（思考模式下 `required` 返回 400）。模型只回文字时，追加一条提醒。
- `reasoning_content` 的做法：把模型的回复原样追加回对话。
- 输出被截断时，用 2 倍的 `max_tokens` 重做 1 次，再放弃。B0 也用这条规则。
- 每次审核的输出 token 上限是 `ReviewConfig.max_review_output_tokens`（默认 300,000；A1-F 的配置在 5 份合同试跑以后改成 100,000）。
- 评估时每次运行一个数据库 `runs/<run_id>/reviews.db`；`--resume` 时做到一半的合同整份重做。
- 日程：选型在 10/25 完成，施工占用 10/26 的缓冲日。

**第 4 天：工具 + 单项循环**
1. `tools.py`：5 个工具（详细设计 M5）。每个工具有 JSON Schema、执行函数和 10 秒超时。不打开 DeepSeek 的 strict 模式（技术决策记录第 3.6 节 A2）。
   - `search_spans` 用 `rank-bm25`。每份合同只建 1 次索引，放在内存里。
   - 工具结果写入 `events` 之前，先用正则表达式去掉邮箱、电话号码和身份证号。
2. `agent.py` 的单项循环：调用模型 → 执行工具 → 结果交回模型，直到 `record_finding` 成功。
   - 最多 8 步。超出就结果为 `abstained`，原因为 `budget_exceeded`。
   - `tool_choice` 用默认的 `auto`（思考模式下 `required` 返回 400）。模型只回文字时，追加一条提醒。
   - 每一轮都把模型返回的 `reasoning_content` 交回去。用工具时省略它，DeepSeek 返回 400。做法：把模型的回复原样追加回对话。
   - 合同全文放在 system 提示词里，作为 17 项共用的前缀。DeepSeek 自动缓存共用前缀，不需要 `cache_control`。检查问题和 `guidance` 放在 user 消息里。
3. 写一个 `FakeLLM`：按脚本返回预先写好的模型响应。阶段 3 到阶段 6 的单元测试都用它，不花钱、结果固定。

**第 5 天：17 项并发 + 断点恢复**
1. 用 `asyncio.Semaphore(4)` 并发跑 17 项。每次审核设置 token 上限。
2. **缓存预热：** 先单独跑第 1 项，再并发跑剩下的 16 项。同时发出的前几个请求都还没有缓存可读。
3. 检查第 2 项以后的 `usage.cache_read_input_tokens`（DeepSeek 的 `prompt_cache_hit_tokens`）。如果它是 0，检查缓存前缀里有没有每次都变的内容，例如时间戳。
4. `recovery.py`：按详细设计 M6 实现。
5. A1-F 在 dev 上跑。

**验收标准：**
- [x] 测试（FakeLLM）：一份合同的 17 项全部结束，`findings` 里没有重复结论。（`tests/test_agent.py::test_seventeen_items`）
- [x] 测试（FakeLLM）：超过 8 步，结果为 `abstained`、原因为 `budget_exceeded`。（`test_over_max_steps_abstains`）
- [x] 测试：含有假邮箱和假电话号码的工具结果，写入 `events` 后被替换。（`test_events_are_redacted`）
- [x] 测试（FakeLLM）：跑完 5 项后抛出异常模拟崩溃；调用恢复函数后，17 项完整，没有重复。（`tests/test_recovery.py::test_crash_after_five_items`）
- [x] 数字：A1-F 和 B0-F 在 dev 上的对比表。增加平均步数、各工具的调用次数、缓存命中率。（见下面的完成情况）

**完成情况（2026-10-26）：** 新建 `tools.py`、`agent.py`、`recovery.py` 和 A1 的 2 个配置，`llm.py` 加了 `create()` 和截断后加倍重做。离线测试 355 个，全部通过（阶段 0–2 的 250 个仍然通过）。B0 的 `prompt_hash` 仍然是 `50fb0745f4aa`。A1 的 `prompt_hash` 是 `39fa1266b5e6`。施工时改了的规则见技术决策记录第 3.6 节"施工时的规则调整"。

1. **试跑（5 份 dev 合同）：** run `a1-f_dev_20261025-201026`，$0.1136（高峰价）。没有出错，第 2–17 项的输入有约 90% 命中缓存，一份合同的输出最多 16,065 token。按这个结果，`max_review_output_tokens` 改成 100,000。另外试了一条提示词规则（"合同没写某个要求不是 Contradiction"，run `a1-f_dev_20261025-202007`），准确率不变，已撤掉。
2. **A1-F 在 dev 上的结果**（run `a1-f_dev_20261026-003459`，61 份，`git_commit` `4c297f2`，`git_dirty` 是 `true`：阶段 3 的代码还没提交）。B0-F 是阶段 1 的 run `b0-f_dev_20261021-154250`。

   | 配置 | 准确率 | F1 支持 | F1 相反 | F1 没提到 | F1 相反（论文式） | 证据精确率 | 证据召回率 | 已核实精确率 | 覆盖率 | 每份合同 | P50 | P95 |
   |---|---|---|---|---|---|---|---|---|---|---|---|---|
   | B0-F | 0.804 | 0.877 | 0.574 | 0.777 | 0.420 | 0.875 | 0.552 | 0.804 | 1.000 | $0.0052 | 34.4 秒 | 55.8 秒 |
   | A1-F | 0.795 | 0.877 | 0.571 | 0.750 | 0.434 | 0.789 | 0.708 | 0.795 | 1.000 | $0.0193（高峰价；非高峰 $0.0097） | 26.1 秒 | 55.1 秒 |

   | Agent 的数字（A1-F） | 数值 |
   |---|---|
   | 模型调用 | 2,259 次，每份合同 37.0 次；每项平均 2.18 步，最多 6 步 |
   | 每项的工具调用 | `search_spans` 1.46，`read_spans` 0.36，`lookup_definition` 0.33，`get_section` 0.20，`record_finding` 1.00 |
   | 提醒、截断重做、工具错误、被拒绝的登记 | 都是 0 |
   | 放弃（`abstained`） | 0 条。没有项碰到步数上限或总预算 |
   | 缓存命中率（命中 / 全部输入） | 全部 0.862；第 1 项 0.552；第 2–17 项 0.880。B0-F 是 0.203 |
   | 输出 token | 每份合同平均 9,349（B0-F 7,787），最多 19,436（合同 82）；单次调用最多 5,440。思考 token 占 54%（B0-F 90%） |
   | 成本 | 全部在高峰时段跑（周一 07:35–07:43 UTC），总计 $1.177；非高峰价约 $0.589，是 B0-F 的 1.84 倍。非高峰价的构成：输出 58%，未命中缓存的输入 37%，命中缓存的输入 5% |
   | `reviews.db` | 14.6 MB |

3. **准确率差别不显著。** A1 减 B0 是 −0.0096，95% 置信区间 [−0.0241, +0.0039]。算法同阶段 1：按合同的配对 bootstrap，重抽样 10,000 次，种子 20261021。1,037 条里，两个都对 801 条，只有 A1 对 23 条，只有 B0 对 33 条，两个都错 180 条（其中 170 条给出相同的错误答案）。
4. **证据召回率明显提高，精确率下降。** 召回率 0.552 → 0.708，精确率 0.875 → 0.789：A1 引用的句子更多。
5. **"没提到"的错误变多。** 标准"没提到"（423 条）被判成"支持"或"相反"：B0-F 139 条，A1-F 163 条。"支持"的条目更准：被判成"没提到"的从 23 条降到 9 条。

   | 标准答案 \ 模型答案 | B0-F：支持 | B0-F：相反 | B0-F：没提到 | A1-F：支持 | A1-F：相反 | A1-F：没提到 |
   |---|---|---|---|---|---|---|
   | 支持（519） | 476 | 20 | 23 | 486 | 24 | 9 |
   | 相反（95） | 20 | 74 | 1 | 16 | 78 | 1 |
   | 没提到（423） | 70 | 69 | 284 | 87 | 76 | 260 |

6. **B0 最弱的 3 项没有变好。**
   - nda-1（必须明确标注）：0.393 → 0.311。错误几乎都是判成"相反"（B0 36 条，A1 42 条）。模型把"定义按内容列举信息、没有要求标注"当成和"必须标注"相反。
   - nda-16（终止时归还或销毁）：0.459 → 0.459。两个都有 30 条"没提到"被判成"支持"。
   - nda-20（销毁后可以保留一部分）：0.508 → 0.525。两个都有 22 条"没提到"被判成"相反"。
   - 变差最多的是 nda-17（允许复制）：0.705 → 0.574，"没提到"被判成"支持"从 10 条变成 18 条。变好最多的是 nda-7、nda-10，各 +0.049。

**结论：** 在 dev 上，逐项审核和工具没有提高准确率。A1-F 的准确率和 B0-F 没有显著差别，证据召回率提高，成本是 1.84 倍（非高峰价），P50 延迟反而更短（4 项并发）。两个方法错在同一批条目上，主要错误仍然是"没提到"的判断，A1 在这一点上更差：它更多地从相关条款推出"支持"或"相反"。这支持阶段 1 的假设：瓶颈不在"一次审 17 项"，而在检查项的口径（`guidance`）和标准答案对"没提到"的定义。阶段 4 的核对器（L2 规则、L3 复核）针对的正是这类结论。错误分析在缓冲日做。

---

### 缓冲日 1（第 6 天，10/26）

1. 把 A1-F 在 dev 上的错误归入详细设计第 8.4 节的 7 类，写进 `docs/error_analysis_dev.md`。
2. 写 `eval/hard_cases.py`：按详细设计第 8.3 节挑出 H1–H5。它只用标准答案和合同，不调用模型，现在就可以写。
3. 把典型错误（特别是漏看例外条款的案例）加进 `eval/regression.jsonl`。
4. 如果阶段 3 有没做完的任务，在今天补完。

---

### 阶段 4：核对器（第 7–8 天，10/27–28）

**目标：** 解锁 A2-F。**这个阶段结束时，核心系统完成。**

**选型时要比较的 L3 方案（2026-10-26 用户决定）：** 下面第 8 天的任务按"L3 单次调用"写。阶段 4 选型时，另外讨论"L3 做成带只读工具的 Agent"（见技术决策记录第 3 节 N4 一行），两个方案一起比较，由用户决定。

**第 7 天：L1 + L2（纯程序）**
1. `verify.py` 里写 2 个纯函数，不调用模型：
   - `l1_check(finding, contract) -> CheckResult`
   - `l2_context(finding, contract, item) -> L2Context`（标记 + 要交给 L3 的额外 span）
2. 单元测试用手写的小合同：一句带 `except`、一句定义、一句 `Section 5` 引用。
3. 数字：在 A1-F 的 dev 结论上运行 L1 和 L2。统计 L1 不通过的条数，以及 `has_exception` 等标记各出现多少次。

**第 8 天：L3 + 重试 + 三种结果**
1. `l3_review(item, label, span_ids, l2_context) -> L3Result`。和 B0 一样用 JSON 模式 + Pydantic 校验。
   - **函数参数里没有 Agent 的对话历史。** 这样从代码上保证核对器看不到推理过程。
2. 重试：L1 或 L3 不通过时，把具体问题作为 user 消息交回 Agent 的对话，最多 2 次。
3. 按详细设计 N4 的规则给出 `verified`、`violated`、`abstained`。
4. A2-F 在 dev 上跑。如果覆盖率太低，只在 dev 上调 L3 的提示词。

**验收标准：**
- [ ] L1 和 L2 的单元测试通过。
- [ ] 测试（FakeLLM）：L3 第 1 次返回 `fail`、第 2 次返回 `pass`，结果为 `verified`，`attempt` 为 2。
- [ ] 测试（FakeLLM）：L3 连续 3 次返回 `fail`，结果为 `violated`。
- [ ] 数字：A2-F 和 A1-F 在 dev 上的对比。加上已核实精确率、覆盖率、拦截率和误拦率。
- [ ] 打 tag `v0.4`。从这里开始，第 7 阶段之前不改 Agent 和核对器的核心逻辑，只调提示词。

---

### 阶段 5：服务化（第 9 天，10/29）

**目标：** 证明系统是一个服务。这个阶段只用于演示，不进入评估。

**任务：**
1. `report.py`（N5）：输出 JSON 和 Markdown。先列"需人工确认"的项，再列高风险项，最后列其他项。首页写免责声明。
2. `api.py`（M1）：3 个接口，用 FastAPI 的 `BackgroundTasks` 启动审核。服务启动时调用 `recovery.py`。
3. `parse.py` 的 PDF 部分：PyMuPDF 提取文字 → 按句子和列表项切成 span → 走阶段 2 的章节和定义识别。
4. 真实的断点恢复测试：
   1. 启动 `uvicorn`，调用 `POST /reviews`。
   2. 审到一半时强制结束进程。在 Windows 上用 `taskkill /F /PID <pid>`。
   3. 重启服务。
   4. 检查报告是否有 17 项、有没有重复的结论。

> **注意：** 详细设计的 14 天计划没有给 N5 报告排时间。`GET /reviews/{id}/report` 依赖它，所以本路线把它放在第 9 天。

**验收标准：**
- [ ] 用 `curl` 走完"创建 → 查进度 → 拿报告"的流程，数据集合同和演示 PDF 各 1 次。
- [ ] 真实进程的断点恢复测试通过。
- [ ] 演示 PDF 的 Markdown 报告存进 `docs/demo_report.md`，README 要用它。

---

### 阶段 6：核对器的可信度（第 10 天，10/30）

**目标：** 用数字说明核对器能拦住哪些错误。

**任务：**
1. `eval/inject.py`：从 dev 上 A2-F 判对的结论生成 E1–E4 四种坏结论，共约 40 条。再混入 40 条正确结论。
2. 只跑核对器（L1 → L2 → L3），不跑 Agent。这样成本很低，结果只反映核对器。
3. `eval/gate.py`：
   ```bash
   uv run python -m provenclause.eval gate --baseline runs/<基准 run_id>
   ```
   - 用 Flash 在 `eval/regression.jsonl` 上跑一次，和基准结果比较。
   - 已核实精确率下降超过 2 个百分点，或者"相反"类 F1 下降超过 3 个百分点，就返回非 0 的退出码。

**验收标准：**
- [ ] 错误注入结果表：每种错误的拦截率，以及正确结论的误拦率。
- [ ] 故意改坏一次提示词，`eval gate` 返回非 0 的退出码。

---

### 阶段 7：正式实验和分析（第 11–12 天，10/31–11/01）

**第 11 天开始前：**
1. 打 tag `v0.7`。记下提示词哈希。从这里开始不改任何代码和提示词。
2. 每组配置先在 2 份 dev 合同上试跑。确认 Flash 和 Pro 的请求都没有 400 错误，成本和估算一致。

**第 11 天：跑实验**
1. 先跑便宜的配置：B0-F、A1-F、A2-F。
2. 再跑贵的配置：B0-P、A1-P、A2-P。出错时用 `--resume` 继续。
3. 补充实验：A2-F 跑 test 全集。可选：C5。

**第 12 天：分析**
1. `eval/analyze.py` 读取 `runs/` 里的结果，生成：
   - 主表（详细设计第 7.3 节）
   - 成本-质量图
   - 风险-覆盖曲线（A2-F 和 A2-P 各一张，每张 2 条曲线）
   - 难例表（H1–H5 × 6 组配置）
   - 错误分类表
2. 图表存进 `docs/figures/`，README 要用它们。

**验收标准：**
- [ ] 6 组核心配置在 test-40 上都有 `metrics.json`，每组 680 条结论。
- [ ] 所有图表都能用 1 条命令从 `runs/` 重新生成。

---

### 阶段 8：交付（第 13–14 天，11/02–03）

1. README：架构图、证据示例、主表、图表、错误分析、相关工作、数据来源（ContractNLI，CC BY 4.0）。
2. 推送到 GitHub。打 tag `v0.8`。

---

## 4. 动手前要先定的 5 个决策

读详细设计时发现以下 5 处没有写清楚。每一处都给出建议，你确认后再开始对应的阶段。#1、#4、#5 已在 2026-10-21 按建议确认（见技术决策记录第 3.2 节）；#2、#3 在阶段 3、4 确认。

| # | 问题 | 影响哪个阶段 | 建议 |
|---|---|---|---|
| 1 | B0 和 A1 没有核对器，主表里它们的"已核实精确率"怎么算？ | 阶段 1 | 没有核对器时，所有结论都算 `verified`。覆盖率为 100%，已核实精确率等于准确率。在主表的脚注里写明 |
| 2 | N4 说 verified 要求"L2 没有未处理的标记"。什么算"已处理"？ | 阶段 4 | L2 的上下文已经交给 L3，并且 L3 返回 `pass`，就算已处理。这样 verified 的条件就是"L1 通过且 L3 通过" |
| 3 | 断点恢复时 `attempt + 1`。这一次算不算进"最多重试 2 次"？ | 阶段 3 | 不算。恢复后这一项重新开始，重试次数清零。`attempt` 只用来生成新的 `operation_id` |
| 4 | B0 一次输出 17 条结论。其中一条的 `span_id` 不存在时怎么办？ | 阶段 1 | 保留原样，照常打分。B0 没有 L1，这正是要测量的错误。另外统计"不存在的 span_id"条数 |
| 5 | 设计里没有 `models.py`、`llm.py` 和 `configs/` | 阶段 1 | 加上这 3 个。它们让 B0、A1、A2 共用同一套数据结构、模型调用和配置格式 |

---

## 5. 进度落后时怎么砍

按详细设计第 11 节的顺序砍，对应到本路线的阶段：

| 顺序 | 砍掉什么 | 属于哪个阶段 |
|---|---|---|
| 1 | 可选配置 C5 | 阶段 7 |
| 2 | A2-F 在 test 全集上的补充实验 | 阶段 7 |
| 3 | 演示 PDF 的解析（API 只支持数据集里的合同） | 阶段 5 |
| 4 | 错误注入缩到 E2 和 E4 | 阶段 6 |
| 5 | 正式实验缩到 test-20 | 阶段 7 |

**不要砍：** 阶段 1–4 的全部内容、6 组核心配置、风险-覆盖曲线和难例分析。

**判断是否落后的检查点：**
- 第 2 天结束时没有 B0-F 的结果表 → 第 6 天的缓冲日用来追进度。
- 第 8 天结束时 A2-F 跑不通 → 阶段 5 只做 API 和报告，不做 PDF。
- 第 10 天结束时阶段 6 没做完 → 错误注入缩到 E2 和 E4，第 11 天照常跑正式实验。
