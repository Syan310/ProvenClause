# ProvenClause 技术决策记录

**用法：** 开始写一个阶段之前，先讨论这个阶段各模块用什么技术。讨论时遵守第 2 节的选型方式。地基决策的结论记在第 2.3 节，模块的结论记在第 3 节。没有讨论过的模块，保持"待讨论"。

总的技术栈见[详细设计](ProvenClause_详细设计.md)第 10.1 节。本文只记录每个模块具体的选择。

---

## 1. 已核实的事实（不是选择）

下面这些是 2026-10-21 查到的事实。讨论模块时可以拿来参考。

### 1.1 环境和库

| 事实 | 来源 |
|---|---|
| 本机 Python 是 3.13.7，uv 是 0.10.11 | 本机命令 |
| `openai` Python SDK：2026-10-21 用 uv 安装的最新版是 3.36.0 | uv |
| PyMuPDF 的许可证是 AGPL-3.0 | PyMuPDF 官方 |
| `rank-bm25` 最新版是 0.2.2（2022-02-16 发布，以后没有更新），依赖 numpy。`bm25s` 最新版是 0.3.15（2026-10-22），可选依赖多 | PyPI，2026-10-25 |

### 1.2 DeepSeek API 的约束

2026-10-21 从 Claude 换成 DeepSeek（见第 3.3 节）。下表来自 [DeepSeek API 文档](https://api-docs.deepseek.com/)，2026-10-21 核对。

| 约束 | 影响哪些模块 |
|---|---|
| 模型 ID：`deepseek-flash`（DeepSeek-V4.1-Flash）、`deepseek-v4-pro`（DeepSeek-V4-Pro）。上下文 100 万 token，最大输出 384K token | 全部调用模型的模块 |
| 原生接口兼容 OpenAI 格式，`base_url` 是 `https://api.deepseek.com`。另有 Anthropic 兼容接口，但它的 `output_config` 只支持 `effort`，没有结构化输出 | `llm.py` |
| 价格（每百万 token，非高峰 / 高峰）：Flash 输入（未命中缓存）$0.15 / $0.30、命中缓存 $0.003 / $0.006、输出 $0.60 / $1.20；Pro 输入 $0.66 / $1.32、命中缓存 $0.022 / $0.044、输出 $1.98 / $3.96。没有缓存写入费 | 评估的成本计算 |
| 高峰时段：UTC 周一到周五 01:00–04:00 和 06:00–10:00，中国法定节假日除外。`llm.py` 按调用时间选价格，不处理节假日，所以节假日的成本会高估 | 评估的成本计算 |
| 两个模型默认打开思考。思考内容在 `message.reasoning_content` 里，和 `content` 分开 | B0、M4、L3 |
| `reasoning_effort` 接受 `low`、`high`、`max`；`medium` 和 `xhigh` 被当成 `high`。默认值是 `high` | B0、M4、L3 |
| 思考也占用 `max_tokens`。思考模式下 `max_tokens` 默认 64K | B0、M4、L3 |
| 思考模式下 `temperature` 不报错，但没有效果。模型输出不能通过参数固定 | M7 的实验设计 |
| 没有按 Schema 约束的结构化输出。JSON 模式（`response_format={"type": "json_object"}`）保证返回合法的 JSON，但不保证符合 Schema。提示词里必须有 "json" 这个词。文档说 JSON 模式偶尔返回空内容 | B0、L3 |
| `finish_reason`：`stop`、`length`、`content_filter`、`tool_calls`、`insufficient_system_resource`（服务器资源不足，中断请求）、`aborted` | `llm.py` |
| 缓存自动打开，按前缀匹配，没有 `cache_control`。用量里 `prompt_cache_hit_tokens` 和 `prompt_cache_miss_tokens` 相加等于 `prompt_tokens` | 评估的成本计算、M4 |
| 带工具的多轮对话要把前面每一轮的 `reasoning_content` 发回，否则返回 400 | M4、N4 的重试 |
| 并发上限：Flash 2500，Pro 500 | 评估运行器 |
| 实测（B0-F，dev 61 份，2026-10-21）：思考 token 占输出的 90%，输出占成本的 89%。一份合同的输出最多 17,649 token，低于配置的 `max_tokens: 32000` | 评估的成本计算、`max_tokens` 的设置 |
| 实测（B0-P，dev 61 份，2026-10-21）：思考 token 占输出的 88%。在同样的合同上，输出 token 是 Flash 的 1.06 倍，思考 token 是 1.04 倍，成本是 3.60 倍，P50 延迟是 1.6 倍。一份合同的输出最多 17,247 token | 评估的成本计算、`max_tokens` 的设置 |
| `tool_choice` 接受 `none`、`auto`、`required` 和指定工具 `{"type": "function", "function": {"name": ...}}`。有工具时默认 `auto`。**思考模式下 `required` 和指定工具返回 400**，所以只能用 `auto`。2026-10-25 查 [API 参考](https://api-docs.deepseek.com/api/create-chat-completion) | M4 |
| strict 模式是 Beta：`base_url` 要用 `https://api.deepseek.com/beta`，每个工具设 `"strict": true`。思考和非思考模式都支持。对象的全部属性都要写进 `required`，`additionalProperties` 必须是 `false`。不支持 `minLength`、`maxLength`、`minItems`、`maxItems`。2026-10-25 查 [Tool Calls 指南](https://api-docs.deepseek.com/guides/tool_calls) | M5 |
| 带工具时，`reasoning_content` 要在后面**全部**请求里原样发回，包括模型没有调用工具的轮次。把 `choices[0].message` 原样追加到 `messages` 就满足。工具结果用 `role: "tool"` 和对应的 `tool_call_id`。不带工具的多轮对话里，发回的 `reasoning_content` 被忽略。2026-10-25 查 [思考模式指南](https://api-docs.deepseek.com/guides/thinking_mode) | M4 |
| `reasoning_effort` 另外接受 `none`（关掉思考）；`minimal` 被当成 `low`。也可以用 `extra_body={"thinking": {"type": "disabled"}}` 关掉思考。2026-10-25 查 API 参考 | 不用（关掉思考会改变实验条件） |

---

## 2. 选型方式（2026-10-21 决定）

### 2.1 逐阶段选型

- **逐阶段选型。** 不在开工前定下所有阶段的技术。做完一个阶段，再讨论下一个阶段用什么。
- **原因：**
  1. 详细设计已经定了模块划分、数据流和接口。后面的选择大多是"往固定位置里放哪个库"。
  2. 大部分后期选择只影响一个模块，以后替换不会牵连别的模块。
  3. 前面阶段的实测结果（例如成本、缓存命中率）能帮助做后面的决定。
- **例外：** 第 2.2 节的 6 项地基决策会被很多阶段共用，必须在阶段 0 和阶段 1 定下来。

### 2.2 必须提前定的 6 项地基决策

| # | 决策 | 在哪个阶段定 | 影响哪些阶段 | 定错的代价 |
|---|---|---|---|---|
| F1 | Python 版本和包管理工具 | 阶段 0 | 全部 | 环境要重建 |
| F2 | 测试框架 | 阶段 0 | 全部 | 测试要重写 |
| F3 | 配置文件格式（检查清单和实验配置） | 阶段 0 | 0、1、7 | 配置要重写 |
| F4 | 同步还是异步 | 阶段 1 | 1、3、4、5 | 模型调用和评估运行器要重写 |
| F5 | 数据结构用什么库，以及核心接口（`Finding`、`review_contract()` 等，见实现路线第 2 节） | 阶段 0（库）、阶段 1（接口） | 1–7 | 每个模块都要改 |
| F6 | 模型调用怎么封装，以及测试时怎么替换成假模型 | 阶段 1 | 1、3、4、6 | B0、Agent、L3 都要改 |

> **注意 F4：** 不能只按阶段 1 的需要来定。阶段 1 的 B0 只调用 1 次模型，用同步代码最简单。但阶段 3 要同时跑 4 个检查项，阶段 5 的 API 框架通常也是异步的。所以 F4 要按阶段 3 和阶段 5 的需要来定。

### 2.3 全局约定

F1–F6 讨论完以后，结论写在这张表里。后面所有阶段的选择都不能和它冲突。

| # | 结论 | 原因 | 决定日期 |
|---|---|---|---|
| F1 | Python 3.13 + uv。用 `uv init --package` 建 src 布局；`uv.lock` 提交到 Git | 本机已有 3.13.7，不用下载；全部依赖都支持 3.13；锁文件让 `manifest.json` 记下的环境可以复现 | 2026-10-21 |
| F2 | pytest。异步测试插件在阶段 1 和 F4 一起定 | fixture 能共用手写的小合同；`parametrize` 适合正则用例；`tmp_path` 适合 SQLite 测试 | 2026-10-21 |
| F3 | YAML。用 PyYAML 的 `safe_load` 读取，再用 Pydantic 校验 | 文档里的文件名和示例已经是 `.yaml`；`>` 能写多行 `guidance`，也能写注释；Pydantic 校验能拦下 YAML 的隐式类型转换（例如 `no` → `False`） | 2026-10-21 |
| F4 | 全部异步：`AsyncOpenAI`（2026-10-21 前是 `AsyncAnthropic`）、`async def review_contract`，并发用 `asyncio.Semaphore`。异步测试用 pytest-asyncio，打开 `asyncio_mode = "auto"`。**例外（2026-10-23）：** 数据库函数是同步的，用标准库 `sqlite3`，在事件循环的线程里直接调用（见第 3.5 节） | 阶段 3 要并发跑 17 项，`asyncio.wait_for` 能直接做工具超时；阶段 5 的 FastAPI 是异步的；auto 模式下测试函数不用加标记。数据库每次写入不到 1 毫秒，一次模型调用约 30 秒，同步调用的阻塞可以忽略 | 2026-10-21 |
| F5 | 数据结构用 Pydantic v2。接口按实现路线第 2 节，另外：`review_contract()` 返回 `ReviewResult`（结论 + `Usage` + 延迟）；`Finding` 加 `reason`；`ReviewConfig` 加 `max_tokens`，`effort` 用 `Literal` | 成本和延迟要随每份合同返回，多份合同并发时不会混在一起；`abstained` 要写明原因；Pydantic 能直接校验模型返回的 JSON，FastAPI 也直接支持 Pydantic | 2026-10-21 |
| F6 | `LLM` 协议，用参数传给 `review_contract()`。真实实现 `DeepSeekLLM`（调用 DeepSeek 的原生接口），测试用 `FakeLLM`。`parse()` 返回 SDK 的 `ParsedChatCompletion` 对象。重试交给 SDK（`max_retries=4`）。SDK 固定为 `openai>=3.36,<4`，到阶段 7 不升级。另有 1 个真实调用的测试，标记 `live`，默认跳过 | 依赖清楚，测试不花钱、结果固定；Agent 要把模型的回复（含 `reasoning_content`）原样追加回历史，所以保留 SDK 对象；中途换 SDK 会改变实验条件。2026-10-21 修改：原来是 `AnthropicLLM` + `anthropic>=1.17,<2`，换成 DeepSeek 的原因见第 3.3 节 | 2026-10-21 |

### 2.4 每次选型的步骤

1. 读第 1 节（已核实的事实）和第 2.3 节（全局约定）。
2. 对这个阶段的每个模块，给用户 2–3 个方案、各自的优缺点和一个建议。
3. 检查每个方案是否和全局约定冲突。有冲突时，先说明要改哪些已经写好的代码，再由用户决定是否修改全局约定。
4. 用户决定以后，把结论写进第 3 节。地基决策写进第 2.3 节。
5. 写这个阶段的施工文档时，加上第 2.3 节的链接，让施工的 Agent 也遵守。

---

## 3. 模块决策

| 模块 | 文件 | 实现阶段 | 状态 | 结论 |
|---|---|---|---|---|
| 共用数据结构 | `models.py` | 0、1 | 已决定 | 见 F5 |
| 模型调用 | `llm.py` | 1、3 | 阶段 1、3 部分已实现 | 见 F6 和第 3.3 节。阶段 1 实现 `parse()`（JSON 模式 + Pydantic 校验）、价格和成本计算、`FakeLLM`。阶段 3 加 `create()`（工具调用）和"截断后加倍重做 1 次"，见第 3.6 节 |
| N1 合同解析 | `parse.py` | 0、2、5 | 阶段 0 部分和规则部分已实现；PDF 部分待讨论 | 阶段 0 用标准库 `json` 读取官方数据包，没有选型。规则部分（阶段 2）见第 3.5 节和"施工时的规则调整"。PDF 部分（阶段 5）待讨论 |
| N2 检查清单 | `checklist.py` | 0 | 已决定 | YAML 文件，PyYAML `safe_load` 读取，Pydantic 校验（见 F3、F5） |
| N3 结论账本 | `ledger.py` | 2 | 已实现 | 纯函数 `validate_finding()` + `record_finding()`，可读的 `operation_id`，见第 3.5 节 |
| N4 核对器 | `verify.py` | 4 | 待讨论 | L3 的候选方案（2026-10-26 用户要求阶段 4 一起比较）：① 单次调用，JSON 输出，不用工具（详细设计的原方案）；② 带只读工具的 Agent，自己在合同里找反面证据，仍然看不到 Agent 的推理过程。原因：A1-F 的主要错误是把"没提到"判成"支持"或"相反"（163 / 423 条），方案 ② 直接针对这类错误 |
| N5 报告 | `report.py` | 5 | 待讨论 | |
| M1 API | `api.py` | 5 | 待讨论 | |
| M2 数据库 | `db.py` | 2 | 已实现 | 标准库 `sqlite3`，同步调用，6 张表（加 `review_items`），见第 3.5 节 |
| M3 状态机 | `fsm.py` | 2 | 已实现 | 手写跳转表 + "比较后再写"，见第 3.5 节 |
| M4 Agent 循环 | `agent.py` | 3 | 已实现 | 见第 3.6 节 |
| M5 工具系统 | `tools.py` | 3 | 已实现 | `rank-bm25`，不开 strict，只读工具在线程里运行并设 10 秒超时，见第 3.6 节 |
| M6 断点恢复 | `recovery.py` | 3 | 已实现 | 未完成的项退回 `pending` 重做；评估续跑时整份合同重做，见第 3.6 节 |
| M7 评估 | `eval/` | 0、1、6、7 | 阶段 0、1 部分已实现；阶段 6、7 待讨论 | 阶段 0 写了 `dataset.py`、`sample.py`、`stats.py`，只用标准库。阶段 1 写了 `metrics.py`、`run.py`、`__main__.py`，见第 3.2 节和第 3.4 节 |

每讨论完一个模块，在下面加一节，写明：选了什么、考虑过什么、为什么这样选。

### 3.1 阶段 0 的选型（2026-10-21）

结论和原因见第 2.3 节。这里只记考虑过、但没有选的方案。

| 决策 | 选了 | 没选的方案和原因 |
|---|---|---|
| F1 | Python 3.13 + uv | Python 3.12 + uv：要另外安装 3.12，在本项目里和 3.13 没有功能差别。pip + venv：没有锁文件，环境不能可靠复现 |
| F2 | pytest | unittest：没有依赖，但写法长，没有参数化 |
| F3 | YAML + PyYAML | TOML + `tomllib`：没有依赖，但长文本可读性差，还要改文档里的 `.yaml` 文件名。JSON：不能写注释，长文本要转义 |
| F5（库） | Pydantic v2 | 阶段 0 先用 `dataclass`：阶段 1 要改代码和测试。等到阶段 1 再定：阶段 0 只能返回 `dict`，测试要重写 |

### 3.2 阶段 1 的选型（2026-10-21）

地基决策 F4、F5、F6 的结论见第 2.3 节。

**没有选的方案：**

| 决策 | 选了 | 没选的方案和原因 |
|---|---|---|
| F4 | 全部异步 + pytest-asyncio | 同步 + 线程池：超时和取消难做，SQLite 连接要按线程分开，阶段 3、5 可能重写。同步核心外包 `asyncio.to_thread`：同一套逻辑有两种写法。anyio 测试插件：不加依赖，但每个测试要加标记 |
| F5 | 返回 `ReviewResult` | 保留 `list[Finding]`，由 `llm` 对象累计用量：多份合同并发时用量会混在一起 |
| F6 | `LLM` 协议 + `FakeLLM` | 在 HTTP 层替换：预先写好的 API JSON 很长，思考块难写。录制和回放（vcrpy 一类）：录下的文件含合同全文，和 SDK 1.x 的 `httpx2` 是否兼容不确定 |
| SDK 版本 | `anthropic>=1.17,<2`（2026-10-21 换成 `openai>=3.36,<4`，见第 3.3 节） | 2.0.0：2026-10-21 刚发布，变更记录只写了"新的 API 版本 2026-10-21" |

**M7 评估的阶段 1 部分：**

| 项目 | 结论 | 原因 |
|---|---|---|
| "相反"类 F1 | 主表用合并计算（全部条目合在一起算）。另算论文式宏平均：先对每个检查问题算 F1，再取平均，只包含至少有 1 条标准"相反"的检查问题。论文式只用于和论文比较 | test-40 只有 66 条"相反"，7 个检查问题一条都没有，宏平均不稳定（见 [notes.md](notes.md) 第 1.2 节） |
| 证据精确率和召回率 | 合并计算，只用标准答案是"支持"或"相反"的条目 | 和"相反"类 F1 的算法一致 |
| 结果文件 | `findings.jsonl` 每行是一份合同的完整 `ReviewResult` | 一份合同要么完整写入，要么没有；`--resume` 只要跳过已有的 `contract_id` |
| 合同之间的并发 | `eval run --workers`，默认 4 | B0 跑一次 dev 要调用 61 次，顺序跑太慢 |
| 命令行 | 标准库 `argparse` | 和阶段 0 的脚本一致，不加依赖。没选 typer：要多一个依赖 |
| API 密钥 | `python-dotenv`，在命令行入口读取 `.env` | 详细设计规定密钥放在 `.env` 里 |
| 实现路线第 4 节 #1、#4、#5 | 按原建议：B0 的结论全部算 `verified`；B0 引用不存在的 `span_id` 时保留原样并单独计数；加 `models.py`、`llm.py`、`configs/` | 2026-10-21 确认 |

### 3.3 换成 DeepSeek（2026-10-21）

用户决定：以后不用 Claude，只用 DeepSeek Flash 和 DeepSeek V4 Pro。DeepSeek 的约束见第 1.2 节。

| 决策 | 选了 | 没选的方案和原因 |
|---|---|---|
| 接入方式 | 原生接口（OpenAI 格式）+ `openai` SDK | Anthropic 兼容接口：能保留 `anthropic` SDK 和 F6 的写法，但只有切回 Claude 时才有用。它没有 JSON 模式，用量字段也没有文档 |
| 结构化输出 | JSON 模式 + 提示词里给出 Schema + Pydantic 校验。校验失败算 `parse_error` | 强制调用一个工具，从工具参数读结果：DeepSeek 不保证工具参数符合 Schema，效果和 JSON 模式相同，代码更多 |
| 配置命名 | `-F`（Flash）、`-P`（Pro）：B0-F、A2-P 等 | 保留 `-H`、`-O`：名字和模型对不上 |
| 峰谷价格 | 按每次调用的时间（`completion.created`）选价格，不处理节假日 | 全部按高峰价：简单，但非高峰时成本高估 1 倍。全部按非高峰价：高峰时低估 |
| `effort` | 配置里写 `high`。`ReviewConfig.effort` 的类型不变 | 写 `medium`：DeepSeek 把它当成 `high`，`manifest.json` 记下的值和实际不一致 |

**对已有代码的影响：** 阶段 0 的代码不调用模型，不用改。`ReviewConfig`、`Finding` 的字段不变。`Usage` 加了 `reasoning_tokens`（思考 token，是 `output_tokens` 的一部分）。`cache_creation_input_tokens` 保留，但 DeepSeek 不报告也不收费，永远是 0。

### 3.4 M7 评估（阶段 1 部分）的模块讲解

2026-10-24 移到本地讲解文档 `docs/讲解/`，不提交到 GitHub。

### 3.5 阶段 2 的选型（2026-10-23）

用户决定全部按建议做。施工细节见[阶段 2 施工文档](ProvenClause_阶段2施工.md)。

**选了什么、没选什么：**

| 模块 | 决策 | 选了 | 没选的方案和原因 |
|---|---|---|---|
| M2 | 数据库库 | 标准库 `sqlite3`，同步调用。只用 1 个连接，所有调用都在事件循环的线程里 | `aiosqlite`：写法和 F4 一致，但多一个依赖，后台每个连接开 1 个线程，所有函数和测试都要改成 `async`。`asyncio.to_thread` 包 `sqlite3`：连接跨线程，要加锁，F4 没选线程池也是这个原因 |
| M2 | 列表字段 | `span_ids`、`refs`、`defines`、`checks` 存成 JSON 文本列 | 另建关联表：表变多，查询要连表。SQLite 3.50 有 JSON 函数，以后需要时也能查 |
| M2 | 表的写法 | `STRICT` 表、WAL、`foreign_keys=ON`。表结构版本记在 `PRAGMA user_version` | 迁移工具（Alembic 一类）：要用 SQLAlchemy，本项目只有 1 个版本的表结构 |
| M2 | `review_id` | `uuid4().hex` | 自增整数：多次评估之间会重复。ULID：要加依赖 |
| M3 | 状态机写法 | 手写跳转表（`dict[状态, frozenset[状态]]`）+ `transition_review()`、`transition_item()` | `transitions` 库：加依赖，动态往对象上加方法，状态在 Python 对象里，不在数据库里。`python-statemachine`：加依赖，2 张小表用不上它的功能 |
| M3 | 防止并发写错状态 | "比较后再写"：`UPDATE ... WHERE status = :from`，改了 0 行就抛出 `InvalidTransition`。改状态和写 `events` 在同一个事务里 | 先读状态再写：读和写之间状态可能被改 |
| N3 | 登记规则写在哪里 | 纯函数 `validate_finding(finding, contract) -> list[str]`，`record_finding()` 调用它。阶段 4 的 L1 也用它 | 写进 `Finding` 的 Pydantic 校验器：B0 弃权时用 "NotMentioned" 占位，实现路线第 4 节 #4 要求 B0 保留不存在的 `span_id`，校验器会拒绝这些结论 |
| N3 | 出错时 | 返回错误列表，一次列出全部问题，用英文写。阶段 3 的工具把它交回 Agent | 抛出异常：Agent 一次只能知道 1 个问题 |
| N3 | `operation_id` | 可读字符串 `"{review_id}:{item_id}:{attempt}"` | sha256：不可读，唯一性和可读字符串相同 |
| N3 | 重复写入 | `INSERT ... ON CONFLICT(operation_id) DO NOTHING`，返回已有的那一条，`duplicate=True` | 重复时报错：Agent 重试同一次登记会被当成失败 |
| N1 | 正则库 | 标准库 `re` | `regex` 包：用不上它多出的功能。spaCy：加大依赖，章节编号不需要句法分析 |
| N1 | `Span.section` | 最深一层的"数字点号"编号，例如 `"2"`、`"2.06"`、`"1.1"`；`ARTICLE 1` 记为 `"1"`。`(a)`、`(i)` 这类括号编号不记 | 记完整路径 `"5(a)(ii)"`：要分清 `(i)` 是罗马数字还是字母，半句话开头的句子（notes 第 3.5 节）也会让层级出错。只记顶级 `"5"`：dev 有 266 个 `1.1` 式标题，`Clause 14.1` 这类引用找不准 |
| N1 | 防止误识别标题 | 编号要按顺序出现：等于上一个编号加 1、等于上一个编号、或者从 1 重新开始 | 不检查顺序：句中的 `2.`、`Section 4(b) of this Agreement` 这类句子会被当成标题 |
| N1 | 字母标题 `A.` | 合同里有数字标题时，`A.`、`B.` 当成前言或分组，不作为章节；没有数字标题时才作为章节 | 总是作为章节：dev 合同 10 的 `A.`–`C.` 是前言，合同 9 的 `A.`、`B.` 下面又从 `1.` 编号 |
| N1 | 定义存在哪里 | `Span` 加字段 `defines: list[str]`（这个句子定义的术语），`spans` 表加一列 | `Contract` 加 `definitions: dict[str, list[int]]`：同一个信息存两处，`spans` 表之外还要多存一份 |
| N1 | `refs` 存什么 | 规范化的章节编号（例如 `"2.06"`），只存能在本合同找到目标章节的引用。丢掉的条数单独统计 | 存全部匹配：会存下 `Section 12 of the Securities Exchange Act`、`Section A of this Attachment A` 这类指向合同外面的引用 |
| N1 | 什么时候运行 | 纯函数 `annotate_contract()`，由 `load_contractnli()` 默认调用。`format_contract()` 不变，B0 的 `prompt_hash` 不变 | 只在需要时单独调用：调用方容易忘记，拿到的 `section` 是空的 |

**设计缺口的处理：**

| # | 缺口 | 决定 |
|---|---|---|
| 1 | 设计的 5 张表里没有检查项的状态，M3 和 M6 都要用 | 加第 6 张表 `review_items (review_id, item_id, status, attempt, retries, updated_at)`。没选"从 `events` 里找最后一次跳转"：恢复查询复杂 |
| 2 | 检查项的跳转表缺边 | 加 `drafting → abstained`（超出预算、拒绝回答发生在登记之前）、`drafting → pending` 和 `verifying → pending`（M6 恢复）。A1 也走 `drafting → verifying → verified`，`verifying` 什么都不做，A1 和 A2 用同一张表 |
| 3 | `attempt` 什么时候加 1 | 退回 `drafting`（重试）和退回 `pending`（恢复）都加 1，这样新一次登记有新的 `operation_id`。只有重试让 `retries` 加 1。所以"最多重试 2 次"看 `retries`，恢复不计入（实现路线第 4 节 #3） |
| 4 | L2 的例外词表 | 不在阶段 2 处理，阶段 4 讨论（notes 第 4 节第 3 条） |
| 5 | 阶段结束时的 dev 评估 | 不跑。阶段 2 不改 B0 和提示词，再跑一次只会得到同样的条件，还要花约 $0.32。阶段 2 的数字是 N1 的统计，不调用模型 |
| 6 | 数据库文件放在哪里 | 阶段 3 再定。阶段 2 只写 `connect(path)`，测试用 `tmp_path`。阶段 3 已定，见第 3.6 节 D1 |
| 7 | `findings` 表的核对结果 | 一列 `checks`（JSON），和 `Finding.checks` 一一对应，不分成 `l1`、`l2`、`l3` 三列。另加 `reason` 列，和 `Finding.reason` 对应 |

**施工时的规则调整（2026-10-24）：** 施工文档第 3.2–3.4 节允许在 train 和 dev 上调整 N1 的规则。下面是全部改动。没有用 test 和 test-40。

| # | 规则 | 施工文档的写法 | 改成 | 原因和证据 |
|---|---|---|---|---|
| 1 | 章节顺序 | 下一个编号等于上一个、加 1，或者 1 | 另外允许向前跳，但下一个候选标题的编号要在 N 到 N+2 之间。最后一个标题没有下一个，最多跳 3 | 少一个标题时，后面的全部标题都被拒绝，句子一直沿用旧编号。例如 dev 582 没有 `9.`，`10.`–`15.` 都被拒绝；dev 35 没有 `3.`，`4.1`–`13.9` 共 37 个标题被拒绝。dev 598 的孤立 `4.` 后面是 `3.`，所以仍然被拒绝 |
| 2 | 第一个标题 | 编号 ≤ 2 | 另外允许被下一个标题确认（同第 1 条） | train 145 从 `Article 4` 开始，全部 13 个标题被拒绝 |
| 3 | 标题写法 | `Section 5` | 另外接受 `Section 5.1`，记为 `"5.1"` | 和 `N.M` 一样是"数字点号"编号 |
| 4 | 定义的动词 | `means`、`shall mean` 等 9 种 | 另加 `has the meaning`、`shall have the meanings`、`is defined as`、`shall be deemed to include`、`shall be (broadly) interpreted to include`；允许中间有 `as used herein` | dev 37 S40、dev 45 S8、dev 610 S11、dev 19 S17 等 |
| 5 | 动词前的多个术语 | 只取紧挨动词的术语 | `"X", "Y" and "Z" shall have the meanings` 取全部 3 个 | dev 37 S47 |
| 6 | 小写术语 | 术语以大写字母或数字开头 | 后面紧跟定义动词时，也接受小写开头 | `“day” shall mean`（dev 7 S181）、`'confidential information' means`（dev 54 S13）、`"trade secrets" means`（dev 398 S30）。括号里的小写术语仍然不接受 |
| 7 | 括号和 `referred to as` 前面的词 | `the`、`a`、`an`、`each`、`collectively`、`hereinafter`、`individually`、`or` 等 | 另加 `this`、`and`、`together`、`jointly`、`also`、`hereafter`、`herein`、`called`；`referred to herein / below, collectively, as` | `(this “Agreement”)`（dev 32 S1）、`(hereinafter called "BROOKSTECH")`（dev 45 S2）、`referred to herein, collectively, as the "Indemnified Parties"`（dev 64 S72） |
| 8 | 引号里的 `the` | 原样保存 | `(“the Database”)` 记为 `"Database"` | dev 9 S41、dev 82 S1、dev 152 S6。原样保存时 `lookup_definition("Database")` 查不到 |
| 9 | 同位语 | 不识别 | 句子以 `, the “X”.` 结尾时是定义 | dev 64 S14 `, the “Property”.`、S18 `, the “Business”.` |
| 10 | 闭引号 | `"”` | 另外接受 `“` | dev 37 S8 `(the “Authority“)` |
| 11 | 交叉引用 | 全部匹配 | 句首的 `ARTICLE 1 - DEFINITIONS` 这类标题不算引用 | dev 10 S11、dev 398 S50 把自己算成了引用 |
| 12 | 引用的排除（2026-10-24 审核后改） | `of` 后面不是 `this Agreement`、`this NDA`、`hereof` 时排除 | `of this ...` 都不排除 | train 和 dev 有 2 处 `paragraph 4 of this letter agreement`，指本合同，原来被丢掉。train 找到的引用从 779 条变成 781 条，丢掉的仍然是 62 条；dev 不变 |
| 13 | 章节编号的规范化（2026-10-24 审核后改） | — | `normalize_key()` 和 `_REF` 一样加 `(?!\d)`：3 位数不是章节号 | 原来 `"123"` 被截成 `"12"`，`"Section 100"` 被截成 `"10"`。阶段 3 的 `get_section` 直接把模型给的编号交给 `section_spans()`，会返回错的章节。现在返回空列表 |

**已知的不准确（没有修）：**

- 没有编号的大写标题（`CONFIDENTIALITY OF EVALUATION MATERIALS`）不识别。dev 405、507、595 因此没有章节号，train 有 41 份这样的合同。识别它们要另定规则，施工文档没有覆盖。
- 单独的罗马数字标题 `I.`、`II.` 不识别（施工文档第 3.2 节的决定）。train 89、111、153 因此丢掉了 `Section III` 这类引用。
- `1)`、`16 Except`（没有点号）这类标题不识别。train 的 62 条丢掉的引用大多来自这 3 种情况。
- 合同 9 的 `A.`、`B.` 下面都从 `1.` 编号，`section_spans(c, "1")` 返回两处（施工文档第 3.2 节）。
- `Sections 3 and 4` 只取 `3`（施工文档第 3.4 节）。
- 编号写错的合同（train 211 在 `2.` 下面写 `1.1.`–`1.7.`）按顺序规则拒绝这些小节。
- `"DIRECTLY OR INDIRECTLY" as they modify the words "Compete" or "Competing" shall mean`（dev 398 S17）记成了 `Compete`、`Competing`。

**施工时的其他决定（施工文档没有写明的细节）：**

- `transaction()` 在已经打开的事务里调用时，加入外层事务，不再 `BEGIN`。这样 `save_contract()` 可以放进调用方的事务里。
- `connect()` 先检查 `user_version`，再建表。版本不对的文件不会被改动。
- `db.py` 另有 `insert_finding()` 和 `get_finding()`，`ledger.py` 不直接写 SQL。`StoredFinding` 另有 `finding_id`。
- 带 `RETURNING` 的语句用 `fetchall()` 读完。只读 1 行时，语句没有结束，自动提交模式下写入不会提交。
- `stats --n1` 的"定义术语数"按合同去重（不分大小写）。`--n1` 不接受 `--split test`。

### 3.6 阶段 3 的选型（2026-10-25）

讨论时先查了 DeepSeek 文档，新查到的事实写在第 1.1、1.2 节。用户决定：C6 改成"截断后加倍重做 1 次"，D4 选"整份合同重做"，其他 17 项按建议。施工细节见[阶段 3 施工文档](ProvenClause_阶段3施工.md)。

**选了什么、没选什么：**

| # | 模块 | 决策 | 选了 | 没选的方案和原因 |
|---|---|---|---|---|
| A1 | `llm.py` | 工具调用的接口 | `LLM` 协议加 `create(*, model, system, messages, tools, max_tokens, effort) -> ChatCompletion`。`finish_reason` 为 `stop`、`tool_calls` 时返回；`content_filter`、`length` 抛出 `ModelStopped`；`insufficient_system_resource`、`aborted` 抛出 `ModelInterrupted`。模型的回复原样追加回对话，`reasoning_content` 因此每轮都发回 | — |
| A2 | `llm.py`、`tools.py` | strict 模式 | 不开。工具参数用 Pydantic 校验，错误作为工具结果交回模型，并计数 | 开 strict：要换到 Beta 地址，阶段 7 前行为可能变；B0 和 A1 会走不同的地址；`span_id` 是否存在这类值的检查仍然要程序做 |
| A3 | `llm.py` | 测试用的假模型 | 现有的 `FakeLLM` 加 `create()`，和 `parse()` 共用一个按调用顺序的脚本。辅助函数生成"调用工具"和"只回文字"两种回复 | 另写 `FakeAgentLLM`：阶段 4 的 Agent 和 L3 轮流调用，一个脚本更好写 |
| B1 | `tools.py` | BM25 | `rank-bm25==0.2.2` 的 `BM25Okapi`。分词：小写、`[a-z0-9]+`、去掉约 30 个停用词，不做词干还原。每份合同建 1 次索引 | 自己写：IDF 的细节容易写错。`bm25s`：依赖多，一份合同平均 84 句用不上它的速度 |
| B2 | `tools.py` | 10 秒超时 | 4 个只读工具用 `asyncio.wait_for(asyncio.to_thread(...), 10)`。`record_finding` 在事件循环的线程里运行（SQLite 连接只在这个线程用） | 直接调用：`wait_for` 打断不了同步代码，超时只是名义上的 |
| B3 | `tools.py` | 工具结果的格式 | 文本，每句一行 `[S23] (§2.06) ...`，出错时 `ERROR: ...`。上限：`top_k` ≤ 20，`window` ≤ 3，`span_ids` ≤ 10，`get_section` 最多返回 40 句 | JSON：token 多，和 system 提示词里合同的写法不同 |
| B4 | `agent.py`、`tools.py` | 脱敏范围 | 写进 `events` 的全部文本都脱敏，包括模型回复和思考内容：邮箱、电话、中国身份证号、美国 SSN。模型看到原文 | 只给工具结果脱敏（详细设计原文）：思考内容也会抄下这些信息 |
| B5 | `agent.py` | `events` 存什么 | 每次模型调用存 `finish_reason`、`content`、`reasoning_content`、`tool_calls`、用量；每次工具调用存工具名、参数、结果、耗时。估计一次 dev 约 60 MB | 只存长度：缓冲日的错误分析要读模型的推理过程 |
| C1 | `agent.py` | 提示词结构 | system = 通用说明 + 合同全文，17 项完全相同，不放时间戳和 `review_id`。user = 检查问题 + `guidance`。17 项用同一组工具定义 | 实现路线已经定了，这里只确认 |
| C2 | `agent.py` | 模型不调用工具就结束 | 追加一条 user 消息，要求调用 `record_finding`。算 1 步 | 从文字解析结论：规则不可靠。关掉思考以用 `required`：和 B0 的条件不同 |
| C3 | `agent.py` | 步数 | 1 次模型调用算 1 步，最多 `max_steps`（8）。一次回复里的多个工具调用按顺序执行，算 1 步。`record_finding` 成功后停止，同一回复里后面的调用不执行。登记被拒绝时，错误交回模型，步数照算。超过步数：`abstained`，原因 `budget_exceeded` | — |
| C4 | `agent.py`、`models.py` | 每次审核的 token 上限 | `ReviewConfig` 加 `max_review_output_tokens`，默认 300,000（Flash 约 $0.18）。每次调用模型前检查这次审核累计的输出 token；超过后，没结束的项都是 `abstained`，原因 `budget_exceeded`。先跑 5 份合同再调整数值 | 只靠每项 8 步 × `max_tokens`：最坏 17 × 8 × 32,000 ≈ 435 万 token，Flash 一份合同约 $2.6 |
| C5 | `agent.py` | 并发和缓存预热 | 一次审核里 `Semaphore(config.concurrency)`（4）。先单独跑第 1 项，再并发跑其余的项。评估同时 4 份合同，最多 16 个调用同时进行 | — |
| C6 | `llm.py`、`agent.py`、`baseline.py` | 调用出错 | **截断（`length`）：** 用 2 倍的 `max_tokens` 重做同一次调用 1 次。被截断的回复不进对话记录，费用照算；重做算在同一步里；重做前先查 C4 的预算，不够就不重做。第 2 次还截断：`abstained`，原因 `max_tokens`。**B0 用同一条规则**，B0-F 和 B0-P 在 dev 上没有截断过，以前的结果仍然有效，`prompt_hash` 不变。**拒绝回答**：`abstained`，原因 `refusal`。**服务器中断或 SDK 重试后仍失败**：整个审核 `failed`，异常抛给评估运行器，`--resume` 重跑这份合同 | 截断直接放弃（原建议）：用户决定给 1 次加倍的机会。服务器故障算这一项 `abstained`：基础设施的故障会算进覆盖率 |
| C7 | `models.py`、`eval/metrics.py` | 运行统计 | `Finding` 加 `stats: AgentStats \| None`：步数、各工具调用次数、工具错误数、被拒绝的登记数、提醒次数（C2）、截断重做次数（C6）。`metrics.py` 从 `findings.jsonl` 计算 | 只存在 `events` 里：算指标时还要读数据库 |
| D1 | `eval/run.py` | 数据库文件放在哪里（第 3.5 节缺口 6） | 评估：每次运行一个文件 `runs/<run_id>/reviews.db`。阶段 5 的 API：`data/provenclause.db` | `:memory:`：跑完就丢，缓冲日没有 `events` 可以分析。全局一个文件：不同运行混在一起 |
| D2 | `review.py` | 审核函数怎么拿到连接 | `review_contract()` 加只能按名字传的参数 `conn: sqlite3.Connection \| None = None`。A1、A2 收到 `None` 时打开 `:memory:`。B0 不用它 | 模块级全局连接：`db.py` 规定不保存全局连接 |
| D3 | `recovery.py` | 断点恢复 | `recover(conn)` 找出 `created`、`parsing`、`reviewing` 的审核，把 `drafting`、`verifying` 的项退回 `pending`（`attempt + 1`）。`resume_review()` 只跑 `pending` 的项。结论已登记、状态还没改就崩溃时，这一项也重做；旧 `attempt` 的结论留在表里，最终结果取当前 `attempt` 的结论 | 发现当前 `attempt` 已有结论就直接进入 `verifying`：省 1 次调用，分支更多 |
| D4 | `eval/run.py` | 评估续跑遇到做了一半的合同 | 整份合同重做，用新的 `review_id`，和 B0 一样。M6 在阶段 3 用 FakeLLM 测试，阶段 5 的 API 启动时调用 | 用 M6 逐题接着做：每次中断最多省约 $0.16（Flash）或 $0.6（Pro）；但用量要跨进程累计，延迟没法正确计算，而这两项是阶段 7 主表的数字 |

**对全局约定和已有代码的影响：**

- **D2 给核心接口加了 1 个参数。** 实现路线第 2 节规定"只加字段"。用户 2026-10-25 同意。B0 的行为和 `prompt_hash` 不变。要改 `review.py`、`eval/run.py` 和它们的测试。
- **B2 用了线程，和 F4 不冲突。** F4 没选线程池，是因为模型调用和 SQLite 连接会跨线程。这里只有不碰 SQLite 的只读工具在线程里运行。
- **C4、C7 是新字段，都有默认值。** 以前的 `findings.jsonl` 仍然能读。但 `eval run --resume` 比较完整的配置，以前的 B0 运行加了新字段以后不能再续跑。它们都已经跑完，不受影响。
- **C6 改了 B0 的一条代码路径**（截断时）。这条路径在 dev 上从没走到过，B0 的已有结果不变。
- **新的运行依赖：** `rank-bm25==0.2.2`，连带 numpy。

**设计缺口的处理：**

| # | 缺口 | 决定 |
|---|---|---|
| 1 | 放弃的项在 `findings` 表里没有记录，最终结果和恢复都读不到原因 | 放弃时也写一行 `findings`：`label` 用 `NotMentioned` 占位，`span_ids` 为空，写 `reason`，`outcome = "abstained"`，`operation_id` 用当前 `attempt` 的。和 B0 的 `no_answer()` 写法一致 |
| 2 | `budget_exceeded` 不在 `NO_ANSWER_REASONS` 里，计分时占位的 `NotMentioned` 会被当成答案 | 加进 `NO_ANSWER_REASONS`。超出步数和超出总预算都用 `budget_exceeded`，事件里区分 `steps` 和 `review_tokens` |
| 3 | A1 没有核对器，`findings.outcome` 什么时候写 | 登记成功后，在一个事务里做 `drafting → verifying → verified`，并把这条结论的 `outcome` 改成 `verified`。`db.py` 加 `set_finding_outcome()`，阶段 4 也用它 |
| 4 | 怎么模拟"进程被杀" | 审核循环只把 `Exception` 当成失败（审核 `failed`）。`BaseException`（`KeyboardInterrupt`、`CancelledError`、测试里的 `Crash`）不改状态，和进程被杀一样留在 `reviewing` |
| 5 | M6 恢复的审核，用量和延迟怎么算 | 只算恢复以后的部分。崩溃前结束的项，`stats` 为 `None`（`stats` 不存数据库）。评估不用恢复（D4），所以不影响指标 |
| 6 | 只停在 `created` 的审核（建好就崩溃） | 详细设计 M6 只说 `parsing`、`reviewing`。这里也恢复 `created`，否则它永远不会开始 |

**施工时的规则调整（2026-10-26）：** 下面是施工时改了的规则和数值。都只在 dev 上看结果，没有用 test 和 test-40。

| # | 规则 | 施工文档的写法 | 改成 | 原因和证据 |
|---|---|---|---|---|
| 1 | `max_review_output_tokens`（C4） | 300,000 | A1-F、A1-P 的配置改成 100,000。`ReviewConfig` 的默认值仍然是 300,000 | 5 份 dev 合同（run `a1-f_dev_20261025-201026`）一份合同的输出最多 16,065 token，单次调用最多 3,393 token。100,000 约是实测最大值的 6 倍，最坏的成本更低。用户 2026-10-26 决定 |
| 2 | 停用词表 | 约 30 个 | 31 个：`a an and any are as at be been by for from has have in is it its of on or shall such that the this to was were will with` | — |
| 3 | 截断重做的计数 | `on_truncated` 里 `n_length_retries + 1` | 只在真的重做时加 1：总预算用完、不重做时不计数 | 不重做也计数，统计会多算 |
| 4 | 崩溃测试的预期状态 | 第 6 项 `drafting`，其余 `pending` | 第 6、7 项都是 `drafting` | 第 6 项出错时释放信号量，第 7 项马上开始，然后被 TaskGroup 取消在调用中间。进程被杀也会留下这个状态，`recover()` 把两项都退回 `pending` |

**试过、没有采用的提示词规则（2026-10-26）：** 5 份 dev 合同上，A1-F 把"没提到"判成"相反"的次数多于 B0-F（nda-1：定义按内容列举信息、没有要求标注，模型判成 Contradiction）。在 A1 的 `SYSTEM` 里加了 1 条规则："A missing requirement is not a Contradiction ... the label is NotMentioned"（run `a1-f_dev_20261025-202007`，`prompt_hash` `4d81334e6aa1`）。准确率不变（0.753），nda-1 的 2 个错误还在。两次 A1 运行之间本来就有 4 项不同。用户决定撤掉这条规则，A1 的标签定义和 B0 保持一致，`prompt_hash` 回到 `39fa1266b5e6`。

**施工时的其他决定（施工文档没有写明的细节）：**

- `FakeLLM.create()` 每次先 `await asyncio.sleep(0)`，和真实的网络调用一样让出事件循环。不让出时，并发的各项一项接一项跑完，崩溃没法取消它们。
- `llm._tokens()` 改名为 `token_counts()`：Agent 写 `model_call` 事件时要用它。事件里的 `usage.input` 是未命中缓存的输入 token，和 `Usage.input_tokens` 一致。
- 服务器中断（`ModelInterrupted`）的那次调用也计费，也写 `model_call` 事件，然后再抛出异常。
- 工具的 JSON Schema 去掉 Pydantic 自动加的 `title`，减少 token。
- `resume_review()` 先检查方法。不是 A1 时抛出 `NotImplementedError`，审核状态不变。
- 只回文字、`content` 为空的回复，发回时 `content` 写成空字符串。有 `tool_calls` 的回复不加 `content`。
