# ProvenClause project rules

ProvenClause is a contract review agent. Every finding cites the contract text and goes through three-layer verification. For the design and the plan, see:

- [docs/design.md](docs/design.md)
- [docs/roadmap.md](docs/roadmap.md)
- [docs/decisions.md](docs/decisions.md)

## Run

For the tech stack, see Section 2.3 of the technical decisions. For the directory layout and the data download steps, see [README.md](README.md).

```bash
uv run pytest
uv run python -m provenclause.eval.stats
uv run python -m provenclause.eval run --config configs/b0-f.yaml --split dev --limit 5
```

- **The model is DeepSeek, not Claude** (decided 2026-10-21; see Sections 1.2 and 3.3 of the technical decisions). Use the `openai` SDK to call the native API. The key is `DEEPSEEK_API_KEY` in `.env`.
- **Real calls cost money.** Before you run `eval run` or `pytest -m live`, tell the user the number of calls and the estimated cost, and wait for the user to agree.
- The data is in `data/contract-nli/` and is not committed. When the data is missing, tests that need it are skipped. They do not fail.
- **Windows encoding:** The console uses GBK. A script that prints contract text must first call `sys.stdout.reconfigure(encoding="utf-8")`. `python -I` ignores the `PYTHONUTF8` environment variable.
- **Use test only in Stage 7.** For debugging and prompt tuning, look only at dev.
- **Explainer docs stay local.** `docs/explainers/` (the explainer docs that stage-explainer writes) is in `.gitignore`. Do not commit or push it.

## Current progress

Stage 0 and Stage 1 are complete (2026-10-21). B0-F has an accuracy of 0.804 on dev, at $0.0052 per contract (results table in roadmap Section 3, "Stage 1"). Stage 2 is complete (2026-10-24, tag `v0.2`): `db.py`, `fsm.py`, `ledger.py`, and the rules part of `parse.py`, with 250 offline tests. On dev, 84.2% of sentences have a section number, and each contract has 7.3 defined terms on average (results in roadmap Section 3, "Stage 2"; rule changes in Section 3.5 of the technical decisions). Stage 3 is complete (2026-10-26): `tools.py`, `agent.py`, `recovery.py`, with 355 offline tests. A1-F has an accuracy of 0.795 on dev, with no significant difference from B0-F (95% confidence interval [−0.024, +0.004]). Evidence recall goes from 0.552 to 0.708. The cost is $0.0097 per contract (off-peak price, 1.84 times B0-F). The cache hit rate is 0.862 (results in roadmap Section 3, "Stage 3"; rule changes in Section 3.6 of the technical decisions). Next step: error analysis on the buffer day, then a discussion of the technology choices for Stage 4. For the tasks of each stage, see the roadmap.

## Workflow

1. **Discuss the technology first, then write the module.** Choose the technology one stage at a time: finish a stage, then discuss the technology for the next stage. Follow the steps in Section 2 of the technical decisions and the global conventions in Section 2.3. Write code only after the user decides. Do not decide in advance for modules that the user has not discussed yet.
2. **The agent builds, the user understands.** The agent writes the code, and the user does not review it line by line. After each core module (M4, M6, N3, N4, M7) is complete, add a section for that module to the local explainer docs in `docs/explainers/` (not committed): what it does, why it has this design, which other options were considered, and which follow-up questions are likely.
3. **Run neat-freak before you push.** After the code and the docs are updated, and before you push to GitHub, run the `neat-freak` skill (light path). A push that changes only docs and no code does not need it.
4. **Confirm deletions.** Do not delete any of the deletion candidates that neat-freak lists until the user confirms them.
