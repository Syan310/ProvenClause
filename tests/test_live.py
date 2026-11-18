"""Real DeepSeek calls. They cost money, so they only run with: uv run pytest -m live"""

import os

import dotenv
import pytest

from provenclause.checklist import load_checklist
from provenclause.eval.__main__ import load_config
from provenclause.llm import DeepSeekLLM
from provenclause.models import Checklist
from provenclause.parse import PROJECT_ROOT, load_contractnli
from provenclause.review import review_contract

pytestmark = [pytest.mark.live, pytest.mark.usefixtures("contractnli_available")]


async def test_b0_flash_on_shortest_dev_contract():
    dotenv.load_dotenv()
    if not os.environ.get("DEEPSEEK_API_KEY"):
        pytest.skip("DEEPSEEK_API_KEY is not set")
    contract = next(c for c in load_contractnli("dev") if c.contract_id == "456")  # 2,270 characters
    config = load_config(PROJECT_ROOT / "configs" / "b0-f.yaml")

    result = await review_contract(contract, load_checklist("nda_17"), config, DeepSeekLLM())

    print(result.usage, f"{result.latency_s:.1f}s")
    assert len(result.findings) == 17
    assert result.usage.n_calls == 1
    assert result.usage.cost_usd > 0


async def test_a1_flash_first_two_items():
    dotenv.load_dotenv()
    if not os.environ.get("DEEPSEEK_API_KEY"):
        pytest.skip("DEEPSEEK_API_KEY is not set")
    contract = next(c for c in load_contractnli("dev") if c.contract_id == "456")
    full = load_checklist("nda_17")
    checklist = Checklist(checklist_id=full.checklist_id, items=full.items[:2])
    config = load_config(PROJECT_ROOT / "configs" / "a1-f.yaml")

    result = await review_contract(contract, checklist, config, DeepSeekLLM())

    print(result.usage, f"{result.latency_s:.1f}s", [f.stats for f in result.findings])
    assert [f.item_id for f in result.findings] == [item.id for item in checklist.items]
    assert all(f.stats is not None and f.stats.n_steps >= 1 for f in result.findings)
    assert result.usage.cost_usd > 0
