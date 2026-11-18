"""Shared data structures (F5: Pydantic v2).

Stage 0 defines Span, Contract, ChecklistItem and Checklist.
Stage 1 adds Finding, ReviewConfig, Usage and ReviewResult.
Stage 2 adds Span.defines and the status types of M3.
Stage 3 adds AgentStats, Finding.stats and ReviewConfig.max_review_output_tokens.
Later stages only add fields; they do not change existing ones.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict

Label = Literal["Entailment", "Contradiction", "NotMentioned"]
Outcome = Literal["verified", "violated", "abstained"]
Effort = Literal["low", "medium", "high", "xhigh", "max"]
ReviewStatus = Literal["created", "parsing", "reviewing", "completed", "failed"]
ItemStatus = Literal["pending", "drafting", "verifying", "verified", "violated", "abstained"]

# Reasons for an abstained finding where the model gave no answer.
# Scoring counts these items as "no answer" (wrong for every gold label).
NO_ANSWER_REASONS = frozenset({"missing", "refusal", "max_tokens", "parse_error", "budget_exceeded"})


class Span(BaseModel):
    span_id: int
    start: int
    end: int
    text: str
    section: str | None = None  # filled in stage 2
    is_definition: bool = False  # filled in stage 2; always equals bool(defines)
    refs: list[str] = []  # filled in stage 2
    defines: list[str] = []  # stage 2: the terms this sentence defines


class Contract(BaseModel):
    contract_id: str
    source: Literal["contractnli", "pdf"]
    title: str
    text: str
    spans: list[Span]


class ChecklistItem(BaseModel):
    # forbid: a misspelled key in the YAML file is an error, not a silent default
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    name: str
    hypothesis: str
    guidance: str
    risk_label_for_recipient: Label


class Checklist(BaseModel):
    model_config = ConfigDict(frozen=True)

    checklist_id: str
    items: list[ChecklistItem]


class AgentStats(BaseModel):
    """What the Agent loop did for one item (decision record 3.6 C7). Not stored in the database."""

    n_steps: int = 0  # model calls, not counting the length retry
    tool_calls: dict[str, int] = {}  # tool name -> calls, failed calls included
    n_tool_errors: int = 0  # bad arguments, unknown tool, timeout
    n_rejected_records: int = 0  # record_finding calls that validate_finding() rejected
    n_nudges: int = 0  # C2 reminders after a plain-text reply
    n_length_retries: int = 0  # C6 retries with 2x max_tokens


class Finding(BaseModel):
    item_id: str
    # Required. With no answer it holds the placeholder "NotMentioned";
    # scoring then looks at reason, not at this label.
    label: Label
    span_ids: list[int]
    rationale: str
    reason: str | None = None  # why abstained, e.g. "refusal", "missing"
    self_confidence: int | None = None  # filled by the Agent from stage 3
    outcome: Outcome | None = None  # filled by the verifier from stage 4; B0 sets "verified"
    support_score: int | None = None  # filled by L3 from stage 4
    checks: dict[str, str] = {}  # from stage 4: "L1" / "L2" / "L3" -> result
    stats: AgentStats | None = None  # stage 3: filled by A1 and A2; None for B0


class ReviewConfig(BaseModel):
    # forbid: a misspelled key in the config file is an error
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str  # e.g. "A2-F"
    method: Literal["B0", "A1", "A2"]
    agent_model: str
    verifier_model: str | None = None  # differs from agent_model only in C5
    effort: Effort = "medium"
    max_tokens: int = 16000
    max_steps: int = 8
    max_retries: int = 2
    concurrency: int = 4
    max_review_output_tokens: int = 300_000  # C4: output tokens summed over one review


class Usage(BaseModel):
    """Usage summed over all model calls of one review."""

    input_tokens: int = 0  # prompt tokens that missed the cache
    output_tokens: int = 0  # includes reasoning_tokens
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0  # DeepSeek does not report or bill it
    reasoning_tokens: int = 0  # part of output_tokens
    n_calls: int = 0
    cost_usd: float = 0.0


class ReviewResult(BaseModel):
    contract_id: str
    findings: list[Finding]
    usage: Usage
    latency_s: float
