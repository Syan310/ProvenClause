"""M7 evaluation runner: review every contract of a split, then score the results.

Results go to runs/<run_id>/:
    manifest.json   what ran: git commit, config, models, effort, prompt hash, seed
    findings.jsonl  one complete ReviewResult per line, one line per contract
    errors.jsonl    contracts that failed with an API error (rerun by --resume)
    metrics.json    written only when every contract has a result
    reviews.db      A1 / A2 only: reviews, items, findings and events (decision record 3.6 D1)

--resume reviews a half-done contract again from the start with a new review_id
(D4); the old review stays in reviews.db as "reviewing" or "failed".

The command line is in eval/__main__.py. run_eval() takes the LLM as an
argument, so tests pass a FakeLLM.
"""

import asyncio
import hashlib
import json
import os
import platform
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import openai

from provenclause import agent, baseline, db
from provenclause.checklist import CHECKLIST_DIR, load_checklist
from provenclause.eval.dataset import load_gold
from provenclause.eval.metrics import compute_metrics, format_agent_metrics, format_table
from provenclause.eval.sample import SEED, load_test40
from provenclause.llm import LLM
from provenclause.models import Contract, ReviewConfig, ReviewResult
from provenclause.parse import CONTRACTNLI_DIR, PROJECT_ROOT, load_contractnli
from provenclause.review import review_contract

RUNS_DIR = PROJECT_ROOT / "runs"
CHECKLIST_ID = "nda_17"

EvalSplit = Literal["dev", "test40", "test"]
TEST_SPLITS = ("test40", "test")


class RunError(Exception):
    """The run cannot start, e.g. a test split without --allow-test."""


def prompt_hash(config: ReviewConfig) -> str:
    match config.method:
        case "B0":
            return baseline.prompt_hash()
        case "A1":
            return agent.prompt_hash()
        case _:
            raise NotImplementedError(f"method {config.method} comes in stage 4")


def load_eval_split(split: EvalSplit, data_dir: Path = CONTRACTNLI_DIR) -> list[Contract]:
    """Contracts of the split, sorted by the number in contract_id."""
    if split == "test40":
        ids = set(load_test40())
        contracts = [c for c in load_contractnli("test", data_dir) if c.contract_id in ids]
    else:
        contracts = load_contractnli(split, data_dir)
    return sorted(contracts, key=lambda c: int(c.contract_id))


def _git(*args: str) -> str | None:
    try:
        out = subprocess.run(["git", *args], cwd=PROJECT_ROOT, capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return None
    return out.stdout.strip()


def _git_state() -> dict[str, Any]:
    status = _git("status", "--porcelain")
    return {"git_commit": _git("rev-parse", "HEAD"), "git_dirty": None if status is None else status != ""}


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def make_manifest(config: ReviewConfig, split: EvalSplit, limit: int | None, contract_ids: list[str]) -> dict[str, Any]:
    return {
        **_git_state(),
        "model": {"agent_model": config.agent_model, "verifier_model": config.verifier_model},
        "effort": config.effort,
        "prompt_hash": prompt_hash(config),
        "seed": SEED,
        "config": config.model_dump(mode="json"),
        "split": split,
        "limit": limit,
        "checklist_id": CHECKLIST_ID,
        "checklist_hash": _file_hash(CHECKLIST_DIR / f"{CHECKLIST_ID}.yaml"),
        "contract_ids": contract_ids,
        "openai_version": openai.__version__,
        "python_version": platform.python_version(),
        "started_at": _now(),
        "resumes": [],
    }


def check_resume(manifest: dict[str, Any], config: ReviewConfig, split: EvalSplit) -> None:
    """A resumed run must use the same prompt, config (models, effort, ...) and split."""
    problems = []
    if manifest["prompt_hash"] != prompt_hash(config):
        problems.append(f"prompt_hash {manifest['prompt_hash']} != {prompt_hash(config)}")
    if manifest["config"] != config.model_dump(mode="json"):
        problems.append(f"config {manifest['config']} != {config.model_dump(mode='json')}")
    if manifest["split"] != split:
        problems.append(f"split {manifest['split']} != {split}")
    if problems:
        raise RunError("cannot resume this run: " + "; ".join(problems))


def read_findings(path: Path) -> list[ReviewResult]:
    """Read findings.jsonl. An incomplete last line (the process was killed while
    writing it) is dropped, and the file is rewritten with only complete lines."""
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    results = []
    for n, line in enumerate(lines, start=1):
        try:
            results.append(ReviewResult.model_validate_json(line))
        except ValueError:
            if n != len(lines):
                raise RunError(f"{path}: line {n} of {len(lines)} is not a valid result") from None
            print(f"dropping incomplete last line of {path}", file=sys.stderr)
            with path.open("w", encoding="utf-8", newline="\n") as f:
                f.writelines(line + "\n" for line in lines[:-1])
                f.flush()
                os.fsync(f.fileno())
    return results


def _append_line(f, line: str) -> None:
    # One write call per line, then fsync: a crash leaves at most one incomplete last line.
    f.write(line + "\n")
    f.flush()
    os.fsync(f.fileno())


async def run_eval(
    config: ReviewConfig, split: EvalSplit, *, llm: LLM, limit: int | None = None,
    workers: int = 4, resume: Path | None = None, runs_dir: Path = RUNS_DIR,
    allow_test: bool = False, data_dir: Path = CONTRACTNLI_DIR,
) -> tuple[Path, int]:
    """Run the evaluation. Returns (run directory, number of contracts that failed)."""
    if split in TEST_SPLITS and not allow_test:
        raise RunError(f"split {split!r} is held out until stage 7; pass --allow-test to use it")

    contracts = load_eval_split(split, data_dir)
    checklist = load_checklist(CHECKLIST_ID)

    if resume is None:
        selected = contracts[:limit] if limit is not None else contracts
        run_dir = runs_dir / f"{config.name.lower()}_{split}_{datetime.now():%Y%m%d-%H%M%S}"
        run_dir.mkdir(parents=True)
        manifest = make_manifest(config, split, limit, [c.contract_id for c in selected])
    else:
        run_dir = resume
        manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
        check_resume(manifest, config, split)
        manifest["resumes"].append({**_git_state(), "started_at": _now()})
        # The contract list comes from the first run, so --limit does not apply here.
        if limit is not None:
            print(f"--limit is ignored with --resume; the run keeps its {len(manifest['contract_ids'])} contracts",
                  file=sys.stderr)
        wanted = set(manifest["contract_ids"])
        selected = [c for c in contracts if c.contract_id in wanted]
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    findings_path = run_dir / "findings.jsonl"
    done = {r.contract_id for r in read_findings(findings_path)}
    todo = [c for c in selected if c.contract_id not in done]
    print(f"{run_dir}: {len(done)} done, {len(todo)} to review", file=sys.stderr)

    semaphore = asyncio.Semaphore(workers)
    # One connection for all contracts: every database call is synchronous and
    # runs in this thread (decision record 3.6 D1).
    conn = db.connect(run_dir / "reviews.db") if config.method != "B0" else None

    async def review_one(contract: Contract) -> tuple[Contract, ReviewResult | Exception]:
        async with semaphore:
            try:
                return contract, await review_contract(contract, checklist, config, llm, conn=conn)
            except Exception as exc:  # API errors: log the contract and go on
                return contract, exc

    n_errors = 0
    tasks = [asyncio.create_task(review_one(c)) for c in todo]
    try:
        with (findings_path.open("a", encoding="utf-8", newline="\n") as findings_file,
              (run_dir / "errors.jsonl").open("a", encoding="utf-8", newline="\n") as errors_file):
            # The only place that writes the files: one result at a time, as it completes.
            for n, next_done in enumerate(asyncio.as_completed(tasks), start=1):
                contract, outcome = await next_done
                prefix = f"[{n}/{len(todo)}] {contract.contract_id}"
                if isinstance(outcome, Exception):
                    n_errors += 1
                    _append_line(errors_file, json.dumps({
                        "contract_id": contract.contract_id, "error_type": type(outcome).__name__,
                        "message": str(outcome), "time": _now(),
                    }))
                    print(f"{prefix} ERROR {type(outcome).__name__}: {outcome}", file=sys.stderr)
                else:
                    _append_line(findings_file, outcome.model_dump_json())
                    print(f"{prefix} ${outcome.usage.cost_usd:.4f} {outcome.latency_s:.1f}s", file=sys.stderr)
    finally:
        for task in tasks:
            task.cancel()
        if conn is not None:
            conn.close()

    results = read_findings(findings_path)
    missing = set(manifest["contract_ids"]) - {r.contract_id for r in results}
    if missing:
        print(f"{len(missing)} contracts have no result; rerun with --resume {run_dir}", file=sys.stderr)
        return run_dir, max(n_errors, len(missing))

    gold = load_gold("test" if split == "test40" else split, data_dir)
    metrics = compute_metrics(results, gold, selected)
    (run_dir / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    print(format_table([(config.name, metrics)]))
    if "agent" in metrics:
        print(format_agent_metrics(metrics["agent"]))
    print(f"cache read ratio {metrics['cache_read_ratio']:.3f}, abstained {metrics['n_abstained_by_reason']}")
    print(f"total cost ${metrics['cost_total_usd']:.4f}, results in {run_dir}")
    return run_dir, n_errors
