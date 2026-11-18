# ProvenClause：有证据、可核验的合同审核 Agent — 详细设计

版本：v1.0（2026-10-21，精简版）
开发周期：2026-10-21 至 2026-11-03（14 天）
项目名：ProvenClause 是暂定名。2026-10-21 在 GitHub 上搜索，没有同名仓库。做完以后可以再改。

---

## 0. 项目定位

### 一句话说明

ProvenClause 按一份检查清单审核合同。每一条结论都附合同原文证据，并且经过程序和独立模型的核对。不确定的时候，它会说"需要人工确认"，而不是猜一个答案。

**核心卖点：** 不是"读完合同给个总结"，而是"每个结论都能指回原文，并且知道自己什么时候不可靠"。

### 要解决的问题

1. 审合同很费时间。律师、法务和银行的合规人员每天都要逐条检查合同条款。
2. 直接让 LLM 读合同会出 3 类错：
   - **编造条款：** 引用了合同里根本没有的句子。
   - **漏看例外条款：** 合同写着"可以 X，但 Y 情况除外"，模型只看到前半句。
   - **把"没提到"当成"允许"或"禁止"。**
3. 这 3 类错都很难被用户发现，除非用户自己回到原文逐句核对。

### 核心研究问题

> **核对器能不能让便宜的模型，达到贵模型的可靠性？**

- 便宜模型：DeepSeek Flash（`deepseek-flash`，非高峰时段输入 $0.15 / 百万 token）。
- 贵模型：DeepSeek V4 Pro（`deepseek-v4-pro`，非高峰时段输入 $0.66 / 百万 token）。输入价格约为 Flash 的 4.4 倍，输出价格约为 3.3 倍。
- 2026-10-21 从 Claude 换成 DeepSeek，见技术决策记录第 3.3 节。
- 同一套 Agent 和核对器，换不同的模型，比较准确率、"已核实"答案的精确率和每份合同的成本。

**为什么问这个问题：**
- 无论结果是什么，结论都能讲清楚。追上了，就说明核对器可以替代昂贵的模型；没追上，就能具体说明差在哪一类错误上。
- 它对应香港银行的真实顾虑：成本，以及在本地部署小模型。

### 为什么用 NDA 和 ContractNLI

| 理由 | 说明 |
|---|---|
| 有标准答案 | ContractNLI 有 607 份 NDA，每份都标注了 17 个检查问题的结论和证据句子 |
| 结构和设计一致 | 数据集的格式就是"检查问题 + 三种结论 + 证据句子"，不需要改造 |
| 评估不需要 LLM 打分 | 结论是 3 选 1，证据是句子编号，可以直接用程序算分 |
| 有公认的难点 | 数据集作者指出，合同里"用例外条款表达否定"是主要难点 |
| 许可证宽松 | CC BY 4.0，可以用于公开项目，注明来源即可 |
| 学习门槛低 | 17 个检查问题都是白话英文，不需要法律背景 |

### 和 Salus 实习的关系

| 经历 | 让什么变得可靠 | 方法 |
|---|---|---|
| Salus 实习 | Agent 的**执行** | 故障注入、幂等写入、崩溃恢复、权限控制 |
| ProvenClause | Agent 的**结论** | 证据账本、三层核对器、风险-覆盖分析、模型对比实验 |

运行时（Agent 循环、工具系统、状态机、断点恢复）沿用训练营的后端设计。这样大部分时间可以花在审核的准确性和评估上。**项目的重点是结论的可靠性和实验结果，不是运行时。**

---

## 1. 目标、非目标和体量原则

### 目标

1. 按 17 条检查清单审核一份 NDA，每一条给出结论：**支持 / 相反 / 没提到**，或者**需人工确认**。
2. 每个"支持"或"相反"的结论都附证据句子编号和原文。
3. 三层核对：引用是否真实、规则检查（例外条款、定义、交叉引用）、独立模型复核。
4. 回答核心研究问题：在同一测试集上比较 6 组配置。
5. 做难例分析、风险-覆盖曲线和错误注入测试。
6. 一次审核是一次 Run，有 API、数据库记录、状态机和断点恢复。

### 非目标（不做）

- 不给法律意见。报告首页写明"辅助阅读工具，不构成法律意见"。
- 不做合同修改建议和谈判建议。
- 不做网页界面、登录和多用户。
- 不做队列、实时推送、Docker 和监控（见第 15 节"未来扩展"）。
- 不训练模型。

### 体量原则

1. **每个组件都要有一个实验数字支撑。** 没有数字支撑的组件，放进"未来扩展"。
2. **作者必须理解每个核心模块。** 代码由 Agent 编写，作者不逐行审。对每个核心模块（M4 Agent 循环、M6 断点恢复、N3 结论账本、N4 核对器、M7 评估），作者要能回答 3 个问题：它做什么、为什么这样设计、考虑过哪些其他方案。代码量控制在约 2,000–2,500 行（含测试，粗略估计）。代码越少，越容易讲清楚。
3. **先测量，再加复杂度。**
4. **开发阶段默认用便宜模型。** Flash 的费用很低，可以随便试。贵模型可以在 dev 上跑参考结果，正式实验只在最后跑（2026-10-21 修改，见实现路线第 0 节第 4 条）。

---

## 2. 用户场景和输出示例

### 2.1 用户流程

1. 调用 `POST /reviews`，上传一份 NDA（或者指定数据集里的一份合同），选择检查清单 `nda_17` 和模型配置。
2. 系统返回 `review_id`，在后台开始审核。
3. 调用 `GET /reviews/{id}` 查看进度：17 项里完成了几项。
4. 完成以后，调用 `GET /reviews/{id}/report` 拿到报告。

### 2.2 报告中的一条结论

```json
{
  "item_id": "nda-5",
  "item_name": "Sharing with employees",
  "hypothesis": "Receiving Party may share some Confidential Information with some of Receiving Party's employees.",
  "label": "Entailment",
  "label_zh": "合同支持这个说法",
  "outcome": "verified",
  "evidence": [
    {"span_id": 23, "section": "3.2", "text": "Recipient may disclose Confidential Information to its employees who have a need to know ..."}
  ],
  "rationale": "第 3.2 条允许向有必要知道的员工披露。",
  "support_score": 92,
  "checks": {"L1": "pass", "L2": "pass", "L3": "pass"}
}
```

`outcome` 有 3 种：`verified`（已核实）、`violated`（核对不通过，需人工确认）、`abstained`（弃权，需人工确认）。

---

## 3. 总体架构

### 3.1 一次审核的一生

```
M1 创建   POST /reviews  →  返回 review_id
   │
M2 落库   reviews 表新增一行（状态 = created）；保存合同
   │
   │      （排队：不做。直接启动后台任务）
   │
M3 状态   created → parsing
   │      N1 合同解析：拆成句子（span）、章节、定义，写入 spans 表
   │
M3 状态   parsing → reviewing
   │
M4 循环 + M5 工具   17 个检查项，最多 4 个同时进行。每一项：
   │      N2 读检查清单 → Agent 读合同、查定义、跟交叉引用
   │      → N3 登记结论（写入 findings 表）
   │      → N4 核对器 L1 / L2 / L3 → 不通过就退回重做（最多 2 次）
   │      ← M6 恢复：每一项结束就存档。中断后重启，只做没完成的项
   │
M3 状态   reviewing → completed（或 failed）
   │      N5 生成报告
   │
   │      （实时推送：不做。进度事件写入 events 表，用 GET 查询）
   │
M7 回流   评估：ContractNLI 跑分；错误案例加入回归测试集
```

### 3.2 模块清单（12 个）

**从训练营后端框架迁移的 7 个模块：**

| 编号 | 模块 | 对应训练营阶段 | 体量 |
|---|---|---|---|
| M1 | API 接口 | 01 创建 | 最简：3 个接口 |
| M2 | 数据库（SQLite） | 02 落库 | 最简：6 张小表 |
| M3 | 状态机 | 04 状态机 | 最简：审核级 + 检查项级，约 80 行 |
| M4 | Agent 循环（含预算、重试） | 05 执行 | 核心 |
| M5 | 工具系统（含超时、日志脱敏） | 06 调工具 | 核心 |
| M6 | 断点恢复 | 08 恢复 | 最简：按检查项存档 |
| M7 | 评估与回归测试 | 10 回流 | 核心 |

**合同审核新写的 5 个模块：**

| 编号 | 模块 | 干什么 |
|---|---|---|
| N1 | 合同解析 | 合同 → 句子（span）、章节、定义 |
| N2 | 检查清单 | 17 个检查问题的配置文件 |
| N3 | 结论账本 | 每个结论绑定证据句子，写入数据库 |
| N4 | 核对器 | L1 引用核对、L2 规则检查、L3 独立模型复核、三种结果 |
| N5 | 报告生成 | 按风险和结果整理报告 |

**不做的 3 个阶段：** 03 排队、07 推事件、09 上线加固。09 里的预算、超时和日志脱敏，并进 M4 和 M5。

### 3.3 设计原则

1. **结论必须有证据。** "支持"和"相反"必须引用至少 1 个句子；"没提到"不能引用句子。
2. **先程序核对，再模型核对。** 程序核对便宜、确定、可解释。
3. **核对器看不到 Agent 的推理过程。** 它只看检查问题、结论和证据，以及程序自动补充的上下文。
4. **不确定就弃权。** 宁可说"需人工确认"，也不给一个可能错的结论。

---

## 4. 数据

### 4.1 ContractNLI（主评估集）

2026-10-21 下载官方数据包后核对的事实：

| 项目 | 数值 |
|---|---|
| 合同总数 | 607 份 NDA |
| 划分 | train 423 / dev 61 / test 123 |
| 合同长度 | 平均约 11,000 个字符，最长 54,571 个字符（按 4 个字符 ≈ 1 个 token 估算，平均约 2,800 token） |
| 每份合同的句子数（span） | 平均约 77–81 个 |
| 每份合同的检查问题 | 17 个，所有合同相同 |
| test 的结论分布 | 支持 968，没提到 903，相反 220（共 123 × 17 = 2,091 条） |
| test 中"相反"最多的检查项 | nda-2（88 条）、nda-1（36）、nda-20（30）、nda-7（25）、nda-17（25） |
| 许可证 | CC BY 4.0，另附 Hitachi America 的使用条款 |
| 下载地址 | https://stanfordnlp.github.io/contract-nli/ |

**格式：** 每份合同有全文 `text` 和句子边界 `spans`。每个检查问题的标注是 `choice`（Entailment / Contradiction / NotMentioned）和证据句子编号 `spans`。

**注意：** Hugging Face 上的 `kiddothe2b/contract-nli` 是"句子级"改写版（只有一段文字和一个问题），不是整份合同。**要用官方数据包。**

**重要发现：NDA 很短。** 平均约 2,800 token，最长约 14,000 token，整份合同可以直接放进上下文。所以 Agent 的价值不在"找到相关段落"，而在：
1. 逐项审核，每一项都专注于一个问题。
2. 查定义、跟交叉引用。
3. 被核对器退回以后，按反馈重新检查。

B0 基线（整份合同一次性审完 17 项）就是用来检验这个判断的。

### 4.2 17 个检查问题

| ID | 名称 | 检查问题（原文） |
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

ID 不连续（没有 nda-6、9、14），这是数据集原本的编号。

### 4.3 数据的使用方式

| 数据 | 用途 | 规模 |
|---|---|---|
| dev（61 份） | 开发和调提示词；错误注入的材料；回归测试集的来源 | 61 × 17 = 1,037 条 |
| **test-40**（从 test 里用固定随机种子抽 40 份） | **核心研究问题的正式实验**，6 组配置都跑这一份 | 40 × 17 = 680 条 |
| test 全集（123 份） | 只跑便宜模型的配置，作为补充结果 | 2,091 条 |
| train | 不用于训练。只用来挑选提示词里的少量示例 | — |
| 演示合同（2–3 份） | 公开的 NDA 或租房合同 PDF，用来演示 PDF 解析和 API | — |

**规则：** 调提示词只看 dev。test 只在第 11 天跑正式实验。

---

## 5. 新写的模块

### N1 合同解析

**输入两种来源，输出同一种结构：**

| 来源 | 做法 |
|---|---|
| ContractNLI | 直接读取 `text` 和 `spans`，不需要重新切句子。这样证据编号和标准答案一致 |
| 演示用的 PDF | PyMuPDF 提取文字 → 按句子和列表项切成 span |

**两种来源都要做的（阶段 2 实现，`annotate_contract()`，`load_contractnli()` 默认调用）：**
1. **章节识别：** 用正则表达式识别 `1.`、`1.1`、`1.01`、`ARTICLE 1 -`、`Article II`、`Section 5.` 这类句首编号。`Span.section` 记最深一层的"数字点号"编号，例如 `"2"`、`"2.06"`；`ARTICLE 1` 记为 `"1"`，罗马数字保留原样（`"II"`）。`(a)`、`(i)`、`a)` 这类括号编号不记。标题后面的句子沿用这个编号，直到下一个标题。编号要按顺序出现：等于上一个、加 1、从 1 重新开始，或者向前跳但被下一个标题确认。合同里有数字标题时，`A.`、`B.` 不作为章节。
2. **定义识别：** 识别 `"Confidential Information" means ...`、`... (the "Agreement")`、`is referred to as "Buyer"` 这类句子，直引号、弯引号、单引号都匹配。一个句子定义的术语存进 `Span.defines`（按出现顺序，去重）；`is_definition == bool(defines)`。`definition_spans(contract, term)` 按术语找定义句子，不分大小写，查不到返回空列表。
3. **交叉引用识别：** 识别 `Section 5`、`Clause 1.2(a)`、`Paragraph 4E`、`Article II` 这类引用，规范化成章节编号（`"5"`、`"1.2"`、`"4"`、`"II"`）。编号后面跟 `of ...`（不是 `of this ...`，例如 `of this Agreement`、`of this letter agreement`）时，引用指向合同外面，被排除，例如 `Section 12 of the Securities Exchange Act`。`Span.refs` 只存 `section_spans()` 能在本合同找到的编号。

**输出：** 写入 `spans` 表，每个 span 有：`span_id`、起止位置、文字、章节号、是否为定义、定义的术语、引用的章节。

### N2 检查清单

一个 YAML 文件 `checklists/nda_17.yaml`，每一项包括：

```yaml
- id: nda-5
  name: Sharing with employees
  hypothesis: "Receiving Party may share some Confidential Information with some of Receiving Party's employees."
  guidance: >
    Entailment if the contract permits disclosure to employees (even with conditions such as need-to-know).
    Contradiction if it forbids disclosure to all employees.
    NotMentioned if employees are not addressed.
  risk_label_for_recipient: Contradiction   # 对接收方来说，哪种结论风险高
```

- `guidance` 用自己的话写。论文附录没有公开标注说明，所以根据检查问题原文和 train 里的证据例子写（见 [notes.md](notes.md) 第 1.3 节）。
- 换一种合同，只换这个文件，代码不改。
- **所以核心代码不写死具体的检查项**（2026-10-25 加入）。阶段 3 的 Agent 提示词、阶段 4 的 L2 规则、阶段 5 的报告，都只能用 `ChecklistItem` 的字段（例如 `guidance`、`risk_label_for_recipient`），不能写"如果是 nda-2 就……"这种只对某一题有效的规则。某一题需要的特别说明，写进它的 `guidance`。只有评估代码（`eval/`）可以写死 `nda_17`，因为标准答案只覆盖这 17 项。这一条让第 15 节的"用户上传检查清单"以后只需要加一个入口。

### N3 结论账本

每个检查项的每次尝试写一条记录（`findings` 表）：

| 字段 | 说明 |
|---|---|
| review_id, item_id, attempt | 哪次审核、哪个检查项、第几次尝试 |
| label | Entailment / Contradiction / NotMentioned |
| span_ids | 证据句子编号（列表） |
| rationale | 一句话说明理由 |
| reason | 弃权的原因，和 `Finding.reason` 对应 |
| self_confidence | Agent 自己报告的把握（0–100），用于和核对器的分数比较 |
| checks | 三层核对的结果，JSON：`{"L1": ..., "L2": ..., "L3": ...}`，和 `Finding.checks` 对应（2026-10-23 决定，代替 `l1`、`l2`、`l3` 三列） |
| support_score | L3 给出的支持度（0–100） |
| outcome | verified / violated / abstained |
| operation_id | 可读字符串 `"{review_id}:{item_id}:{attempt}"`，有唯一索引。重复写入不会产生两条记录 |
| finding_id, created_at | 自增编号和写入时间 |

**登记规则**（纯函数 `validate_finding()`，一次返回全部问题，用英文写，阶段 4 的 L1 也调用它）：
1. "支持"和"相反"必须有至少 1 个 `span_id`；"没提到"必须为空。不符合时 `record_finding` 直接返回错误。
2. `span_id` 必须存在于这份合同。
3. `span_ids` 里不能有重复的编号（阶段 2 加的输入检查）。
4. `self_confidence` 不是空值时，必须在 0–100 之间（阶段 2 加的输入检查）。

**登记过程（`record_finding()`）：** 不合法时写一条 `finding_rejected` 事件，`findings` 表不写，所以不占用 `operation_id`。合法时用 `INSERT ... ON CONFLICT(operation_id) DO NOTHING`：重复写入返回已有的那一条，`duplicate=True`，不覆盖。`record_finding()` 不改检查项的状态。

### N4 核对器

#### L1 引用核对（程序）

1. 每个 `span_id` 都存在于这份合同吗？
2. 结论和证据数量的规则符合吗（见 N3 登记规则）？
3. 如果 `rationale` 里引用了原文，引用的文字真的出现在那个 span 里吗？

#### L2 规则检查（程序）

L2 不直接判对错，而是**找出 L3 必须看的上下文**，并标记风险：

| 检查 | 规则 | 结果 |
|---|---|---|
| 例外条款 | 证据句子，或者同一章节里的其他句子，含有 `except`、`provided that`、`unless`、`notwithstanding`、`subject to`、`other than`、`save that` | 把这些句子加入 L3 的输入，并标记 `has_exception` |
| 定义 | 证据句子里用到了大写开头的已定义术语（例如 Confidential Information） | 把这个术语的定义句子加入 L3 的输入 |
| 交叉引用 | 证据句子引用了其他章节（例如 "Section 5"） | 把那个章节的句子加入 L3 的输入 |
| "没提到"的覆盖 | 结论是"没提到"时，用检查问题的关键词在全文检索 | 检索到的高分句子加入 L3 的输入，让 L3 确认真的没提到 |

#### L3 独立模型复核

- **输入：** 检查问题、`guidance`、Agent 的结论、证据句子，以及 L2 补充的上下文（例外条款、定义、交叉引用、检索到的句子）。
- **不输入：** Agent 的推理过程和对话历史。
- **输出（结构化）：**

```json
{"verdict": "pass" | "fail" | "uncertain", "support_score": 0-100, "issue": "一句话说明问题"}
```

- **默认模型：** 和 Agent 用同一个模型。这样"便宜模型 + 核对器"的总成本仍然便宜。可选配置 C5 让 L3 改用贵模型（见第 7 节）。

#### 三种结果

| 结果 | 条件 |
|---|---|
| **verified（已核实）** | L1、L3 通过；L2 没有未处理的标记 |
| **violated（核对不通过）** | 重试 2 次后，L1 或 L3 仍不通过 |
| **abstained（弃权）** | L3 返回 `uncertain`；或者超出预算；或者模型拒绝回答（原因为 `refusal`） |

#### 失败处理

1. L1 或 L3 不通过 → 把具体问题返回给 Agent，例如"span 41 含有例外条款 'except as required by law'，你的结论没有考虑它"。
2. 最多重试 2 次。
3. 仍不通过 → 结果为 `violated`，在报告里标记"需人工确认"，附上原因。

### N5 报告生成

- 输出 JSON 和 Markdown 两种格式。
- 排序：先列"需人工确认"的项，再按 `risk_label_for_recipient` 列出高风险项，最后列其他项。
- 每一条都附证据原文和章节号。
- 报告首页写明：辅助阅读工具，不构成法律意见。

---

## 6. 迁移的模块

以下按训练营框架的思路**重写精简版**，不直接复制训练营或 Salus 的代码（见第 16 节）。

### M1 API 接口（FastAPI）

| 接口 | 作用 |
|---|---|
| `POST /reviews` | 上传 PDF，或者指定数据集里的 `contract_id`；参数包括 `checklist_id` 和 `model_config`。返回 `202` 和 `review_id`，用后台任务开始审核 |
| `GET /reviews/{id}` | 返回状态和进度（17 项里各状态的数量） |
| `GET /reviews/{id}/report` | 返回报告（JSON 或 Markdown） |

评估脚本**不走 API**，直接调用核心函数，这样更快、更容易调试。API 用于演示，也证明系统是一个服务。

### M2 数据库（SQLite，6 张表）

阶段 2 实现了 6 张表（2026-10-23 决定加 `review_items`，见技术决策记录第 3.5 节）。表结构见 `src/provenclause/db.py`：

```
contracts    (contract_id, source, title, text, n_spans, created_at)
spans        (contract_id, span_id, char_start, char_end, text, section, is_definition, defines, refs)
reviews      (review_id, contract_id, checklist_id, model_config, status, created_at, updated_at, error)
review_items (review_id, item_id, status, attempt, retries, updated_at)   -- 检查项的状态，M3 和 M6 用
findings     (finding_id, operation_id, review_id, item_id, attempt, label, span_ids, rationale,
              reason, self_confidence, checks, support_score, outcome, created_at)   -- 见 N3，operation_id 唯一
events       (review_id, seq, ts, type, item_id, payload)   -- 审计日志：状态变化、登记；阶段 3 加模型调用、工具调用
```

- 全部是 `STRICT` 表。打开 WAL 和 `foreign_keys`。表结构版本记在 `PRAGMA user_version`（现在是 1）。
- `spans` 的起止列叫 `char_start`、`char_end`，因为 `END` 是 SQL 关键字。
- 列表和字典（`defines`、`refs`、`span_ids`、`checks`、`payload`）存成 JSON 文本。时间是 UTC 的 ISO 8601 文本。
- 用标准库 `sqlite3`，同步调用，自动提交。多条语句一起提交时用 `transaction()`（`BEGIN IMMEDIATE`）。
- 事件类型（阶段 2）：`review_created`、`review_status`、`item_status`、`finding_recorded`、`finding_rejected`。

### M3 状态机

**审核级：**

```
created → parsing → reviewing → completed
   └───────┴──────────┴──────→ failed
```

**检查项级：**

```
pending → drafting → verifying → verified
             ↑           │    ├→ violated
             └─ 重试 ────┘    └→ abstained

drafting → abstained                 （阶段 2 加）
drafting、verifying → pending（恢复） （阶段 2 加）
```

| 从 | 可以到 |
|---|---|
| pending | drafting |
| drafting | verifying、abstained、pending |
| verifying | verified、violated、abstained、drafting、pending |
| verified、violated、abstained | （终止状态） |

- 阶段 2 加了 3 条边：`drafting → abstained`（超出预算、拒绝回答发生在登记之前）；`drafting → pending` 和 `verifying → pending`（M6 恢复）。A1 没有核对器，也走 `drafting → verifying → verified`。
- **计数：** `verifying → drafting`（核对不通过，重试）让 `attempt` 和 `retries` 都加 1。退回 `pending`（恢复）只让 `attempt` 加 1。其他跳转不变。新的 `attempt` 让下一次登记得到新的 `operation_id`。"最多重试 2 次"由调用方看 `retries`。
- 两个函数 `transition_review()`、`transition_item()` 先查跳转表，不合法就抛出 `InvalidTransition`。合法时用"比较后再写"：`UPDATE ... WHERE status = :from`。改了 0 行说明实际状态不是 `from`，也抛出 `InvalidTransition`。
- 改状态和写 `events` 在同一个事务里，每次跳转写 1 条事件。

### M4 Agent 循环

阶段 3 实现（`agent.py`），选型见技术决策记录第 3.6 节。

- 每个检查项运行一次 Agent 循环：调用模型 → 执行工具 → 结果交回模型，直到 `record_finding` 登记成功。
- **合同全文放进 system 提示词**，每个句子前加编号，例如 `[S23] Recipient may disclose ...`。system = 通用说明 + 合同全文，17 项完全相同，不放时间戳、`review_id` 和检查项编号。DeepSeek 自动缓存这个共用前缀。检查问题和 `guidance` 放在 user 消息里。
- **并发和缓存预热：** 第 1 项先单独跑，让合同进入缓存。其余 16 项再用 `asyncio.Semaphore(4)` 最多同时跑 4 项。
- **提醒（C2）：** 思考模式下 `tool_choice` 不能用 `required`。模型只回文字、没有调用工具时，追加一条 user 消息，要求调用 `record_finding`。提醒算 1 步。
- **步数（C3）：** 1 次模型调用算 1 步，每项最多 `max_steps`（8）步。一次回复里的多个工具调用按顺序执行，算 1 步。`record_finding` 成功后停止，同一回复里后面的调用不执行。登记被拒绝时，错误作为工具结果交回模型，步数照算。超出步数：`abstained`，原因 `budget_exceeded`。
- **每次审核的总预算（C4）：** `max_review_output_tokens` 限制一次审核累计的输出 token（A1-F 的配置是 100,000）。每次调用模型之前检查。用完以后，没有结束的项都是 `abstained`，原因 `budget_exceeded`。事件里用 `kind` 区分 `steps` 和 `review_tokens`。
- **截断（C6）：** `finish_reason` 为 `length` 时，用 2 倍的 `max_tokens` 重做同一次调用 1 次。被截断的回复不进对话记录，费用照算。重做算在同一步里。总预算用完时不重做。第 2 次还截断：`abstained`，原因 `max_tokens`。
- **拒绝回答：** `content_filter` → `abstained`，原因 `refusal`。
- **`failed` 和 `abstained` 的区别：** `abstained` 是模型没有给出可用的答案（拒绝、截断、超出预算），只影响这一项。`failed` 是基础设施的问题：服务器中断（`insufficient_system_resource`、`aborted`），或者 SDK 重试 4 次以后仍然失败（429、5xx、网络错误）。这时整个审核记为 `failed`，其他项被取消，异常交给调用方。基础设施的故障不算进覆盖率。
- 每次模型调用和工具调用都写进 `events`（结论、思考内容、工具参数和结果、用量），写之前脱敏。每项的运行统计（步数、各工具调用次数、工具错误、被拒绝的登记、提醒、截断重做）放在 `Finding.stats`，评估从 `findings.jsonl` 计算。

### M5 工具系统（5 个只读工具）

| 工具 | 输入 | 输出 | 用途 |
|---|---|---|---|
| `search_spans` | query, top_k=8 | 句子编号 + 文字 + 分数 | BM25 检索。用于确认"没提到"、找分散的相关条款 |
| `read_spans` | span_ids, window=1 | 这些句子和前后各 1 句 | 看上下文 |
| `lookup_definition` | term | 定义句子 | 查术语定义 |
| `get_section` | section | 整个章节的句子 | 跟交叉引用 |
| `record_finding` | label, span_ids, rationale, self_confidence | 成功，或者错误原因 | 登记结论（N3），然后进入核对器 |

阶段 3 实现（`tools.py`），选型见技术决策记录第 3.6 节。

- **结果格式：** 文本，每句一行 `[S23] (§2.06) ...`，和 system 提示词里的合同一样以 `[S{id}]` 开头。没有章节号时省略 `(§...)`。出错时以 `ERROR:` 开头。
- **上限：** `top_k` 1–20；`window` 0–3；`read_spans` 最多 10 个编号；`get_section` 最多返回 40 句，后面写一行提示剩下多少句。
- **`search_spans`：** `rank-bm25` 的 `BM25Okapi`。分词：小写、`[a-z0-9]+`、去掉 31 个英文停用词，不做词干还原。每份合同建 1 次索引，17 项共用。只返回分数大于 0 的句子。
- **`lookup_definition`：** 先查原词；查不到时再试去掉开头的 `the`，再试单复数。查不到时列出合同里已定义的术语（最多 40 个）。
- **`get_section`：** `Section 2(a)` 找到 `2`，`2.9` 不存在时退回 `2`。查不到时列出合同的章节号。
- **参数校验：** 每个工具一个 Pydantic 参数模型（`extra="forbid"`），JSON Schema 由同一个模型生成。不打开 DeepSeek 的 strict 模式。参数不是 JSON、缺字段、多字段、超出范围、编号不存在、工具名不存在：错误作为工具结果交回模型，并计数。`record_finding` 没有 `item_id` 参数，模型不能登记别的项。
- **超时：** 4 个只读工具用 `asyncio.wait_for(asyncio.to_thread(...), 10)`。线程停不下来，超时以后在后台跑完，结果丢掉。`record_finding` 要用 SQLite 连接，在事件循环的线程里直接运行。
- **脱敏：** 写进 `events` 的全部文本都脱敏，包括模型回复、思考内容、工具参数和工具结果。替换邮箱、中国身份证号、美国 SSN、电话号码（美国写法、带 `+` 的国际写法、中国手机号）。模型看到的是原文。dev 的 61 份合同里命中 7 个邮箱和 24 个电话，人工检查都是真实的联系方式。

### M6 断点恢复

阶段 3 实现（`recovery.py`），选型见技术决策记录第 3.6 节。

- 每个检查项一进入最终状态（verified / violated / abstained）就写入数据库。放弃的项也写一行占位结论（`NotMentioned`、没有引用、写明原因）。
- 服务重启时，`recover()`：
  1. 找出状态为 `created`、`parsing` 或 `reviewing` 的审核。`created` 也要恢复，否则建好就崩溃的审核永远不会开始。
  2. 已经结束的检查项跳过。
  3. 停在 `drafting` 或 `verifying` 的检查项退回 `pending`，`attempt + 1`，`retries` 不变。新的登记因此有新的 `operation_id`，不会和旧结论冲突。
  4. 只改数据库，不调用模型。连续调用 2 次，第 2 次什么都不改。
- `resume_review()` 把审核补到 `reviewing`，再只跑 `pending` 的项。最终结果取每项当前 `attempt` 的结论。
- **结论已登记、状态还没改就崩溃：** 这一项停在 `drafting`，也退回重做。旧 `attempt` 的结论留在表里，不使用。
- **什么算崩溃：** 审核循环只把 `Exception` 当成失败（审核记为 `failed`）。`KeyboardInterrupt`、`CancelledError` 这类 `BaseException` 不改状态，和进程被杀一样，审核留在 `reviewing`，由 M6 恢复。
- **评估续跑不用 M6：** `eval run --resume` 遇到做了一半的合同，用新的 `review_id` 整份重做。原因：成本和延迟要按一次完整的审核计算，这是阶段 7 主表的数字。M6 由阶段 5 的 API 在启动时调用。
- 测试方法（FakeLLM）：跑完 5 项后抛出 `BaseException` 模拟进程被杀，恢复后检查 17 项完整、没有重复的结论。

### M7 评估与回归测试

- `eval run --config <配置> --split dev|test40|test`：跑评估，结果写入 `runs/<run_id>/`，包括指标、每条结论和 `manifest.json`（git commit、模型、effort、提示词哈希、随机种子）。
- **回归测试集：** dev 上每次发现的典型错误（例如漏看例外条款的案例），加上第 8.5 节的错误注入用例，存进 `eval/regression.jsonl`。
- **回归门禁：** 改了提示词或代码以后，用 Flash 在回归测试集上跑一次。和上一次的基准结果比较：已核实精确率下降超过 2 个百分点，或者"相反"类 F1 下降超过 3 个百分点，就判为"退步"，不采用这次修改。用 Flash 跑一次只需要几美分。

---

## 7. 核心研究问题：实验设计

### 7.1 配置

| 配置 | 方法 | 模型 | 回答什么问题 |
|---|---|---|---|
| B0-F | 整份合同一次审完 17 项，单次调用，无工具、无核对 | DeepSeek Flash | 最简单的做法效果如何？ |
| B0-P | 同上 | DeepSeek V4 Pro | 同上，贵模型 |
| A1-F | 逐项 Agent + 工具，无核对器 | DeepSeek Flash | 逐项审核带来多少提升？ |
| A1-P | 同上 | DeepSeek V4 Pro | 同上，贵模型 |
| **A2-F** | 逐项 Agent + 工具 + 三层核对器 | DeepSeek Flash | **核对器能把便宜模型提高多少？** |
| **A2-P** | 同上 | DeepSeek V4 Pro | 核对器对贵模型还有没有用？ |
| C5（可选） | Agent 用 Flash，L3 用 Pro | 混合 | 便宜的模型干活，贵的模型检查，是否最划算？ |

配置名的后缀：`-F` 是 Flash，`-P` 是 Pro。6 组核心配置都在 **test-40** 上跑。A2-F 另外在 test 全集上跑一次。

### 7.2 控制变量

- 提示词、工具、检查清单和核对规则，6 组完全相同。只换模型和"是否开启核对器"。
- 两个模型都显式设置 `effort: high`，并记录在 `manifest.json` 里。
- 随机种子固定。

### 7.3 要报告的结论

1. **主表：** 6 组配置的准确率、"相反"类 F1、已核实精确率、覆盖率、每份合同的成本、P50 / P95 延迟。
2. **核心对比：** A2-F 和 A1-P 比较，也就是"便宜模型 + 核对器"对"贵模型无核对"。
3. **成本-质量图：** 横轴是每份合同的成本，纵轴是已核实精确率，6 个配置各一个点。
4. **差在哪里：** 如果 A2-F 没追上 A2-P，用第 8.3 节的难例分类说明差在哪一类。

---

## 8. 评估方案

### 8.1 指标（不需要 LLM 打分）

| 指标 | 定义 |
|---|---|
| 准确率 | 结论和标准答案一致的比例（全部 680 条） |
| 各类 F1 | 支持、相反、没提到分别计算。**"相反"类 F1 是重点** |
| 证据精确率 / 召回率 | 只对标准答案是"支持"或"相反"的条目计算：Agent 引用的句子里，有多少在标准证据里；标准证据里，有多少被引用了 |
| 已核实精确率 | 结果为 verified 的条目里，结论正确的比例。**最重要的指标** |
| 覆盖率 | 结果为 verified 的条目 ÷ 全部条目 |
| 错误接受数 | verified 但结论错误的条目数。每一条都写进错误分析 |
| 拦截率 / 误拦率 | 核对器拦住的错误结论 ÷ 全部错误结论；核对器拦住的正确结论 ÷ 全部正确结论 |
| 成本和延迟 | 每份合同的美元成本；每份合同的 P50 / P95 秒数 |

**已有基线：**
- ContractNLI 原论文：微调过的 BERT-large，准确率 .875，"相反"类 F1 .357（2026-10-21 已核对原论文 Table 4）。评估设置和我们的不同，见 [notes.md](notes.md) 第 1 节。
- 2026 年的一篇小模型对比论文：GPT-4o-mini 约 76.6%（任务格式可能不同）。

### 8.2 风险-覆盖曲线

**目的：** 说明"如果要求 X% 的精确率，系统能覆盖多少条"。

**做法：**
1. 每条结论有两个分数：Agent 自报的 `self_confidence`，以及 L3 给出的 `support_score`。
2. 按分数从高到低排序。只保留前 10%、20%……100%，分别计算准确率。
3. 画两条曲线：横轴是覆盖率，纵轴是准确率。一条按 `self_confidence` 排序，一条按 `support_score` 排序。
4. A2-F 和 A2-P 各画一张。

**要回答的问题：**
- 核对器的分数比模型自报的把握更可靠吗？（如果更可靠，L3 那条曲线会更高。）
- 要求 95% 的准确率时，便宜模型和贵模型分别能覆盖多少条？

### 8.3 难例分析

把 test-40 的条目分成 5 类难例，每一类分别报告 6 组配置的准确率：

| 编号 | 难例类别 | 怎么挑出来 |
|---|---|---|
| H1 | 标准答案是"相反" | 标签 |
| H2 | 证据里有例外条款 | 标准证据句子含有 L2 的例外关键词 |
| H3 | 标准答案是"没提到" | 标签。重点看错误地判成"支持"或"相反"的情况 |
| H4 | nda-2（"只包括技术信息"） | test 中"相反"最多的检查项 |
| H5 | 长合同 | 字符数排在前 25% 的合同 |

### 8.4 错误分类

每次评估后，把错误归入以下类别，写进 README：

1. 漏看例外条款
2. 定义理解错误
3. 没跟交叉引用
4. 把"没提到"当成"支持"或"相反"
5. 证据找错（结论对，但证据不对）
6. 检查问题理解错误
7. 标准答案本身有问题

### 8.5 错误注入测试

**做法：** 从 dev 里取 A2-F 判对的结论，用程序改坏，生成约 40 个"坏结论"，再混入同样数量的正确结论，让核对器检查。

| 编号 | 错误 | 怎么改 | 应该由哪一层拦住 |
|---|---|---|---|
| E1 | 伪造证据 | 把一个 `span_id` 换成不存在的编号，或者改动引用的文字 | L1 |
| E2 | 结论翻转 | 证据不变，把"支持"改成"相反"，或者反过来 | L3 |
| E3 | 证据替换 | 换成同一份合同里含有相同关键词、但不支持结论的句子 | L3 |
| E4 | 删除例外 | 结论依赖例外条款时，从证据里去掉例外句子，并把结论改成相反的 | L2 + L3 |

**报告：** 每种错误的拦截率，以及正确结论的误拦率。

---

## 9. 模型和成本

### 9.1 模型

| 角色 | 便宜配置 | 贵配置 |
|---|---|---|
| Agent | `deepseek-flash`（DeepSeek-V4.1-Flash） | `deepseek-v4-pro`（DeepSeek-V4-Pro） |
| L3 核对 | 和 Agent 相同（C5 例外） | 和 Agent 相同 |

价格（DeepSeek 官方，2026-10-21 核对，美元 / 百万 token，非高峰 / 高峰）：

| 模型 | 输入（未命中缓存） | 输入（命中缓存） | 输出 |
|---|---|---|---|
| `deepseek-flash` | 0.15 / 0.30 | 0.003 / 0.006 | 0.60 / 1.20 |
| `deepseek-v4-pro` | 0.66 / 1.32 | 0.022 / 0.044 | 1.98 / 3.96 |

- 高峰时段是 UTC 周一到周五的 01:00–04:00 和 06:00–10:00。高峰价格是非高峰的 2 倍。
- 写入缓存不收费。
- 两个模型的上下文都是 100 万 token，最多输出 38.4 万 token。

### 9.2 成本估算（2026-10-21 用 B0-F、2026-10-26 用 A1-F 的实测值更新）

**实测（B0-F，dev 61 份，run `b0-f_dev_20261021-154250`，非高峰时段）：**

| 项目 | 数值 |
|---|---|
| 总成本 | $0.320，每份合同 $0.0052（最高 $0.0106） |
| 输入 token | 未命中缓存 229,346；命中缓存 58,368（20%） |
| 输出 token | 474,985，其中思考 427,714（90%） |
| 成本构成 | 输出约 89%，输入约 11% |
| 延迟 | P50 34.4 秒，P95 55.8 秒；最大输出 17,649 token |

**实测（B0-P，dev 61 份，run `b0-p_dev_20261021-162737`，非高峰时段）：** 总成本 $1.152，每份合同 $0.0189（最高 $0.0359）。输出 503,152 token，其中思考 442,697。最大输出 17,247 token。P50 56.0 秒，P95 106.1 秒。

**"Pro 的思考 token 和 Flash 差不多"这个假设成立。** 在同样 61 份合同上，Pro 的输出 token 是 Flash 的 1.06 倍，思考 token 是 1.04 倍。所以 Pro 的成本是 Flash 的 3.60 倍：比只按输出价格算的 3.3 倍略高，因为 Pro 的输入价格是 Flash 的 4.4 倍，输出也多 6%。下表的 Pro 估算用 3.6 倍。

**实测（A1-F，dev 61 份，run `a1-f_dev_20261026-003459`，2026-10-26，全部在高峰时段）：** 总成本 $1.177，按非高峰价折算 $0.589（价格正好是一半），每份合同 $0.0097，是 B0-F 的 1.84 倍。每份合同调用 37.0 次。输入：未命中缓存 1,460,429，命中缓存 9,151,739（86%）。输出 570,307 token，其中思考 308,591（54%）。非高峰价的构成：输出 58%，未命中缓存的输入 37%，命中缓存的输入 5%。一份合同的输出最多 19,436 token。P50 26.1 秒，P95 55.1 秒。

**A1 的 Pro 倍数用 3.9，不用 3.6。** A1 未命中缓存的输入占成本的 37%（B0 只占 11%），而 Pro 的输入价格是 Flash 的 4.4 倍。用 A1-F 的 token 数按 Pro 的价格算，成本是 3.9 倍。这假设 Pro 的 token 数和 Flash 相同（B0 上是 1.06 倍）。

**估算（非高峰价；高峰时段乘以 2）：** test-40 的合同比 dev 平均长 14%（13,848 对 12,095 个字符，见 [notes.md](notes.md) 第 2 节），所以 test-40 的估算乘以 1.15。A2 的核对和重试还没有实测，按 A1 × 1.5 估算，阶段 4 实测后更新。

| 实验 | 估算 | 依据 |
|---|---|---|
| 开发阶段：B0-F 跑一次 dev | 约 $0.32 | 实测 |
| 开发阶段：B0-P 跑一次 dev（参考结果） | 约 $1.15 | 实测 |
| 开发阶段：A1-F 跑一次 dev | 约 $0.59 | 实测（高峰时段 $1.18） |
| B0-F（test-40） | 约 $0.25 | $0.0052 × 40 × 1.15 |
| B0-P（test-40） | 约 $0.9 | B0-F × 3.6 |
| A1-F（test-40） | 约 $0.45 | $0.0097 × 40 × 1.15 |
| A1-P（test-40） | 约 $1.7 | A1-F × 3.9 |
| A2-F（test-40，含核对和重试） | 约 $0.7 | A1-F × 1.5 |
| A2-P（test-40） | 约 $2.6 | A2-F × 3.9 |
| A2-F（test 全集 123 份） | 约 $2.1 | A2-F（test-40）× 123 / 40 |
| **正式实验合计** | **约 $9** | 按 2 倍余量准备 $18。2026-10-21 的估算是 $32，当时假设 A1 每项输出约 4,000 token；实测约 550 token |

省钱的方法：
1. 开发阶段默认用 Flash。Pro 可以在 dev 上跑参考结果（2026-10-21 修改，见实现路线第 0 节第 4 条）。
2. 正式实验在第 11 天跑，每组配置各跑一次。
3. 合同全文在 17 项之间不变，放在提示词开头作为共用前缀。DeepSeek 自动缓存这个前缀。用 `usage.cache_read_input_tokens`（DeepSeek 的 `prompt_cache_hit_tokens`）确认缓存生效。
4. 正式实验尽量在非高峰时段跑。高峰时段的价格是 2 倍。
5. 如果预算紧张，把 test-40 缩小到 test-20。这由你决定。

### 9.3 API 使用要点

- 两个模型默认打开思考模式。思考模式下 `temperature` 不起作用。
- `reasoning_effort` 接受 `low`、`high`、`max`。DeepSeek 把 `medium` 和 `xhigh` 当成 `high`。默认值是 `high`。**配置文件显式设置 `effort: high`**，并记录在 `manifest.json` 里。
- DeepSeek 没有按 schema 强制的结构化输出。B0 和 L3 用 JSON 模式（`response_format={"type": "json_object"}`），它保证输出是合法 JSON。schema 写在提示词里，再用 Pydantic 校验。校验失败时，结果为 `abstained`，原因为 `parse_error`。
- 每次调用都检查 `finish_reason`：
  - `content_filter`：当作拒绝回答，结果为 `abstained`，原因为 `refusal`。
  - `length`：用 2 倍的 `max_tokens` 重做 1 次。第 2 次还截断，结果为 `abstained`，原因为 `max_tokens`。B0 和 Agent 用同一个函数 `call_with_length_retry()`。
  - `insufficient_system_resource`、`aborted`：服务器中断请求，抛出 `ModelInterrupted`，审核记为 `failed`。
- 工具调用（阶段 3 已实现并在 dev 上实测，技术决策记录第 1.2 节、第 3.6 节）：
  - 思考模式下 `tool_choice` 不能用 `required` 或指定工具（返回 400），所以不传 `tool_choice`（默认 `auto`）。模型只回文字时，Agent 追加一条提醒。
  - strict 模式是 Beta，不打开，`base_url` 不变。工具参数由 Pydantic 校验。
  - 用工具时，前面每一轮的 `reasoning_content` 都要发回，否则返回 400。Agent 用 `message.model_dump(exclude_none=True)` 把回复追加回对话，它保留 `reasoning_content` 和 `tool_calls`。`test_llm.py` 有用例锁定这一点。

---

## 10. 技术栈和目录

### 10.1 技术栈

| 层 | 选择 |
|---|---|
| 语言 | Python 3.13（2026-10-21 决定，见技术决策记录 F1） |
| LLM | `openai` Python SDK（`openai>=3.36,<4`），调用 DeepSeek 的 OpenAI 兼容 API（`base_url="https://api.deepseek.com"`）；key 存在 `.env` 的 `DEEPSEEK_API_KEY` 里 |
| API | FastAPI |
| 数据库 | SQLite（标准库 `sqlite3`） |
| PDF 解析 | PyMuPDF（只用于演示合同） |
| 检索 | `rank-bm25` |
| 数据校验 | Pydantic v2 |
| 配置文件 | YAML（PyYAML 的 `safe_load`） |
| 测试 | pytest |
| 画图 | matplotlib（风险-覆盖曲线、成本-质量图） |
| 环境 | `uv`；`.env` 存 API key，不提交到 Git |

### 10.2 目录

```
ProvenClause/
├── checklists/
│   └── nda_17.yaml               # N2
├── configs/                      # 实验配置：b0-f.yaml、a2-p.yaml 等
├── data/                         # 不提交到 Git，README 写下载步骤
│   ├── contract-nli/             # 官方数据包
│   └── demo/                     # 演示合同 PDF
├── src/provenclause/
│   ├── models.py                 # 共用数据结构
│   ├── llm.py                    # 唯一调用模型的地方：DeepSeek、成本、FakeLLM
│   ├── baseline.py               # B0
│   ├── review.py                 # review_contract()：B0、A1、A2 的统一入口
│   ├── api.py                    # M1
│   ├── db.py                     # M2：建表、读写
│   ├── fsm.py                    # M3
│   ├── agent.py                  # M4：循环、预算、并发
│   ├── tools.py                  # M5
│   ├── recovery.py               # M6
│   ├── parse.py                  # N1
│   ├── checklist.py              # N2
│   ├── ledger.py                 # N3
│   ├── verify.py                 # N4：L1、L2、L3、三种结果
│   ├── report.py                 # N5
│   └── eval/                     # M7：运行评估、指标、难例、曲线、错误注入、回归门禁
├── tests/
├── runs/                         # 每次评估的结果和日志（不提交）
├── docs/
└── README.md
```

模块用单个文件。某个文件超过约 400 行时再拆。

---

## 11. 14 天计划

| 天 | 日期 | 任务 | 当天交付物 |
|---|---|---|---|
| 1 | 10/21 | 建仓库；下载 ContractNLI 官方数据包；写数据读取代码；统计数据；手动读 5 份 NDA 和 17 个检查问题；抽出 test-40；**核对原论文的基线数字** | 数据统计；test-40 名单；17 个问题的笔记 |
| 2 | 10/22 | 指标模块（准确率、各类 F1、证据精确率和召回率）；B0 基线（整份合同一次审完）；用 Flash 在 dev 上跑；**实测成本** | 第一张结果表 |
| 3 | 10/23 | 数据库（6 张表）+ 状态机 + 检查清单 YAML + 结论账本 | 单元测试通过 |
| 4 | 10/24 | Agent 循环 + 5 个工具 + 预算 + 审计日志；1 个检查项跑通 | 1 项能出结论 |
| 5 | 10/25 | 17 项并发编排 + 断点恢复；A1-F 在 dev 上跑 | A1-F 结果 |
| 6 | 10/26 | **缓冲日 1**；dev 的错误分析；确定难例类别 | 错误分类表 |
| 7 | 10/27 | 核对器 L1 + L2（例外、定义、交叉引用、"没提到"的覆盖）+ 单元测试 | 核对器测试通过 |
| 8 | 10/28 | 核对器 L3 + 重试 + 三种结果；A2-F 在 dev 上跑。**核心系统完成** | A2-F 结果；拦截率、误拦率 |
| 9 | 10/29 | API（3 个接口）；演示用 PDF 的解析；端到端跑一次演示合同；断点恢复测试（中途杀掉进程） | API 能用；恢复测试通过 |
| 10 | 10/30 | 错误注入（4 种，约 40 个用例）；回归测试集 + 回归门禁 | 错误注入结果表 |
| 11 | 10/31 | **正式实验**：6 组配置跑 test-40；A2-F 跑 test 全集；可选 C5 | 原始结果 |
| 12 | 11/01 | 分析：主表、成本-质量图、风险-覆盖曲线、难例表、错误分类 | 全部图表 |
| 13 | 11/02 | README：架构图、证据示例、结果表、图表、错误分析、相关工作 | 完整 README |
| 14 | 11/03 | 推送到 GitHub；**缓冲日 2** | 公开仓库 |

**进度落后时，按这个顺序砍掉：**
1. 可选配置 C5。
2. A2-F 在 test 全集上的补充实验。
3. 演示用 PDF 的解析（API 只支持数据集里的合同）。
4. 错误注入缩到 2 种（E2 和 E4）。
5. 正式实验缩到 test-20。
6. **不要砍：** 结论账本、三层核对器、6 组核心配置、风险-覆盖曲线和难例分析。它们是项目的核心。

---

## 12. 风险与对策

| 风险 | 对策 |
|---|---|
| 大模型直接做就有很高的准确率，提升空间小 | 重点报告"相反"类 F1、已核实精确率和难例，而不只报告总准确率。核心问题是"便宜模型能不能追上"，这个问题和总分高低无关 |
| L3 和 Agent 用同一个模型，犯同样的错 | L3 看不到 Agent 的推理；L2 补充上下文；C5 测试换成贵模型的效果 |
| L3 误拦太多，覆盖率太低 | 报告误拦率和风险-覆盖曲线；调提示词只看 dev |
| 标准答案本身有错 | 错误分类里单独列一类，在 README 里说明 |
| Pro 费用超出预算 | 第 2 天实测；开发默认用 Flash，Pro 只在 dev 上跑参考结果；正式实验各跑一次，尽量在非高峰时段跑；必要时缩到 test-20 |
| 演示用 PDF 的切句结果和数据集不一致 | 演示只展示功能，不进入评估 |
| 时间不够 | 按第 11 节的顺序砍功能 |

---

## 15. 未来扩展

| 扩展 | 为什么现在不做 | 什么情况下做 |
|---|---|---|
| 任务队列和 worker（训练营 03） | 只有一个用户 | 多用户、高并发 |
| 实时推送进度（训练营 07） | 没有界面 | 做网页界面 |
| Docker、监控、限流（训练营 09） | 不上线 | 部署给真实用户 |
| 每个检查项派一个子 Agent | 现在用 asyncio 并发就够了 | 检查项需要不同的工具或模型 |
| CUAD 商业合同（510 份，41 类条款） | 先在一个数据集上做扎实 | 证明方法能迁移到长合同 |
| 香港中英双语租约 | 需要自己标注 | 想做香港本地化 |
| 开源模型（Qwen / DeepSeek）做 L3 | 先用一个模型系列跑通 | 说明可以本地部署 |
| 合同修改建议 | 涉及法律意见 | 有法律专业人员参与 |
| 网页界面 | 投入产出比低 | 有真实用户试用 |
| 用户上传自己的检查清单（Playbook） | 用户的检查项没有标准答案，系统测不出准确率；`guidance` 会原样进入提示词，要先做提示词注入、文件大小和题目数量的检查。核心代码已经只认 `ChecklistItem` 的 5 个字段，`reviews` 表有 `checklist_id` 列，所以以后只要加上传入口、存储和检查（2026-10-25 加入） | 方法在 17 个有标准答案的检查项上验证完成以后；报告里要写明"这份清单的效果没有经过评估" |

---

## 16. 代码来源说明

- 训练营材料和 Salus 实习的代码，都可能有版权或保密限制。**不要直接复制。**
- 按同样的思路重写精简版，代码由 Agent 编写。核心循环约 300–500 行。作者要能讲清楚它做什么、为什么这样设计（见第 1 节体量原则 2）。
- README 里写明数据来源：ContractNLI（Koreeda & Manning, 2021），CC BY 4.0。
