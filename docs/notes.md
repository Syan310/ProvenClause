# ProvenClause 笔记

阶段 0 的笔记：论文基线、数据统计、5 份 NDA 的阅读笔记。日期 2026-10-21。

---

## 1. 原论文的基线和评估设置

**论文：** Koreeda & Manning, "ContractNLI: A Dataset for Document-level Natural Language Inference for Contracts", Findings of EMNLP 2021（arXiv 2110.01799）。数字来自论文 Table 4 和 Table 3。

### 1.1 基线数字（test 全集，123 份）

| 系统 | 准确率 | F1（相反） | F1（支持） | 证据 mAP |
|---|---|---|---|---|
| 多数类（每个检查问题输出最常见的结论） | .674 | .083 | .428 | — |
| Doc TF-IDF + SVM | .733 | .197 | .641 | — |
| Span NLI BERT-base | .838 | .287 | .765 | .885 |
| **Span NLI BERT-large** | **.875** | **.357** | **.834** | **.922** |
| DeBERTa v2 xlarge + CUAD 预训练（论文最好的结果） | .892 | .405 | .859 | .936 |

详细设计第 8.1 节的"BERT-large 准确率约 87.5%、相反类 F1 约 0.36"已经核对，数字正确。

### 1.2 评估设置：和我们的设置不同的 4 点

1. **平均方式。** 论文先在合同上做微平均，再在检查问题上做宏平均。
   - 准确率：每个检查问题的条目数都相同，所以宏平均的准确率等于全部条目合在一起的准确率。两种算法结果一样。
   - F1（相反）：两种算法结果不一样。论文对 17 个检查问题分别算 F1，再取平均。test 里有 7 个检查问题没有一条"相反"（nda-8、10、11、12、13、15、18），另有 4 个只有 1–4 条。没有"相反"时 F1 怎么算，论文没有说清楚。
   - **2026-10-21 已决定：** 主表用合并计算；另算论文式宏平均，只用于和论文比较（见技术决策记录第 3.2 节）。
2. **测试集不同。** 论文用 test 全集 123 份；我们的正式实验用 test-40。和论文比较时，要用 A2-F 在 test 全集上的补充结果。
3. **结果是多次运行的平均。** 论文跑 10 组超参数，报告 dev 上最好的 3 个模型的平均值。
4. **证据指标不同。** 论文的 mAP 和 P@R80 是排序指标，要求每个句子有一个分数。我们的系统输出一个句子集合，所以算精确率和召回率。两者不能直接比较。

### 1.3 其他有用的事实

- **标注方式不同。** train 只核对了标注过的句子，没有通读全文。dev 大部分由主标注人一个人标注。test 大部分由主标注人通读全文补标。所以 train 的证据可能漏标较多。
- **给出正确证据后，"相反"类的 F1 明显上升。** Table 6 只做"支持 / 相反"二分类：BERT-base 的 F1（相反）从 .490 升到 .657。这支持我们"先找对证据，再下结论"的设计。
- **论文附录没有公开每个检查问题的标注说明。** 附录只列出了 17 个检查问题。所以 `nda_17.yaml` 的 `guidance` 是根据检查问题的原文和 train 里的证据例子写的。
- **编号不同。** 论文 Table 10 的编号是 1–17，数据集的 ID 是 nda-1 到 nda-20（中间有空号）。nda-2 的名字在数据集里写成 "None-inclusion"，论文里写成 "Non-inclusion"。`nda_17.yaml` 照抄数据集。

---

## 2. 数据统计

命令：`uv run python -m provenclause.eval.stats`

| 划分 | 合同数 | 平均字符数 | 最长字符数 | 平均句子数 | 支持 | 相反 | 没提到 |
|---|---|---|---|---|---|---|---|
| train | 423 | 11,049 | 54,571 | 77.8 | 3,530 | 841 | 2,820 |
| dev | 61 | 12,095 | 32,359 | 83.6 | 519 | 95 | 423 |
| test | 123 | 11,218 | 41,779 | 81.8 | 968 | 220 | 903 |
| test-40 | 40 | 13,848 | 41,779 | 95.4 | 294 | 66 | 320 |

**test-40 的偏差（种子 20261021）：**
- test-40 的合同比 test 平均长 23%（13,848 对 11,218 个字符）。test 全集里最长的合同也在 test-40 里。所以正式实验的成本要按这个长度估算。
- 40 份里有 14 份的长度在 test 的前 25%。随机抽样的期望值是 10 份。难例 H5（长合同）的样本会多一些。
- 格式分布：search-pdf 27、sec-text 8、sec-html 5。test 全集的比例是 76 : 24 : 23。sec-html 偏少。
- 结论分布：支持 43%、相反 9.7%、没提到 47%。test 全集是 46%、10.5%、43%。
- 论文的划分按格式分层，我们的抽样没有分层。这些偏差在 README 里写明，不重新抽样。重新抽样等于看着 test 挑数据。

---

## 3. 5 份 dev NDA 的阅读笔记

读的 5 份合同覆盖 3 种格式，长度从最短到最长：

| ID | 格式 | 字符数 | 句子数 |
|---|---|---|---|
| 456 | sec-text | 2,270（dev 最短） | 20 |
| 411 | sec-text | 9,425 | 46 |
| 3 | search-pdf | 11,142 | 97 |
| 563 | sec-html | 13,208 | 80 |
| 64 | search-pdf | 32,359（dev 最长） | 255 |

### 3.1 章节编号

- **见到的写法：** `1.`–`22.`（5 份都有）；`A.`、`B.`；`(a)`、`(i)`、`(A)`、`(1)`；只有右括号的 `a)`、`1)`（合同 3）；没有编号的标题，例如 `WHEREAS`、`RECITALS:`、`General`。
- **没见到：** `1.1`、`Article III`、罗马数字的顶级章节。阶段 2 的正则表达式仍要支持它们，但测试用例要从 train 里找。
- **标题和正文的切分：**
  - 单独一行的标题是一个独立的句子，例如 411 S9 `1. Non-Competition.`。
  - 同一行的标题和正文在同一个句子里，例如 64 S44 `A. NO REPRESENTATION OR WARRANTIES: The Receiving Party agrees…`。
  - 标题可能被切成 2 个句子，例如 64 S115–S116 `7. ENVIRONMENTAL` / `DOCUMENTATION AND ISSUES:`。
- **列表的切分：** 每个列表项是一个句子。引导句是单独的句子。列表后面的半句话会接在最后一项里，例如 411 S12 `(b)` 里有整条竞业限制规则。

### 3.2 定义

- **写法：** `"X" shall mean`、`"X" means`、`(the "Agreement")`、`(hereinafter referred to as the "Agreement")`、`(collectively, "Representatives")`、`is referred to as "Buyer"`、`is hereinafter collectively referred to as the "Information"`。
- **反向定义：** `The term "Confidential Information" does not include`（411 S18）。这类句子是例外列表的开头，L2 要同时把它当成定义和例外。
- **引号：** 有的合同用直引号，有的用弯引号，合同 64 两种都有。正则表达式必须同时匹配 `"` 和 `“”`。
- **每份合同的定义数（人工数，误差约 ±2）：** 456 有 0 个，411 有 12 个，3 有 11 个，563 有 9 个，64 有 24 个。合同 456 的定义在另一页上，不在文本里。
- **陷阱：** 563 S65 用了 "Receiving Party"，但合同只定义了 "Recipient"。`lookup_definition` 查不到时要返回"未定义"，不能报错。

### 3.3 交叉引用

- **能指回本合同章节的写法：** `this Section 1`、`Section 2(a) hereof`、`Except as provided in Section 3`、`Paragraph 4E`（数字和字母之间没有分隔符）、`Paragraph 6K above`。
- **指向合同外面、不能解析的写法：**
  - `Section 12 of the Securities Exchange Act of 1934`（411 S15）：法律条文。正则表达式会误匹配，要排除 `of the ... Act` 这类写法。
  - `Section A of this Attachment A`（456 S2）：这一节不在文本里。
  - `as that term is defined in the Merger Agreement`（411 S30）：另一份文件。
- **没有编号的引用：** `the foregoing`、`the immediately preceding sentence`、`this paragraph`、`as provided above`、`hereof`。L2 不处理它们，只处理有编号的引用。
- **没见到：** `clause 4.2`、`Article II`。

### 3.4 例外条款

- **详细设计列出的词：** `except`、`provided that` / `provided, however`、`unless`、`notwithstanding`、`other than` 都出现了。`save that` 没有出现。`subject to` 有时不是例外（3 S13、563 S65）。
- **列表外的例外写法（建议阶段 2 讨论是否加入 L2）：**
  - `shall not apply to`、`does not include`、`is not subject to the terms of this Agreement if`
  - `Nothing contained in ... shall be deemed to prevent`、`However, this paragraph shall not prohibit`
  - `without the prior written consent of`、`Absent ... prior written consent`
  - `so long as`、`to the extent necessary`、`only if`
- **例外的位置：**
  - 短的例外和主规则在同一个句子里，例如 563 S23 `except Recipient's Representatives`。
  - 排除列表在单独的章节里，每项一个句子，例如合同 3 的第 2 节 "Exclusions"（S26–S31）。
  - 有的例外在下一个句子或下一个编号段落里，例如 411 S13–S15。所以 L2 要检查"同一章节的其他句子"，只检查证据句子本身不够。

### 3.5 文本提取的问题

- **dev 61 份里都没有：** 断行连字符（`employ-ees`）、连字（ﬁ）、句子里的换行、单独的页码行。没有被句子覆盖的文字只有换行符。
- **有：**
  - 多余的字母：64 S29 `SuchF information`、64 S80 `theF Indemnifying Party`。
  - 填空用的下划线和省略号：64 S130–S241、3 S3。
  - 页眉页脚混在正文里：456 S0 `(page 2.)`、563 S0 `Exhibit 99(d)(7)`、64 S242–S254。
  - 句子从半句话开始：PDF 的换行变成了句子边界，例如 64 S4–S5 `This Commercial` / `CONFIDENTIALITY AND NON-DISCLOSURE AGREEMENT…`。所以章节识别不能假设每个句子都从句首开始。

### 3.6 "相反"的表达方式

| 合同 | "相反"的检查项（证据句子） |
|---|---|
| 456 | 无 |
| 411 | nda-2 [17]、nda-5 [17]、nda-7 [17] |
| 3 | nda-2 [17] |
| 563 | nda-1 [12, 13]、nda-2 [12–15] |
| 64 | 无 |

- **411 的 nda-5 和 nda-7（与员工、第三方共享）：** S17 说股东"不得向任何人透露"保密信息。合同里的例外只有定义的排除项（S18–S23）和法律强制披露（S25–S26），没有允许共享的例外。**这里的"相反"来自"没有例外"，而不是来自某个例外词。** L2 的关键词规则发现不了这种情况。
- **nda-2（只包括技术信息）：** 定义里列出了商业信息，所以结论是"相反"。例如 3 S17：`"Confidential Information" shall mean scientific, research, technical or business information`。

---

## 4. 要你确认的事项

1. **`risk_label_for_recipient` 是我选的。** 规则是：哪个结论对接收方更不利，就选哪个。例如 nda-1 选"相反"，因为不需要标记的信息也算保密信息，接收方要保护的范围更大。它只影响报告的排序，不影响打分。请看一遍 [`checklists/nda_17.yaml`](../checklists/nda_17.yaml)。
2. **F1（相反）的算法**（第 1.2 节第 1 点）：2026-10-21 已决定。
3. **L2 的例外词列表**（第 3.4 节）：在阶段 4 讨论。阶段 0 不改详细设计。
