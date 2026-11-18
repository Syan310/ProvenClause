"""review_contract(): the one entry point for B0, A1 and A2.

The evaluation runner and the API (stage 5) only call this function.
"""

import sqlite3
import time

from provenclause import db
from provenclause.agent import review_a1
from provenclause.baseline import review_b0
from provenclause.llm import LLM
from provenclause.models import Checklist, Contract, ReviewConfig, ReviewResult


async def review_contract(
    contract: Contract, checklist: Checklist, config: ReviewConfig, llm: LLM,
    *, conn: sqlite3.Connection | None = None,
) -> ReviewResult:
    """conn is the database for A1 and A2 (decision record 3.6 D2). With None they
    use an in-memory database that is closed at the end. B0 does not use it."""
    start = time.perf_counter()  # includes the SDK's retry time
    match config.method:
        case "B0":
            findings, usage = await review_b0(contract, checklist, config, llm)
        case "A1":
            own = conn is None
            conn = db.connect(":memory:") if own else conn
            try:
                findings, usage = await review_a1(conn, contract, checklist, config, llm)
            finally:
                if own:
                    conn.close()
        case "A2":
            raise NotImplementedError("method A2 comes in stage 4")
    return ReviewResult(
        contract_id=contract.contract_id,
        findings=findings,
        usage=usage,
        latency_s=time.perf_counter() - start,
    )
