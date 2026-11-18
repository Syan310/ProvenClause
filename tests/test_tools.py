"""Stage 3 M5: the five tools, argument checks, time limit and redaction."""

import json
import time

import pytest

from conftest import FAKE_EMAIL, FAKE_PHONE, make_contract
from provenclause import db, tools
from provenclause.checklist import load_checklist
from provenclause.models import ReviewConfig
from provenclause.tools import TOOL_SCHEMAS, ContractTools, redact, redact_obj, run_tool

CONFIG = ReviewConfig(name="A1-F", method="A1", agent_model="deepseek-flash")
ITEM = "nda-1"


@pytest.fixture
def ct(small_nda):
    return ContractTools(small_nda)


@pytest.fixture
def conn(small_nda):
    connection = db.connect(":memory:")
    db.save_contract(connection, small_nda)
    yield connection
    connection.close()


@pytest.fixture
def review_id(conn, small_nda):
    return db.create_review(conn, small_nda.contract_id, load_checklist("nda_17"), CONFIG)


async def call(ct, conn, review_id, name, args):
    arguments = args if isinstance(args, str) else json.dumps(args)
    return await run_tool(ct, conn, review_id, ITEM, name, arguments)


def test_contract_fixture(small_nda):
    assert [s.section for s in small_nda.spans] == [None, "1", "1", "1", "2", "2", "2", "3", "3", "3", "3"]
    assert small_nda.spans[2].defines == ["Confidential Information"]
    assert small_nda.spans[8].refs == ["2"]


# --- schemas ----------------------------------------------------------------------


def test_schemas():
    names = [t["function"]["name"] for t in TOOL_SCHEMAS]
    assert names == ["search_spans", "read_spans", "lookup_definition", "get_section", "record_finding"]
    record = TOOL_SCHEMAS[4]["function"]["parameters"]
    assert record["additionalProperties"] is False
    assert set(record["required"]) == {"label", "span_ids", "rationale", "self_confidence"}
    assert record["properties"]["label"]["enum"] == ["Entailment", "Contradiction", "NotMentioned"]
    assert "item_id" not in record["properties"]
    assert '"title"' not in json.dumps(TOOL_SCHEMAS)


# --- search_spans -----------------------------------------------------------------


def test_search_ranks_matching_sentence_first(ct):
    lines = ct.search_spans("return or destroy", 8).splitlines()
    assert lines[0].startswith("[S6] (§2) The Recipient shall return or destroy")
    assert lines[0].endswith(")") and "(score " in lines[0]


def test_search_top_k(ct):
    assert len(ct.search_spans("Confidential Information Recipient", 2).splitlines()) == 2
    # only S2, S5 and S6 contain these words; sentences with score 0 are left out
    lines = ct.search_spans("Confidential Information Recipient", 20).splitlines()
    assert sorted(line.split()[0] for line in lines) == ["[S2]", "[S5]", "[S6]"]


def test_search_no_match(ct):
    assert ct.search_spans("helicopter", 8) == 'No sentence matches "helicopter".'
    assert ct.search_spans("the of and", 8) == 'No sentence matches "the of and".'


async def test_search_top_k_too_large(ct, conn, review_id):
    out = await call(ct, conn, review_id, "search_spans", {"query": "x", "top_k": 50})
    assert (out.ok, out.error_kind) == (False, "arguments")
    assert out.text.startswith("ERROR: invalid arguments for search_spans: top_k:")
    assert "(got 50)" in out.text


async def test_search_through_run_tool(ct, conn, review_id):
    out = await call(ct, conn, review_id, "search_spans", {"query": "governed laws"})
    assert out.ok and out.text.startswith("[S10] (§3)") and out.duration_ms >= 0


# --- read_spans -------------------------------------------------------------------


def test_read_window(ct):
    assert ct.read_spans([5], 1).splitlines() == [
        "[S4] (§2) 2. Obligations",
        "[S5] (§2) The Recipient shall not disclose Confidential Information to any third party, "
        "except to its Representatives who need to know it.",
        "[S6] (§2) The Recipient shall return or destroy all Confidential Information within 30 days "
        "after a written request.",
    ]
    assert len(ct.read_spans([5], 0).splitlines()) == 1


def test_read_edges_stay_in_contract(ct):
    assert [line.split()[0] for line in ct.read_spans([0], 3).splitlines()] == ["[S0]", "[S1]", "[S2]", "[S3]"]
    assert [line.split()[0] for line in ct.read_spans([10], 2).splitlines()] == ["[S8]", "[S9]", "[S10]"]
    assert ct.read_spans([0], 0) == "[S0] MUTUAL NON-DISCLOSURE AGREEMENT"  # no section, no (§...)


def test_read_gap_and_merge(ct):
    lines = ct.read_spans([8, 1, 2], 0).splitlines()
    assert [line.split()[0] for line in lines] == ["[S1]", "[S2]", "...", "[S8]"]


async def test_read_unknown_id(ct, conn, review_id):
    out = await call(ct, conn, review_id, "read_spans", {"span_ids": [3, 99]})
    assert (out.ok, out.error_kind) == (False, "arguments")
    assert out.text == "ERROR: invalid arguments for read_spans: span_ids [99] do not exist; valid ids are 0-10"


# --- lookup_definition ------------------------------------------------------------


@pytest.mark.parametrize(("term", "span"), [
    ("Confidential Information", "[S2]"),
    ("confidential information", "[S2]"),
    ("the Confidential Information", "[S2]"),
    ("Representative", "[S3]"),
    ("the representative", "[S3]"),
])
def test_lookup_definition(ct, term, span):
    assert ct.lookup_definition(term).startswith(f"{span} (§1) ")


def test_lookup_definition_missing(ct):
    assert ct.lookup_definition("Purpose") == (
        'No definition of "Purpose" found. Terms defined in this contract: '
        '"Confidential Information", "Representatives".'
    )


# --- get_section ------------------------------------------------------------------


def test_get_section(ct):
    assert [line.split()[0] for line in ct.get_section("2(a)").splitlines()] == ["[S4]", "[S5]", "[S6]"]
    assert ct.get_section("Section 3").startswith("[S7] (§3) 3. Term")


def test_get_section_missing(ct):
    assert ct.get_section("9") == 'Section "9" not found. Sections in this contract: 1, 2, 3.'


def test_get_section_no_sections():
    ct = ContractTools(make_contract(["No headings here.", "None at all."]))
    assert ct.get_section("1") == "This contract has no numbered sections; use search_spans."


def test_get_section_long_is_cut():
    ct = ContractTools(make_contract(["1. Long"] + [f"Sentence {i}." for i in range(44)]))
    lines = ct.get_section("1").splitlines()
    assert len(lines) == 41
    assert lines[-1] == "... 5 more sentences in this section; use read_spans to read them."


# --- argument errors --------------------------------------------------------------


@pytest.mark.parametrize(("name", "arguments", "expected"), [
    ("search_spans", "{not json", "ERROR: invalid arguments for search_spans: Invalid JSON"),
    ("search_spans", "{}", "ERROR: invalid arguments for search_spans: query: Field required"),
    ("search_spans", '{"query": "x", "limit": 3}',
     "ERROR: invalid arguments for search_spans: limit: Extra inputs are not permitted (got 3)"),
    ("search_spans", '{"query": "  "}', "ERROR: invalid arguments for search_spans: query:"),
    ("read_spans", '{"span_ids": []}', "ERROR: invalid arguments for read_spans: span_ids:"),
    ("read_spans", '{"span_ids": [1], "window": 4}', "ERROR: invalid arguments for read_spans: window:"),
])
async def test_bad_arguments(ct, conn, review_id, name, arguments, expected):
    out = await call(ct, conn, review_id, name, arguments)
    assert (out.ok, out.error_kind) == (False, "arguments")
    assert out.text.startswith(expected)


async def test_unknown_tool(ct, conn, review_id):
    out = await call(ct, conn, review_id, "delete_contract", {})
    assert (out.ok, out.error_kind) == (False, "unknown_tool")
    assert "search_spans" in out.text and "record_finding" in out.text


async def test_timeout(ct, conn, review_id, monkeypatch):
    monkeypatch.setattr(tools, "TOOL_TIMEOUT_S", 0.05)
    monkeypatch.setattr(ct, "search_spans", lambda query, top_k: time.sleep(0.5) or "late")
    out = await call(ct, conn, review_id, "search_spans", {"query": "x"})
    assert (out.ok, out.error_kind) == (False, "timeout")
    assert out.text == "ERROR: search_spans timed out after 0.05 s."
    assert out.duration_ms < 400


# --- record_finding ---------------------------------------------------------------


async def test_record_ok(ct, conn, review_id):
    args = {"label": "Entailment", "span_ids": [5], "rationale": "r", "self_confidence": 80}
    out = await call(ct, conn, review_id, "record_finding", args)
    assert (out.ok, out.text, out.error_kind) == (True, "Recorded.", None)
    assert out.record.operation_id == f"{review_id}:{ITEM}:1"
    stored = db.get_finding(conn, out.record.operation_id).finding
    assert (stored.item_id, stored.label, stored.span_ids, stored.self_confidence) == (ITEM, "Entailment", [5], 80)

    again = await call(ct, conn, review_id, "record_finding", args)
    assert again.ok and again.record.duplicate


async def test_record_rejected(ct, conn, review_id):
    args = {"label": "Entailment", "span_ids": [], "rationale": "r", "self_confidence": 80}
    out = await call(ct, conn, review_id, "record_finding", args)
    assert (out.ok, out.error_kind) == (False, "rejected")
    assert out.text == ("ERROR: the finding was not recorded: label Entailment requires at least one span_id. "
                        "Fix it and call record_finding again.")
    assert db.list_findings(conn, review_id) == []


async def test_record_bad_confidence_and_item_id(ct, conn, review_id):
    args = {"label": "NotMentioned", "span_ids": [], "rationale": "r", "self_confidence": 120}
    out = await call(ct, conn, review_id, "record_finding", args)
    assert out.error_kind == "arguments" and "self_confidence" in out.text
    out = await call(ct, conn, review_id, "record_finding", {**args, "self_confidence": 50, "item_id": "nda-2"})
    assert out.error_kind == "arguments" and "item_id" in out.text


# --- redaction --------------------------------------------------------------------


@pytest.mark.parametrize(("text", "expected"), [
    ("mail jane.doe@acme-corp.com now", "mail [EMAIL] now"),
    ("ID 11010519491231002X.", "ID [ID]."),
    ("SSN 123-45-6789,", "SSN [ID],"),
    ("call +1 (212) 555-0100.", "call [PHONE]."),
    ("call 212-555-0100.", "call [PHONE]."),
    ("call (212) 555-0100.", "call [PHONE]."),
    ("call +86 138 1234 5678.", "call [PHONE]."),
    ("call 13812345678.", "call [PHONE]."),
])
def test_redact_replaces(text, expected):
    assert redact(text) == expected


@pytest.mark.parametrize("text", [
    "Section 2.06", "[S123]", "$1,000,000", "2026-10-21", "1934", "Securities Exchange Act of 1934",
    "span_ids [12, 15]", "10 days",
])
def test_redact_keeps(text):
    assert redact(text) == text


def test_redact_obj():
    value = {"a": [FAKE_EMAIL, {"b": FAKE_PHONE}], "n": 3, "t": ("x",), "none": None}
    assert redact_obj(value) == {"a": ["[EMAIL]", {"b": "[PHONE]"}], "n": 3, "t": ["x"], "none": None}
