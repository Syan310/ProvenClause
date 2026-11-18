# ProvenClause

有证据、可核验的合同审核 Agent。

- **做什么：** 按一份检查清单逐项审核 NDA。每条结论都引用合同原文，并且经过三层核对；不确定时返回"需人工确认"。
- **核心研究问题：** 核对器能不能让便宜的模型（DeepSeek Flash）达到贵模型（DeepSeek V4 Pro）的可靠性？
- **评估：** ContractNLI（607 份 NDA，17 个检查问题，CC BY 4.0）。
- **开发周期：** 2026-10-21 至 2026-11-03。阶段 0（骨架和数据）和阶段 1（B0 基线 + 打分）已完成。B0-F 在 dev 上的准确率 0.804，每份合同 $0.0052。阶段 2（存储底座：数据库、状态机、结论账本、章节/定义/交叉引用识别）已完成。阶段 3（逐项 Agent：工具、Agent 循环、断点恢复）已完成。A1-F 在 dev 上的准确率 0.795，和 B0-F 没有显著差别；证据召回率从 0.552 升到 0.708；每份合同 $0.0097。

## 文档

| 文档 | 内容 |
|---|---|
| [详细设计](docs/ProvenClause_详细设计.md) | 定位、12 个模块（7 个迁移 + 5 个新写）、核心研究问题的实验设计、评估、14 天计划、未来扩展 |
| [实现路线](docs/ProvenClause_实现路线.md) | 从 MVP（B0 基线 + 打分）开始，分 9 个阶段逐步加模块；每个阶段的任务、验收标准和要先定的决策 |
| [技术决策记录](docs/ProvenClause_技术决策记录.md) | 已核实的 API 约束；每个模块写之前讨论技术选择，结论记在这里 |
| [笔记](docs/notes.md) | 论文基线和评估设置；数据统计；5 份 NDA 的阅读笔记 |
| [阶段 1 施工文档](docs/ProvenClause_阶段1施工.md) | 给施工 Agent 的说明：文件、接口、测试、检查点和验收标准 |
| [阶段 2 施工文档](docs/ProvenClause_阶段2施工.md) | 存储底座：数据库、状态机、结论账本、章节/定义/交叉引用识别 |
| [阶段 3 施工文档](docs/ProvenClause_阶段3施工.md) | 逐项 Agent：工具、Agent 循环、断点恢复 |

## 运行

1. 安装 [uv](https://docs.astral.sh/uv/)。项目用 Python 3.13。运行依赖在 `pyproject.toml`：`openai`、`pydantic`、`python-dotenv`、`pyyaml`、`rank-bm25`（阶段 3 的 BM25 检索，连带 numpy）。`uv run` 自动安装它们。
2. 下载 ContractNLI 官方数据包 [contract-nli.zip](https://stanfordnlp.github.io/contract-nli/resources/contract-nli.zip)（65 MB），解压到 `data/`。解压后应有 `data/contract-nli/test.json`。
3. 运行测试：

```bash
uv run pytest
```

4. 打印数据统计：

```bash
uv run python -m provenclause.eval.stats
```

5. 打印一份合同和它的标准答案：

```bash
uv run python -m provenclause.eval.stats --split dev --contract <contract_id>
```

6. 打印 N1 规则的统计（章节号、定义术语、交叉引用；只用 train 或 dev）：

```bash
uv run python -m provenclause.eval.stats --n1 --split dev
```

### 用模型审核和打分

> **注意：** 下面的命令调用 DeepSeek API，要花钱。

1. 复制 `.env.example`，改名为 `.env`。在 `DEEPSEEK_API_KEY=` 后面填上密钥。`.env` 不提交到 Git。
2. 先审核 dev 的前 5 份合同：

```bash
uv run python -m provenclause.eval run --config configs/b0-f.yaml --split dev --limit 5
```

   用逐项 Agent（A1-F）审核时，换成 A1 的配置：

```bash
uv run python -m provenclause.eval run --config configs/a1-f.yaml --split dev --limit 5
```

3. 结果在 `runs/<run_id>/`：`manifest.json`、`findings.jsonl`、`errors.jsonl`、`metrics.json`。A1 另有 `reviews.db`：每项的状态、结论、每次模型调用和工具调用（已脱敏）。命令结束时打印结果表；A1 另外打印步数和工具调用次数。
4. 运行中断或者有合同出错时，在同一个目录上继续：

```bash
uv run python -m provenclause.eval run --config configs/b0-f.yaml --split dev --resume runs/<run_id>
```

5. 运行真实调用的测试（B0 审核 1 份合同；A1 审核 1 份合同的前 2 项）：

```bash
uv run pytest -m live
```

- `--workers N`：同时审核 N 份合同，默认 4。
- `test40` 和 `test` 要另加 `--allow-test`。它们只在阶段 7 使用。

## 目录

```
ProvenClause/
├── checklists/nda_17.yaml        # N2：17 个检查问题
├── configs/                      # 实验配置：b0-f.yaml、a1-f.yaml（Flash）；b0-p.yaml、a1-p.yaml（Pro）
├── data/contract-nli/            # 官方数据包（不提交）
├── runs/                         # 评估结果（不提交）
├── src/provenclause/
│   ├── models.py                 # 共用数据结构
│   ├── parse.py                  # N1：读取 ContractNLI；带句子编号的全文；章节、定义、交叉引用
│   ├── checklist.py              # N2：读取并校验检查清单
│   ├── db.py                     # M2：SQLite，6 张表和读写函数
│   ├── fsm.py                    # M3：审核和检查项的状态跳转
│   ├── ledger.py                 # N3：结论的登记规则和只写一次
│   ├── llm.py                    # 调用 DeepSeek（JSON 输出和工具调用）；截断后加倍重做；价格和成本；FakeLLM
│   ├── baseline.py               # B0：一次调用审完 17 项
│   ├── tools.py                  # M5：BM25 检索等 5 个工具、参数校验、超时、日志脱敏
│   ├── agent.py                  # M4：A1 的逐项 Agent 循环、预算、并发
│   ├── recovery.py               # M6：进程中断后恢复未完成的检查项
│   ├── review.py                 # review_contract()：B0、A1、A2 的统一入口
│   └── eval/
│       ├── __main__.py           # 命令行：eval run
│       ├── run.py                # 评估运行器：并发、断点续跑
│       ├── metrics.py            # 指标和结果表
│       ├── dataset.py            # 标准答案
│       ├── sample.py             # 抽取 test-40
│       ├── stats.py              # 数据统计；--n1：N1 规则的统计
│       └── splits/test40.txt     # test-40 的合同编号
├── tests/
└── docs/                         # 设计文档和笔记
```

其他模块按详细设计第 10.2 节，在后面的阶段加入。
