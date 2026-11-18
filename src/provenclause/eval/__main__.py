"""Evaluation command line.

    uv run python -m provenclause.eval run --config configs/b0-f.yaml --split dev --limit 5
    uv run python -m provenclause.eval run --config configs/b0-f.yaml --split dev --resume runs/<run_id>
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

import dotenv
import yaml

from provenclause.eval.run import RUNS_DIR, RunError, run_eval
from provenclause.llm import DeepSeekLLM
from provenclause.models import ReviewConfig


def load_config(path: Path) -> ReviewConfig:
    with path.open(encoding="utf-8") as f:
        return ReviewConfig.model_validate(yaml.safe_load(f))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m provenclause.eval", description="ProvenClause evaluation")
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="review the contracts of a split and score them")
    run.add_argument("--config", type=Path, required=True, help="YAML file with a ReviewConfig")
    run.add_argument("--split", required=True, choices=["dev", "test40", "test"])
    run.add_argument("--allow-test", action="store_true", help="allow test40 and test (stage 7 only)")
    run.add_argument("--limit", type=int, help="only the first N contracts by contract_id")
    run.add_argument("--workers", type=int, default=4, help="contracts reviewed at the same time")
    run.add_argument("--resume", type=Path, metavar="DIR", help="continue the run in this directory")
    run.add_argument("--runs-dir", type=Path, default=RUNS_DIR, help="where new runs go")
    return parser


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")  # the console is GBK on Windows
    args = build_parser().parse_args(argv)
    config = load_config(args.config)
    if args.split in ("test40", "test") and not args.allow_test:
        print(f"error: split {args.split!r} is held out until stage 7; pass --allow-test", file=sys.stderr)
        return 2

    dotenv.load_dotenv()
    if not os.environ.get("DEEPSEEK_API_KEY"):
        print("error: DEEPSEEK_API_KEY is not set; copy .env.example to .env and add the key", file=sys.stderr)
        return 2

    try:
        _, n_errors = asyncio.run(run_eval(
            config, args.split, llm=DeepSeekLLM(), limit=args.limit, workers=args.workers,
            resume=args.resume, runs_dir=args.runs_dir, allow_test=args.allow_test,
        ))
    except RunError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("interrupted; finished contracts are saved. Continue with --resume <run dir>", file=sys.stderr)
        return 130
    return 1 if n_errors else 0


if __name__ == "__main__":
    sys.exit(main())
