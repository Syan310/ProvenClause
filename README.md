# ProvenClause

An evidence-grounded, verifiable contract review agent.

- **What it does:** It reviews an NDA against a checklist, one item at a time. Every finding cites the contract text and goes through three-layer verification. When the result is not certain, it returns "needs human review".
- **Core research question:** Can a verifier make a cheap model (DeepSeek Flash) as reliable as an expensive model (DeepSeek V4 Pro)?
- **Evaluation:** ContractNLI (607 NDAs, 17 checklist questions, CC BY 4.0).
- **Timeline:** 2026-10-21 to 2026-11-03. Stage 0 (skeleton and data) and Stage 1 (B0 baseline + scoring) are complete. B0-F has an accuracy of 0.804 on dev, at $0.0052 per contract. Stage 2 (storage layer: database, state machine, finding ledger, section/definition/cross-reference detection) is complete. Stage 3 (per-item agent: tools, agent loop, crash recovery) is complete. A1-F has an accuracy of 0.795 on dev, with no significant difference from B0-F. Evidence recall rises from 0.552 to 0.708. The cost is $0.0097 per contract.

## Documents

| Document | Contents |
|---|---|
| [Detailed design](docs/design.md) | Positioning, 12 modules (7 ported + 5 new), experiment design for the core research question, evaluation, 14-day plan, future extensions |
| [Roadmap](docs/roadmap.md) | Starts from the MVP (B0 baseline + scoring) and adds modules over 9 stages; tasks, acceptance criteria, and decisions to make first for each stage |
| [Technical decisions](docs/decisions.md) | Verified API constraints; the technology choice for each module is discussed before it is written, and the result is recorded here |
| [Notes](docs/notes.md) | Paper baselines and evaluation setup; data statistics; reading notes on 5 NDAs |
| [Stage 1 spec](docs/stage1-spec.md) | Instructions for the build agent: files, interfaces, tests, checkpoints, and acceptance criteria |
| [Stage 2 spec](docs/stage2-spec.md) | Storage layer: database, state machine, finding ledger, section/definition/cross-reference detection |
| [Stage 3 spec](docs/stage3-spec.md) | Per-item agent: tools, agent loop, crash recovery |

## Run

1. Install [uv](https://docs.astral.sh/uv/). The project uses Python 3.13. The runtime dependencies are in `pyproject.toml`: `openai`, `pydantic`, `python-dotenv`, `pyyaml`, `rank-bm25` (BM25 search for Stage 3, which also installs numpy). `uv run` installs them automatically.
2. Download the official ContractNLI data package [contract-nli.zip](https://stanfordnlp.github.io/contract-nli/resources/contract-nli.zip) (65 MB) and unzip it into `data/`. After you unzip it, `data/contract-nli/test.json` must exist.
3. Run the tests:

```bash
uv run pytest
```

4. Print the data statistics:

```bash
uv run python -m provenclause.eval.stats
```

5. Print one contract and its gold answers:

```bash
uv run python -m provenclause.eval.stats --split dev --contract <contract_id>
```

6. Print the statistics for the N1 rules (section numbers, defined terms, cross-references; train or dev only):

```bash
uv run python -m provenclause.eval.stats --n1 --split dev
```

### Review and score with a model

> **Caution:** The commands below call the DeepSeek API, which costs money.

1. Copy `.env.example` and rename the copy to `.env`. Put your key after `DEEPSEEK_API_KEY=`. Git does not commit `.env`.
2. First review the first 5 contracts of dev:

```bash
uv run python -m provenclause.eval run --config configs/b0-f.yaml --split dev --limit 5
```

   To review with the per-item agent (A1-F), use the A1 config:

```bash
uv run python -m provenclause.eval run --config configs/a1-f.yaml --split dev --limit 5
```

3. The results are in `runs/<run_id>/`: `manifest.json`, `findings.jsonl`, `errors.jsonl`, `metrics.json`. A1 also writes `reviews.db`, which holds the state and finding of each item, and each model call and tool call (redacted). At the end, the command prints a results table. A1 also prints the step count and the tool call count.
4. If a run stops or a contract fails, continue in the same directory:

```bash
uv run python -m provenclause.eval run --config configs/b0-f.yaml --split dev --resume runs/<run_id>
```

5. Run the tests that make real calls (B0 reviews 1 contract; A1 reviews the first 2 items of 1 contract):

```bash
uv run pytest -m live
```

- `--workers N`: review N contracts at the same time. The default is 4.
- `test40` and `test` also need `--allow-test`. They are used only in Stage 7.

## Directory

```
ProvenClause/
├── checklists/nda_17.yaml        # N2: the 17 checklist questions
├── configs/                      # Experiment configs: b0-f.yaml, a1-f.yaml (Flash); b0-p.yaml, a1-p.yaml (Pro)
├── data/contract-nli/            # Official data package (not committed)
├── runs/                         # Evaluation results (not committed)
├── src/provenclause/
│   ├── models.py                 # Shared data structures
│   ├── parse.py                  # N1: read ContractNLI; full text with sentence IDs; sections, definitions, cross-references
│   ├── checklist.py              # N2: read and validate the checklist
│   ├── db.py                     # M2: SQLite, 6 tables and read/write functions
│   ├── fsm.py                    # M3: state transitions for reviews and checklist items
│   ├── ledger.py                 # N3: recording rules for findings; write-once
│   ├── llm.py                    # Call DeepSeek (JSON output and tool calls); retry with double max_tokens after truncation; prices and cost; FakeLLM
│   ├── baseline.py               # B0: review all 17 items in one call
│   ├── tools.py                  # M5: 5 tools including BM25 search; argument validation, timeouts, log redaction
│   ├── agent.py                  # M4: A1 per-item agent loop, budget, concurrency
│   ├── recovery.py               # M6: recover unfinished checklist items after a process crash
│   ├── review.py                 # review_contract(): one entry point for B0, A1, A2
│   └── eval/
│       ├── __main__.py           # Command line: eval run
│       ├── run.py                # Evaluation runner: concurrency, resume
│       ├── metrics.py            # Metrics and results table
│       ├── dataset.py            # Gold answers
│       ├── sample.py             # Sample test-40
│       ├── stats.py              # Data statistics; --n1: statistics for the N1 rules
│       └── splits/test40.txt     # Contract IDs of test-40
├── tests/
└── docs/                         # Design documents and notes
```

The other modules come in later stages, as described in Section 10.2 of the detailed design.
