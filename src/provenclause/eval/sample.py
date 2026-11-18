"""Draw test-40: 40 test contracts chosen with a fixed seed.

    uv run python -m provenclause.eval.sample          # write splits/test40.txt
    uv run python -m provenclause.eval.sample --check  # exit 1 if the file differs
"""

import argparse
import random
import sys
from pathlib import Path

from provenclause.parse import CONTRACTNLI_DIR, read_split_json

SEED = 20261021
SIZE = 40
TEST40_PATH = Path(__file__).parent / "splits" / "test40.txt"


def sample_test40(data_dir: Path = CONTRACTNLI_DIR) -> list[str]:
    # Sort first so the result does not depend on the order in the JSON file.
    ids = sorted((doc["id"] for doc in read_split_json("test", data_dir)["documents"]))
    picked = random.Random(SEED).sample(ids, SIZE)
    return [str(i) for i in sorted(picked)]


def render(ids: list[str]) -> str:
    return "\n".join(ids) + "\n"


def load_test40(path: Path = TEST40_PATH) -> list[str]:
    return path.read_text(encoding="utf-8").split()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="compare instead of write")
    args = parser.parse_args()

    content = render(sample_test40())
    if args.check:
        same = TEST40_PATH.exists() and TEST40_PATH.read_text(encoding="utf-8") == content
        print("test40.txt is up to date" if same else "test40.txt differs from a fresh sample")
        return 0 if same else 1

    TEST40_PATH.parent.mkdir(parents=True, exist_ok=True)
    TEST40_PATH.write_text(content, encoding="utf-8", newline="\n")
    print(f"wrote {SIZE} contract ids to {TEST40_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
