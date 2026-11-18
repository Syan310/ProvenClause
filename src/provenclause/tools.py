"""M5 tools: what the Agent may call while it reviews one checklist item.

Four read-only tools look up the contract: search_spans (BM25), read_spans,
lookup_definition and get_section. record_finding hands in the answer through
the N3 ledger. run_tool() is the only place that runs a tool: it parses and
validates the arguments, runs the tool with a time limit, and measures it. It
writes no events; the Agent does (decision record 3.6 B4, B5).

Strict mode is off (A2). Pydantic validates the arguments, and the errors go
back to the model as the tool result. TOOL_SCHEMAS is built from the same
argument models, so the schema and the check always agree.

Results are text, one sentence per line, "[S{id}] (§{section}) {text}", the same
start as the contract lines in the system prompt (B3). Errors start with "ERROR:".

redact() replaces e-mail addresses, phone numbers and ID numbers. It is only
used on what goes into the events table; the model sees the original text.
"""

import asyncio
import json
import re
import sqlite3
import time
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError
from rank_bm25 import BM25Okapi

from provenclause import ledger
from provenclause.ledger import RecordResult
from provenclause.models import Contract, Finding, Label, Span
from provenclause.parse import definition_spans, section_spans

TOOL_TIMEOUT_S = 10.0
READ_ONLY_TOOLS = ("search_spans", "read_spans", "lookup_definition", "get_section")
MAX_SECTION_SPANS = 40
MAX_LISTED_TERMS = 40

# Common English words that carry no meaning for search. No stemming.
STOPWORDS = frozenset(
    "a an and any are as at be been by for from has have in is it its of on or "
    "shall such that the this to was were will with".split()
)

NonEmpty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class ToolArgumentError(ValueError):
    """Arguments that pass the schema but do not fit this contract, e.g. a span id that does not exist."""


class ToolOutcome(BaseModel):
    name: str
    ok: bool  # False: bad arguments, unknown tool, timeout, rejected record
    text: str  # what goes back to the model
    error_kind: str | None = None  # "arguments" | "unknown_tool" | "timeout" | "rejected"
    record: RecordResult | None = None  # only for record_finding
    duration_ms: float


# --- argument models -------------------------------------------------------------


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SearchSpansArgs(_Args):
    query: NonEmpty = Field(description="Words to search for, e.g. 'return or destroy confidential information'.")
    top_k: int = Field(8, ge=1, le=20, description="Number of sentences to return.")


class ReadSpansArgs(_Args):
    span_ids: list[int] = Field(min_length=1, max_length=10, description="Sentence ids, e.g. [12, 40].")
    window: int = Field(1, ge=0, le=3, description="Also return this many sentences before and after each id.")


class LookupDefinitionArgs(_Args):
    term: NonEmpty = Field(description="A defined term, e.g. 'Confidential Information'.")


class GetSectionArgs(_Args):
    section: NonEmpty = Field(description="A section number as the contract writes it, e.g. '5', '2.06', 'Section 2(a)'.")


class RecordFindingArgs(_Args):
    label: Label = Field(description="Entailment, Contradiction or NotMentioned.")
    span_ids: list[int] = Field(description="Ids of the sentences the label depends on; [] for NotMentioned.")
    rationale: str = Field(description="One sentence that explains the label.")
    self_confidence: int = Field(ge=0, le=100, description="How sure you are, 0-100.")


ARG_MODELS: dict[str, type[_Args]] = {
    "search_spans": SearchSpansArgs,
    "read_spans": ReadSpansArgs,
    "lookup_definition": LookupDefinitionArgs,
    "get_section": GetSectionArgs,
    "record_finding": RecordFindingArgs,
}

DESCRIPTIONS = {
    "search_spans": "Search the contract with BM25 keyword ranking. Returns the best matching sentences "
                    "with their ids, sections and scores.",
    "read_spans": "Read sentences by id, with the sentences around them.",
    "lookup_definition": "Find the sentence that defines a term, e.g. a capitalized term such as "
                         "'Confidential Information'.",
    "get_section": "Read the sentences of a numbered section and its subsections, e.g. when a sentence "
                   "refers to 'Section 5'.",
    "record_finding": "Record your final answer for the checklist item. This is the only way to answer. "
                      "If the finding breaks an evidence rule, the error comes back and you can fix it.",
}


def _drop_titles(schema: Any) -> Any:
    """Pydantic adds a "title" to every property; the model does not need them."""
    if isinstance(schema, dict):
        return {k: _drop_titles(v) for k, v in schema.items()
                if not (k == "title" and isinstance(v, str))}
    if isinstance(schema, list):
        return [_drop_titles(v) for v in schema]
    return schema


TOOL_SCHEMAS: list[dict] = [
    {"type": "function", "function": {
        "name": name, "description": DESCRIPTIONS[name],
        "parameters": _drop_titles(model.model_json_schema()),
    }}
    for name, model in ARG_MODELS.items()
]


# --- the four read-only tools --------------------------------------------------------


def tokenize(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in STOPWORDS]


def format_span(span: Span) -> str:
    section = f" (§{span.section})" if span.section is not None else ""
    return f"[S{span.span_id}]{section} {span.text}"


class ContractTools:
    """The read-only tools for one contract. One per review; the 17 items share it.

    It only reads the contract and never touches the database, so it can run in
    a worker thread.
    """

    def __init__(self, contract: Contract):
        self.contract = contract
        self._spans = contract.spans
        self._position = {s.span_id: i for i, s in enumerate(self._spans)}
        corpus = [tokenize(s.text) for s in self._spans]
        # BM25Okapi divides by the average length: no index for a contract without words.
        self._bm25 = BM25Okapi(corpus) if any(corpus) else None

    def search_spans(self, query: str, top_k: int = 8) -> str:
        terms = tokenize(query)
        if self._bm25 is None or not terms:
            return f'No sentence matches "{query}".'
        scores = self._bm25.get_scores(terms)
        ranked = sorted(
            ((float(score), span) for score, span in zip(scores, self._spans, strict=True) if score > 0),
            key=lambda pair: (-pair[0], pair[1].span_id),
        )[:top_k]
        if not ranked:
            return f'No sentence matches "{query}".'
        return "\n".join(f"{format_span(span)} (score {score:.2f})" for score, span in ranked)

    def read_spans(self, span_ids: list[int], window: int = 1) -> str:
        missing = list(dict.fromkeys(i for i in span_ids if i not in self._position))
        if missing:
            ids = list(self._position)
            valid = f"{min(ids)}-{max(ids)}" if ids else "none"
            raise ToolArgumentError(f"span_ids {missing} do not exist; valid ids are {valid}")
        positions: set[int] = set()
        for span_id in span_ids:
            p = self._position[span_id]
            positions.update(range(max(0, p - window), min(len(self._spans), p + window + 1)))
        lines: list[str] = []
        previous = None
        for p in sorted(positions):
            if previous is not None and p != previous + 1:
                lines.append("...")
            lines.append(format_span(self._spans[p]))
            previous = p
        return "\n".join(lines)

    def lookup_definition(self, term: str) -> str:
        candidates = [term]
        base = term
        if term.lower().startswith("the "):
            base = term[4:].strip()
            candidates.append(base)
        candidates.append(base[:-1] if base.lower().endswith("s") else base + "s")
        for candidate in candidates:
            spans = definition_spans(self.contract, candidate)
            if spans:
                return "\n".join(format_span(s) for s in spans)

        terms = list(dict.fromkeys(t for s in self._spans for t in s.defines))
        if not terms:
            return f'No definition of "{term}" found. This contract has no defined terms; use search_spans.'
        listed = ", ".join(f'"{t}"' for t in terms[:MAX_LISTED_TERMS])
        more = f" and {len(terms) - MAX_LISTED_TERMS} more" if len(terms) > MAX_LISTED_TERMS else ""
        return f'No definition of "{term}" found. Terms defined in this contract: {listed}{more}.'

    def get_section(self, section: str) -> str:
        spans = section_spans(self.contract, section)
        if spans:
            lines = [format_span(s) for s in spans[:MAX_SECTION_SPANS]]
            if len(spans) > MAX_SECTION_SPANS:
                lines.append(f"... {len(spans) - MAX_SECTION_SPANS} more sentences in this section; "
                             "use read_spans to read them.")
            return "\n".join(lines)
        sections = list(dict.fromkeys(s.section for s in self._spans if s.section is not None))
        if not sections:
            return "This contract has no numbered sections; use search_spans."
        return f'Section "{section}" not found. Sections in this contract: {", ".join(sections)}.'


# --- running a tool ----------------------------------------------------------------


def _short(value: Any, limit: int = 100) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    return text if len(text) <= limit else text[:limit] + "..."


def format_validation_error(exc: ValidationError) -> str:
    """Every problem Pydantic found, with the field and the value, in one line."""
    parts = []
    for error in exc.errors(include_url=False):
        loc = ".".join(str(x) for x in error["loc"])
        part = f"{loc}: {error['msg']}" if loc else error["msg"]
        if error["type"] != "missing":
            part += f" (got {_short(error['input'])})"
        parts.append(part)
    return "; ".join(parts)


async def run_tool(
    tools: ContractTools, conn: sqlite3.Connection, review_id: str, item_id: str,
    name: str, arguments: str,
) -> ToolOutcome:
    """Parse, validate and run one tool call. Never raises for the model's mistakes."""
    start = time.perf_counter()

    def outcome(ok: bool, text: str, error_kind: str | None = None,
                record: RecordResult | None = None) -> ToolOutcome:
        return ToolOutcome(name=name, ok=ok, text=text, error_kind=error_kind, record=record,
                           duration_ms=(time.perf_counter() - start) * 1000)

    model = ARG_MODELS.get(name)
    if model is None:
        return outcome(False, f"ERROR: unknown tool {name!r}; available tools: {', '.join(ARG_MODELS)}.",
                       "unknown_tool")
    try:
        args = model.model_validate_json(arguments)
    except ValidationError as exc:
        return outcome(False, f"ERROR: invalid arguments for {name}: {format_validation_error(exc)}",
                       "arguments")

    if isinstance(args, RecordFindingArgs):
        # In the event loop's thread: the SQLite connection is only used here (B2).
        finding = Finding(item_id=item_id, label=args.label, span_ids=args.span_ids,
                          rationale=args.rationale, self_confidence=args.self_confidence)
        result = ledger.record_finding(conn, tools.contract, review_id, finding)
        if result.ok:
            return outcome(True, "Recorded.", record=result)
        return outcome(False, f"ERROR: the finding was not recorded: {'; '.join(result.errors)}. "
                       "Fix it and call record_finding again.", "rejected", record=result)

    function = getattr(tools, name)
    try:
        text = await asyncio.wait_for(asyncio.to_thread(function, **args.model_dump()), TOOL_TIMEOUT_S)
    except TimeoutError:
        # The thread cannot be stopped; it finishes in the background and its result is dropped.
        return outcome(False, f"ERROR: {name} timed out after {TOOL_TIMEOUT_S:g} s.", "timeout")
    except ToolArgumentError as exc:
        return outcome(False, f"ERROR: invalid arguments for {name}: {exc}", "arguments")
    return outcome(True, text)


# --- redaction (B4) ------------------------------------------------------------------

# Order matters: ID numbers before phone numbers, so an ID is not read as a phone number.
_REDACTIONS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}"), "[EMAIL]"),
    (re.compile(r"(?<![0-9A-Za-z])\d{17}[0-9Xx](?![0-9A-Za-z])"), "[ID]"),  # Chinese ID number
    (re.compile(r"(?<![\d-])\d{3}-\d{2}-\d{4}(?![\d-])"), "[ID]"),  # US SSN
    (re.compile(r"(?<![\w+])\+\d[\d\s().-]{6,}\d"), "[PHONE]"),  # international, starts with +
    (re.compile(r"(?<![\w(-])(?:\(\d{3}\)\s?|\d{3}[\s.-])\d{3}[\s.-]\d{4}(?![\d-])"), "[PHONE]"),  # US
    (re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"), "[PHONE]"),  # Chinese mobile
]


def redact(text: str) -> str:
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


def redact_obj(value: Any) -> Any:
    """redact() on every str inside dicts, lists and tuples; other values unchanged."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {k: redact_obj(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_obj(v) for v in value]
    return value
