import asyncio
import json

import pytest

from conftest import routed_llm
from provenclause import agent, db
from provenclause.baseline import B0Item, B0Output
from provenclause.checklist import load_checklist
from provenclause.eval.__main__ import main
from provenclause.eval.run import RunError, run_eval
from provenclause.llm import FakeLLM
from provenclause.models import ReviewConfig, ReviewResult
from provenclause.parse import PROJECT_ROOT

pytestmark = pytest.mark.usefixtures("contractnli_available")

CONFIG = ReviewConfig(name="B0-F", method="B0", agent_model="deepseek-flash", effort="high")
IDS = [item.id for item in load_checklist("nda_17").items]


def full_answer(request=None):
    return B0Output(findings=[
        B0Item(item_id=i, label="Entailment", span_ids=[0], rationale="r") for i in IDS
    ])


def read_rows(run_dir):
    lines = (run_dir / "findings.jsonl").read_text(encoding="utf-8").splitlines()
    return [ReviewResult.model_validate_json(line) for line in lines]


async def test_full_run(tmp_path):
    fake = FakeLLM([full_answer] * 3)
    run_dir, n_errors = await run_eval(CONFIG, "dev", llm=fake, limit=3, runs_dir=tmp_path)
    assert n_errors == 0
    assert run_dir.name.startswith("b0-f_dev_")
    rows = read_rows(run_dir)
    assert len(rows) == 3 and all(len(r.findings) == 17 for r in rows)

    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    for key in ("git_commit", "git_dirty", "model", "effort", "prompt_hash", "seed"):
        assert key in manifest
    assert manifest["model"]["agent_model"] == "deepseek-flash"
    assert manifest["effort"] == "high"
    ids = manifest["contract_ids"]
    assert ids == sorted(ids, key=int) and len(ids) == 3
    assert {r.contract_id for r in rows} == set(ids)

    metrics = json.loads((run_dir / "metrics.json").read_text(encoding="utf-8"))
    assert metrics["n_contracts"] == 3 and metrics["n_items"] == 51


async def test_error_then_resume(tmp_path):
    # workers=1: contracts run in order, so the 2nd call is the 2nd contract
    fake = FakeLLM([full_answer, RuntimeError("API down"), full_answer])
    run_dir, n_errors = await run_eval(CONFIG, "dev", llm=fake, limit=3, workers=1, runs_dir=tmp_path)
    assert n_errors == 1
    assert len(read_rows(run_dir)) == 2
    assert not (run_dir / "metrics.json").exists()
    errors = (run_dir / "errors.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(errors[0])["error_type"] == "RuntimeError"

    fake2 = FakeLLM([full_answer])
    _, n_errors = await run_eval(CONFIG, "dev", llm=fake2, resume=run_dir, workers=1)
    assert n_errors == 0
    assert len(fake2.calls) == 1  # only the missing contract
    rows = read_rows(run_dir)
    assert len(rows) == 3 and len({r.contract_id for r in rows}) == 3
    assert (run_dir / "metrics.json").exists()
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert len(manifest["resumes"]) == 1


async def test_resume_after_incomplete_last_line(tmp_path):
    run_dir, _ = await run_eval(CONFIG, "dev", llm=FakeLLM([full_answer] * 3), limit=3, runs_dir=tmp_path)
    path = run_dir / "findings.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join(lines[:2]) + "\n" + lines[2][:40], encoding="utf-8")  # killed mid-write

    fake = FakeLLM([full_answer])
    _, n_errors = await run_eval(CONFIG, "dev", llm=fake, resume=run_dir)
    assert n_errors == 0 and len(fake.calls) == 1
    rows = read_rows(run_dir)
    assert len(rows) == 3 and len({r.contract_id for r in rows}) == 3


async def test_resume_rejects_other_config(tmp_path):
    run_dir, _ = await run_eval(CONFIG, "dev", llm=FakeLLM([full_answer]), limit=1, runs_dir=tmp_path)
    other = CONFIG.model_copy(update={"effort": "max"})
    with pytest.raises(RunError, match="config"):
        await run_eval(other, "dev", llm=FakeLLM([]), resume=run_dir)
    with pytest.raises(RunError, match="split"):
        await run_eval(CONFIG, "test40", llm=FakeLLM([]), resume=run_dir, allow_test=True)


@pytest.mark.parametrize("split", ["test40", "test"])
async def test_test_splits_need_allow_test(split, tmp_path):
    with pytest.raises(RunError, match="allow-test"):
        await run_eval(CONFIG, split, llm=FakeLLM([]), runs_dir=tmp_path)


def test_cli_refuses_test40_without_flag():
    assert main(["run", "--config", str(PROJECT_ROOT / "configs" / "b0-f.yaml"), "--split", "test40"]) == 2


class SlowLLM(FakeLLM):
    async def parse(self, **request):
        await asyncio.sleep(0.05 if len(self.calls) < 2 else 30)  # the 3rd call hangs
        return await super().parse(**request)


async def test_cancel_keeps_finished_rows(tmp_path):
    # Ctrl+C in asyncio.run() cancels the main task; this does the same.
    task = asyncio.create_task(run_eval(CONFIG, "dev", llm=SlowLLM([full_answer] * 3), limit=3,
                                        workers=1, runs_dir=tmp_path))
    run_dir = None
    while run_dir is None or len(read_rows(run_dir)) < 2:
        await asyncio.sleep(0.02)
        run_dir = next(tmp_path.iterdir(), None)
        if run_dir is not None and not (run_dir / "findings.jsonl").exists():
            run_dir = None
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert (run_dir / "findings.jsonl").read_text(encoding="utf-8").endswith("\n")
    assert len(read_rows(run_dir)) == 2

    fake = FakeLLM([full_answer])
    await run_eval(CONFIG, "dev", llm=fake, resume=run_dir)
    assert len(fake.calls) == 1 and len(read_rows(run_dir)) == 3


# --- stage 3: A1 ----------------------------------------------------------------------

A1_CONFIG = ReviewConfig(name="A1-F", method="A1", agent_model="deepseek-flash", effort="high")


async def test_a1_run(tmp_path):
    fake = routed_llm({})
    run_dir, n_errors = await run_eval(A1_CONFIG, "dev", llm=fake, limit=2, runs_dir=tmp_path)
    assert n_errors == 0
    assert len(fake.calls) == 34
    rows = read_rows(run_dir)
    assert len(rows) == 2 and all(len(r.findings) == 17 for r in rows)
    assert all(f.stats is not None for r in rows for f in r.findings)

    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["prompt_hash"] == agent.prompt_hash()
    metrics = json.loads((run_dir / "metrics.json").read_text(encoding="utf-8"))
    assert metrics["agent"]["n_items_with_stats"] == 34
    assert metrics["agent"]["tool_calls_total"] == {"record_finding": 34}

    conn = db.connect(run_dir / "reviews.db")
    try:
        reviews = conn.execute("SELECT status FROM reviews").fetchall()
        assert [r["status"] for r in reviews] == ["completed", "completed"]
        assert conn.execute("SELECT COUNT(*) FROM findings").fetchone()[0] == 34
    finally:
        conn.close()


async def test_b0_run_has_no_database(tmp_path):
    run_dir, _ = await run_eval(CONFIG, "dev", llm=FakeLLM([full_answer]), limit=1, runs_dir=tmp_path)
    assert not (run_dir / "reviews.db").exists()
