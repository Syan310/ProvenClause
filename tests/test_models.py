import pytest
from pydantic import ValidationError

from provenclause.eval.__main__ import load_config
from provenclause.models import NO_ANSWER_REASONS, Contract, ReviewConfig, ReviewResult, Span
from provenclause.parse import PROJECT_ROOT, format_contract

CONFIGS = PROJECT_ROOT / "configs"


@pytest.mark.parametrize(("name", "method"), [("b0-f", "B0"), ("b0-p", "B0"), ("a1-f", "A1"), ("a1-p", "A1")])
def test_configs_load(name, method):
    config = load_config(CONFIGS / f"{name}.yaml")
    assert config.name == name.upper()
    assert config.method == method


def test_a1_config_values():
    config = load_config(CONFIGS / "a1-f.yaml")
    assert (config.max_tokens, config.max_steps, config.concurrency, config.max_review_output_tokens) == (
        32000, 8, 4, 100_000)


def test_old_result_without_stats_still_reads():
    # A findings.jsonl line written in stage 1, before Finding.stats existed
    line = ('{"contract_id": "1", "latency_s": 1.0, "usage": {}, "findings": [{"item_id": "nda-1", '
            '"label": "NotMentioned", "span_ids": [], "rationale": "r", "outcome": "verified"}]}')
    result = ReviewResult.model_validate_json(line)
    assert result.findings[0].stats is None


def test_budget_exceeded_is_no_answer():
    assert "budget_exceeded" in NO_ANSWER_REASONS


def test_bad_effort_rejected():
    with pytest.raises(ValidationError):
        ReviewConfig(name="x", method="B0", agent_model="deepseek-flash", effort="medum")


def test_extra_key_rejected(tmp_path):
    path = tmp_path / "c.yaml"
    path.write_text("name: x\nmethod: B0\nagent_model: deepseek-flash\nefort: high\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        load_config(path)


def test_format_contract():
    text = "First sentence. Second one."
    contract = Contract(
        contract_id="1", source="contractnli", title="t", text=text,
        spans=[Span(span_id=0, start=0, end=15, text=text[:15]), Span(span_id=1, start=16, end=27, text=text[16:])],
    )
    assert format_contract(contract) == "[S0] First sentence.\n[S1] Second one."
