"""Stage 2 N1 rules: sections, definitions and cross-references."""

import pytest

from provenclause.baseline import prompt_hash
from provenclause.models import Contract, Span
from provenclause.parse import (
    annotate_contract,
    definition_spans,
    detect_sections,
    find_definitions,
    find_refs,
    format_contract,
    load_contractnli,
    normalize_key,
    section_spans,
)


def make_contract(*texts: str) -> Contract:
    spans, pos = [], 0
    for i, text in enumerate(texts):
        spans.append(Span(span_id=i, start=pos, end=pos + len(text), text=text))
        pos += len(text) + 1
    return Contract(
        contract_id="t", source="contractnli", title="t", text="\n".join(texts), spans=spans
    )


def sections(*texts: str) -> list[str | None]:
    return detect_sections(make_contract(*texts).spans)


# --- sections ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("heading", "key"),
    [
        ("1. Definition and interpretation", "1"),  # dev 3 S15
        ("1.1 'Purpose' shall mean any discussions", "1.1"),  # dev 7 S22
        ("1.01 Definitions", "1.01"),  # dev 10 S12
        ("ARTICLE 1 - DEFINITIONS", "1"),  # dev 10 S11
        ("ARTICLE II", "II"),  # dev 398 S22
        ("Section 2. Remedies", "2"),
        ("1.2", "1.2"),  # dev 7 S23: a heading alone in its span
    ],
)
def test_heading_forms(heading, key):
    assert sections("Preamble.", heading, "Body text.") == [None, key, key]


def test_section_with_parenthesis_is_not_a_heading():
    # dev 605 S44: "(" right after the number
    assert sections("1. Scope", "Section 4(b) of this Agreement and the rest") == ["1", "1"]


def test_bracket_numbers_are_not_sections():
    assert sections("2. Use", "(a) first item", "a) second item", "(i) third") == ["2"] * 4


def test_out_of_order_number_is_not_a_heading():
    # "7." between 2 and 3, with "3." next: the next candidate does not confirm the jump
    assert sections("1. A", "2. B", "7. stray", "3. C") == ["1", "2", "2", "3"]


def test_jump_confirmed_by_next_heading():
    # dev 582 has no "9.": 8 -> 10 is accepted because 12 follows within 2
    assert sections("8. A", "10. B", "12. C") == ["8", "10", "12"]


def test_last_heading_may_jump_three():
    # dev 19: "15." then "18. Fax Execution" at the end
    assert sections("1. A", "2. B", "5. C") == ["1", "2", "5"]
    assert sections("1. A", "2. B", "6. C") == ["1", "2", "2"]


def test_first_number_must_be_small_unless_confirmed():
    assert sections("5. stray", "text") == [None, None]
    # train 145 starts at "Article 4", and "Article 5" confirms it
    assert sections("Article 4 – Copies", "Article 5 – Refusal") == ["4", "5"]


def test_number_above_60_is_not_a_section():
    assert sections("1. A", "61. B") == ["1", "1"]


def test_sub_heading_needs_current_or_next_top():
    assert sections("2. A", "2.1 x", "3.1 y", "1.5 z") == ["2", "2.1", "3.1", "3.1"]


def test_renumbering_from_1_is_accepted():
    # dev 9: "A." groups, and "1." starts again under "B."
    # Known inaccuracy: both groups give keys "1"-"2", so section_spans("1") finds both.
    texts = ("A. DEFINITIONS", "1. one", "2. two", "B. TERMS", "1. again", "2. again")
    assert sections(*texts) == [None, "1", "2", "2", "1", "2"]
    assert [s.span_id for s in section_spans(annotate_contract(make_contract(*texts)), "1")] == [1, 4]


def test_letters_are_not_sections_when_numbers_exist():
    # dev 10: A.-C. is the preamble
    assert sections("A. Recital", "B. Recital", "1. Terms") == [None, None, "1"]


def test_letters_are_sections_without_numbers():
    assert sections("Intro", "A. DEFINITIONS", "text", "B. USE", "Z. stray") == [None, "A", "A", "B", "B"]


# --- definitions -------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "terms"),
    [
        ('"Confidential Information" shall mean any data', ["Confidential Information"]),
        ('"Purpose" means discussions', ["Purpose"]),
        ('"Affiliate" shall have the meaning set out below', ["Affiliate"]),
        ('"Data" refers to all data', ["Data"]),
        ('"Data" shall refer to all data', ["Data"]),
        ('"Information" includes notes', ["Information"]),
        ('"Information" shall include notes', ["Information"]),
        # dev 411 S18: a negative definition
        ('The term "Confidential Information" does not include information which', ["Confidential Information"]),
        ('"Information" shall not include public data', ["Information"]),
        ('the Agreement (the "Agreement") is made', ["Agreement"]),
        ('Recipient (collectively, "Representatives")', ["Representatives"]),
        ('This Agreement (hereinafter referred to as the "Agreement")', ["Agreement"]),
        ('each party ("Party")', ["Party"]),
        ('_____ is referred to as "Buyer" and', ["Buyer"]),  # dev 64 S9
        ('is hereinafter collectively referred to as the "Information"', ["Information"]),
    ],
)
def test_definition_forms(text, terms):
    assert find_definitions(text) == terms


@pytest.mark.parametrize(
    ("text", "terms"),
    [
        ("THIS NON-DISCLOSURE AGREEMENT (this “Agreement”) is", ["Agreement"]),  # dev 32 S1
        ('_____(hereinafter called "BROOKSTECH"), and', ["BROOKSTECH"]),  # dev 45 S2
        ("a secure database (“the Database”) for", ["Database"]),  # dev 9 S41
        ("Any reference to “day” shall mean a period", ["day"]),  # dev 7 S181
        ("the term 'confidential information' means information", ["confidential information"]),
        ("“Specified Scope” has the meaning given in Clause 5.1.", ["Specified Scope"]),
        ("“Confidential Information” is defined as any device", ["Confidential Information"]),
        ('The term "person" as used in this letter shall be broadly interpreted to include', ["person"]),
        ('the expressions "subsidiary", "holding company" and "subsidiary undertaking" shall have '
         "the meanings given", ["subsidiary", "holding company", "subsidiary undertaking"]),
        ("________________, the “Property”.", ["Property"]),  # dev 64 S14
        ('shall be referred to herein, collectively, as the "Indemnified Parties" and, each '
         'individually, as an "Indemnified Party": ', ["Indemnified Parties", "Indemnified Party"]),
        ("are sometimes referred to below individually as a “Party” and collectively as the "
         "“Parties”. ", ["Party", "Parties"]),  # dev 73 S2
    ],
)
def test_definition_forms_found_in_dev(text, terms):
    """Forms added while tuning on dev (doc section 3.3 does not list them)."""
    assert find_definitions(text) == terms


@pytest.mark.parametrize(
    "text",
    [
        '"Confidential Information" means data',
        "“Confidential Information” means data",
        "'Confidential Information' means data",
        "‘Confidential Information’ means data",
    ],
)
def test_quote_styles(text):
    assert find_definitions(text) == ["Confidential Information"]


def test_apostrophe_is_not_a_quote():
    assert find_definitions("the Party's obligations and the Recipient's 'Agents' means") == ["Agents"]
    assert find_definitions("the Parties' Representatives' duties include care") == []


def test_two_terms_in_one_sentence():
    text = 'between X and Y (each a "Party" and together the "Parties")'
    assert find_definitions(text) == ["Party", "Parties"]


def test_duplicates_are_removed_in_order():
    text = 'the Merger (the "Merger Agreement"), dated (the "Merger Agreement"), and ("AUGI")'
    assert find_definitions(text) == ["Merger Agreement", "AUGI"]


def test_term_spaces_are_normalized():
    assert find_definitions('(the "Confidential   Information ")') == ["Confidential Information"]


def test_not_definitions():
    assert find_definitions('marked as "Confidential" when disclosed') == []
    assert find_definitions('(as that term is defined in the Merger Agreement) "Transaction Document"') == []
    assert find_definitions("Hereinafter referred to as “………………..”") == []  # dev 3 S6: a blank
    assert find_definitions('(including "Data")') == []


def test_definition_spans_ignores_case_and_returns_empty_when_missing():
    contract = annotate_contract(make_contract(
        '"Recipient" means the party that receives.', "Other text.",
    ))
    assert [s.span_id for s in definition_spans(contract, "recipient")] == [0]
    # dev 563 uses "Receiving Party" but defines only "Recipient"
    assert definition_spans(contract, "Receiving Party") == []


# --- cross-references --------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "refs"),
    [
        ("as set out in Section 6.", ["6"]),
        ("under section 2.06 above", ["2.06"]),
        ("subject to Clause 14.1", ["14.1"]),
        ("as defined in Clause 1.2(a)", ["1.2"]),
        ("as defined in Section 2(a) hereof", ["2"]),  # dev 411 S24: not excluded
        ("Paragraph 4E applies", ["4"]),
        ("the limitation set forth in Paragraph 6K above", ["6"]),  # dev 64 S114
        ("the rights in Article II", ["II"]),
        ("Section 5 of this Agreement survives", ["5"]),
        ("paragraph 3 of this NDA", ["3"]),
        ("as provided in paragraph 4 of this letter agreement", ["4"]),  # train and dev: 2 times
        # Known gap: a plural reference gives only the first number.
        ("Sections 3 and 4 survive", ["3"]),
    ],
)
def test_ref_forms(text, refs):
    assert find_refs(text) == refs


def test_refs_outside_the_contract_are_excluded():
    # dev 411 S15 and dev 456 S2
    assert find_refs("registered under Section 12 of the Securities Exchange Act of 1934") == []
    assert find_refs("see Section A of this Attachment A") == []
    assert find_refs("within the meaning of Section 13(d)(3) of the Exchange Act") == []


def test_heading_is_not_a_ref_to_itself():
    assert find_refs("ARTICLE 1 - DEFINITIONS") == []
    assert find_refs("Nothing contained in this Section 1 shall") == ["1"]


def test_annotate_keeps_only_refs_that_exist():
    contract = annotate_contract(make_contract(
        "1. Scope", "See Section 2 and Section 9.", "2. Use", "Except as in Section 1.",
    ))
    assert [s.refs for s in contract.spans] == [[], ["2"], [], ["1"]]


# --- section lookup ----------------------------------------------------------


@pytest.mark.parametrize(
    ("key", "norm"),
    [
        ("Section 2(a)", "2"),
        ("2.06", "2.06"),
        ("4E", "4"),
        ("Article II", "II"),
        # A number with 3 digits is not a section; it is not cut to its first 2 digits.
        ("123", None),
        ("Section 100", None),
    ],
)
def test_normalize_key(key, norm):
    assert normalize_key(key) == norm


def test_section_spans_includes_subsections_and_falls_back():
    contract = annotate_contract(make_contract(
        "1. Definitions", "text 1", "2. Use", "2.01 first", "2.06 sixth", "text 2.06", "3. End",
    ))
    assert [s.span_id for s in section_spans(contract, "2")] == [2, 3, 4, 5]
    assert [s.span_id for s in section_spans(contract, "2.06")] == [4, 5]
    assert [s.span_id for s in section_spans(contract, "Section 2(a)")] == [2, 3, 4, 5]
    assert [s.span_id for s in section_spans(contract, "2.9")] == [2, 3, 4, 5]  # falls back to "2"
    assert section_spans(contract, "7") == []
    assert section_spans(contract, "no number") == []
    assert section_spans(contract, "Section 100") == []  # not section "10" or "1"


def test_annotate_does_not_change_input():
    contract = make_contract("1. Scope", '"Data" means data.')
    annotated = annotate_contract(contract)
    assert contract.spans[1].defines == [] and contract.spans[0].section is None
    assert annotated.spans[1].defines == ["Data"] and annotated.spans[1].is_definition
    assert annotated.spans[1].section == "1"


# --- dev data ----------------------------------------------------------------


@pytest.fixture(scope="module")
def dev(contractnli_available) -> list[Contract]:
    return load_contractnli("dev")


def test_b0_prompt_does_not_change(dev):
    assert prompt_hash() == "50fb0745f4aa"
    raw = {c.contract_id: c for c in load_contractnli("dev", annotate=False)}
    for contract in dev:
        assert format_contract(contract) == format_contract(raw[contract.contract_id])


def test_is_definition_matches_defines(dev):
    for contract in dev:
        for span in contract.spans:
            assert span.is_definition == bool(span.defines)


def test_every_ref_is_found(dev):
    for contract in dev:
        for span in contract.spans:
            for ref in span.refs:
                assert section_spans(contract, ref), (contract.contract_id, span.span_id, ref)


def test_notes_contracts(dev):
    """Spot checks from notes.md section 3."""
    by_id = {c.contract_id: c for c in dev}
    c411 = by_id["411"]
    assert c411.spans[9].section == "1" and c411.spans[16].section == "2"
    assert c411.spans[18].defines == ["Confidential Information"]  # negative definition
    assert c411.spans[24].refs == ["2"]  # Section 2(a) hereof
    assert c411.spans[15].refs == []  # Section 12 of the Securities Exchange Act
    assert by_id["456"].spans[2].section == "1"  # "B. GENERAL" is not a section
    assert {"Recipient"} <= {t for s in by_id["563"].spans for t in s.defines}
    assert by_id["64"].spans[114].refs == ["6"]  # Paragraph 6K above
