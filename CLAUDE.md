# ProvenClause 项目规则

ProvenClause 是一个合同审核 Agent。每条结论都引用合同原文，并且经过三层核对。设计和计划见：

- [docs/ProvenClause_详细设计.md](docs/ProvenClause_详细设计.md)
- [docs/ProvenClause_实现路线.md](docs/ProvenClause_实现路线.md)
- [docs/ProvenClause_技术决策记录.md](docs/ProvenClause_技术决策记录.md)

## 运行

技术栈见技术决策记录第 2.3 节。目录和数据下载步骤见 [README.md](README.md)。

```bash
uv run pytest
uv run python -m provenclause.eval.stats
uv run python -m provenclause.eval run --config configs/b0-f.yaml --split dev --limit 5
```

- **模型是 DeepSeek，不是 Claude**（2026-10-21 决定，见技术决策记录第 1.2、3.3 节）。用 `openai` SDK 调用原生接口，密钥是 `.env` 里的 `DEEPSEEK_API_KEY`。
- **真实调用要花钱。** 运行 `eval run` 或 `pytest -m live` 之前，先告诉用户调用次数和估计成本，等用户同意。
- 数据在 `data/contract-nli/`，不提交。数据缺失时，依赖数据的测试会跳过，不会失败。
- **Windows 编码：** 控制台是 GBK。打印合同文本的脚本要先调用 `sys.stdout.reconfigure(encoding="utf-8")`。`python -I` 会忽略 `PYTHONUTF8` 环境变量。
- **test 只在阶段 7 使用。** 调试和调提示词只看 dev。
- **讲解文档只放在本地。** `docs/讲解/`（stage-explainer 生成的讲解文档）在 `.gitignore` 里，不提交，不推送。

## 当前进度

阶段 0、阶段 1 已完成（2026-10-21）。B0-F 在 dev 上的准确率 0.804，每份合同 $0.0052（结果表见实现路线第 3 节"阶段 1"）。阶段 2 已完成（2026-10-24，tag `v0.2`）：`db.py`、`fsm.py`、`ledger.py` 和 `parse.py` 的规则部分，离线测试 250 个。dev 上 84.2% 的句子有章节号，每份合同平均 7.3 个定义术语（结果见实现路线第 3 节"阶段 2"，规则调整见技术决策记录第 3.5 节）。阶段 3 已完成（2026-10-26）：`tools.py`、`agent.py`、`recovery.py`，离线测试 355 个。A1-F 在 dev 上的准确率 0.795，和 B0-F 没有显著差别（95% 置信区间 [−0.024, +0.004]）；证据召回率 0.552 → 0.708；每份合同 $0.0097（非高峰价，B0-F 的 1.84 倍）；缓存命中率 0.862（结果见实现路线第 3 节"阶段 3"，规则调整见技术决策记录第 3.6 节）。下一步：缓冲日的错误分析，然后讨论阶段 4 的选型。各阶段的任务见实现路线。

## 工作流程

1. **先讨论技术，再写模块。** 逐阶段选型：做完一个阶段，再讨论下一个阶段的技术。讨论时按技术决策记录第 2 节的步骤做，并遵守第 2.3 节的全局约定。用户决定以后才写代码。不要提前替用户定下还没讨论的模块。
2. **Agent 施工，用户理解。** 代码由 Agent 编写，用户不逐行审。每完成一个核心模块（M4、M6、N3、N4、M7），在本地讲解文档 `docs/讲解/` 里给这个模块加一节讲解（不提交）：它做什么、为什么这样设计、考虑过哪些其他方案、可能有哪些追问。
3. **推送前运行 neat-freak。** 代码写完、文档也更新以后，在推送到 GitHub 之前，运行 `neat-freak` skill（轻量路径）。只改文档、没有改代码的推送不需要运行。
4. **删除要确认。** neat-freak 列出的删除候选，用户确认之前一个都不删。
