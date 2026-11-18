"""N1 contract parsing.

Stage 0: read the official ContractNLI package. The dataset's own spans are
used as-is, so span ids match the gold evidence ids.
Stage 1: format_contract() gives the numbered text that B0, A1 and A2 read.
Stage 2: annotate_contract() fills each span's section, defines and refs with
regular expressions. format_contract() does not show them, so the B0 prompt
does not change.
"""

import json
import re
from pathlib import Path
from typing import Any, Literal
from urllib.parse import unquote

from provenclause.models import Contract, Span

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONTRACTNLI_DIR = PROJECT_ROOT / "data" / "contract-nli"

Split = Literal["train", "dev", "test"]


def read_split_json(split: Split, data_dir: Path = CONTRACTNLI_DIR) -> dict[str, Any]:
    path = data_dir / f"{split}.json"
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found. Download contract-nli.zip from "
            "https://stanfordnlp.github.io/contract-nli/ and unzip it into data/."
        )
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _to_contract(doc: dict[str, Any]) -> Contract:
    text = doc["text"]
    spans = [
        Span(span_id=i, start=start, end=end, text=text[start:end])
        for i, (start, end) in enumerate(doc["spans"])
    ]
    return Contract(
        contract_id=str(doc["id"]),
        source="contractnli",
        title=unquote(doc["file_name"]),
        text=text,
        spans=spans,
    )


def load_contractnli(
    split: Split, data_dir: Path = CONTRACTNLI_DIR, *, annotate: bool = True
) -> list[Contract]:
    contracts = [_to_contract(doc) for doc in read_split_json(split, data_dir)["documents"]]
    return [annotate_contract(c) for c in contracts] if annotate else contracts


def format_contract(contract: Contract) -> str:
    """One span per line: "[S{span_id}] {text}". Dataset spans have no newlines."""
    return "\n".join(f"[S{span.span_id}] {span.text}" for span in contract.spans)


# --- Stage 2: section, definition and cross-reference rules ------------------

MAX_SECTION_NUMBER = 60
MAX_HEADING_JUMP = 3  # a heading may skip up to 2 missing numbers, e.g. 15. -> 18.

_ROMAN = r"[IVXLC]+"
_ROMAN_VALUES = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100}

# Candidate headings, matched at the start of a sentence after leading whitespace.
_HEADING_SUB = re.compile(r"(\d{1,2})\.(\d{1,2})\.?(?=\s|$)")  # 1.1, 1.01, 15.10.
_HEADING_TOP = re.compile(r"(\d{1,2})\.(?=\s|$)")  # 1.
_HEADING_WORD = re.compile(  # ARTICLE 1 - DEFINITIONS, ARTICLE II, Section 5.
    rf"(?:ARTICLE|Article|SECTION|Section)\s+(\d{{1,2}}|{_ROMAN})(?:\.(\d{{1,2}}))?"
    r"(?=\s*[.:\-–]|\s+[A-Z]|\s*$)"
)
_HEADING_LETTER = re.compile(r"([A-Z])\.(?=\s)")  # A. DEFINITIONS

# A quoted term. The open quote follows the start, whitespace or "(", so the
# apostrophe in "Party's" is not an open quote. No letter follows the close quote.
# "“" also closes, because some PDFs have “Authority“.
_QUOTED = re.compile(
    r"(?:^|(?<=[\s(]))"
    r"(?:[\"“]([A-Za-z0-9][^\"“”]{0,63}?)[\"”“]|['‘]([A-Za-z0-9][^'‘’]{0,63}?)['’])"
    r"(?![A-Za-z])"
)
_DEFINE_VERB = re.compile(
    r"\s*,?\s*(?:as\s+used\s+(?:herein|in\s+this\s+\w+)\s*,?\s*)?"  # "person" as used in this Agreement
    r"(?:means|shall,?\s+mean|(?:shall\s+have|has)\s+the\s+meanings?|refers\s+to|shall\s+refer\s+to"
    r"|includes|shall\s+include|does\s+not\s+include|shall\s+not\s+include"
    r"|(?:is|shall\s+be)\s+defined\s+as|shall\s+be\s+deemed\s+to\s+include"
    r"|shall\s+be\s+(?:broadly\s+)?(?:interpreted|construed)\s+to\s+include)\b"
)
# "X", "Y" and "Z" shall have the meanings ...: the terms before the one next to the verb.
_LIST_GAP = re.compile(r"\s*,?\s*(?:and|or)?\s*")
_REFERRED_AS = re.compile(
    r"\b(?:referred\s+to|called)"
    r"(?:[\s,]+(?:herein|hereinafter|hereafter|below|collectively|individually|jointly|each))*"
    r"[\s,]+as\b"
)
# "..., the “Property”." at the end of a sentence.
_APPOSITIVE = re.compile(r"(?:^|,)\s*the\s+$")
# Words that may stand between "(" or "referred to as" and a quoted term, and
# between two quoted terms, e.g. (each a "Party" and together the "Parties").
_CHAIN_WORDS = frozenset(
    "the a an this each collectively individually jointly together or and also "
    "hereinafter hereafter herein referred called to as".split()
)

_REF = re.compile(
    rf"\b(?i:sections?|paragraphs?|clauses?|articles?)\s+(\d{{1,2}}(?:\.\d{{1,3}})*(?!\d)|{_ROMAN}\b)"
    r"(?:[A-Z](?![A-Za-z]))?"  # Paragraph 4E
    r"(?:\s?\([A-Za-z0-9]{1,5}\))*"  # Section 2(a)(ii)
)
# "of this ..." is this contract ("of this Agreement", "of this letter agreement").
_REF_OUTSIDE = re.compile(r"\s+of\s+(?!this\b|hereof\b)", re.IGNORECASE)
# (?!\d) as in _REF: "123" is not section "12".
_KEY = re.compile(
    rf"\s*(?:(?i:sections?|paragraphs?|clauses?|articles?)\s+)?"
    rf"(\d{{1,2}}(?:\.\d{{1,3}})*(?!\d)|{_ROMAN}\b)"
)


def _int_to_roman(n: int) -> str:
    out = ""
    for value, numeral in ((50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")):
        while n >= value:
            out += numeral
            n -= value
    return out


def _section_number(token: str) -> int | None:
    """Value of a section number such as "12" or "II"; None outside 1-60 or not canonical."""
    if token.isdigit():
        n = int(token)
    else:
        values = [_ROMAN_VALUES[ch] for ch in token]
        n = sum(-v if i + 1 < len(values) and v < values[i + 1] else v for i, v in enumerate(values))
        if not 1 <= n <= MAX_SECTION_NUMBER or _int_to_roman(n) != token:
            return None
    return n if 1 <= n <= MAX_SECTION_NUMBER else None


def _numeric_heading(text: str) -> tuple[str, int, bool] | None:
    """(section key, top-level number, has a sub-level) of a numeric heading, or None."""
    text = text.lstrip()
    m = _HEADING_SUB.match(text)
    if m is None:
        m = _HEADING_TOP.match(text) or _HEADING_WORD.match(text)
    if m is None:
        return None
    top = _section_number(m.group(1))
    if top is None:
        return None
    sub = m.group(2) if m.re.groups == 2 else None
    return (f"{m.group(1)}.{sub}", top, True) if sub is not None else (m.group(1), top, False)


def _letter_heading(text: str) -> tuple[str, int, bool] | None:
    m = _HEADING_LETTER.match(text.lstrip())
    return (m.group(1), ord(m.group(1)) - ord("A") + 1, False) if m else None


def _accept_in_order(candidates: list[tuple[str, int, bool] | None]) -> list[str | None]:
    """Keep the candidates whose top-level numbers run in order; carry the key forward.

    The first top-level number is at most 2. Each next one is the same, one more,
    or 1 (numbering starts again; not for "N.M"). A number may also jump forward
    (dev 582 has no "9.") when the next candidate confirms it: the next top
    number is from N to N + 2. The last candidate has nothing to confirm it, so
    it may jump at most MAX_HEADING_JUMP.
    """
    sections: list[str | None] = []
    key: str | None = None
    top: int | None = None
    for i, candidate in enumerate(candidates):
        if candidate is not None:
            cand_key, n, has_sub = candidate
            following = next((c[1] for c in candidates[i + 1 :] if c is not None), None)
            confirmed = following is not None and n <= following <= n + 2
            if top is None:
                ok = n <= 2 or confirmed
            elif n in (top, top + 1) or (n == 1 and not has_sub):
                ok = True
            elif n > top + 1:
                ok = confirmed or (following is None and n <= top + MAX_HEADING_JUMP)
            else:
                ok = False
            if ok:
                key, top = cand_key, n
        sections.append(key)
    return sections


def detect_sections(spans: list[Span]) -> list[str | None]:
    """Section key of each span: the deepest "N" / "N.M" heading above it.

    Letter headings "A.", "B." count only when the contract has no numeric heading.
    """
    sections = _accept_in_order([_numeric_heading(s.text) for s in spans])
    if any(s is not None for s in sections):
        return sections
    return _accept_in_order([_letter_heading(s.text) for s in spans])


def _clean_term(m: re.Match[str], *, allow_lower: bool = False) -> str | None:
    """The quoted term with spaces normalized, or None when it is not a term.

    A leading "the " inside the quotes is dropped: (“the Database”) defines "Database".
    A term starts with a capital letter or a digit; allow_lower also accepts a
    lowercase start, which is used only when a defining verb follows ("day" shall mean).
    """
    term = " ".join((m.group(1) or m.group(2)).split()).rstrip(",.;:")
    if term.startswith("the ") and term[4:5].isupper():
        term = term[4:]
    if not 1 <= len(term) <= 60 or len(term.split()) > 8:
        return None
    if not (term[0].isupper() or term[0].isdigit() or allow_lower):
        return None
    return term


def _chain(text: str, pos: int, end: int, quoted: list[re.Match[str]]) -> list[tuple[int, str]]:
    """Quoted terms from pos to end that only chain words separate from pos and each other."""
    found: list[tuple[int, str]] = []
    for m in quoted:
        if m.start() < pos:
            continue
        if m.start() >= end:
            break
        gap = text[pos : m.start()]
        if re.fullmatch(r"[\s,;A-Za-z]*", gap) is None or any(
            w.lower() not in _CHAIN_WORDS for w in re.findall(r"[A-Za-z]+", gap)
        ):
            break
        if term := _clean_term(m):
            found.append((m.start(), term))
        pos = m.end()
    return found


def find_definitions(text: str) -> list[str]:
    """Terms that this sentence defines, in order of appearance, without duplicates."""
    quoted = list(_QUOTED.finditer(text))
    if not quoted:
        return []
    found: list[tuple[int, str]] = []

    # "X" means / shall mean / does not include ..., also "X", "Y" and "Z" shall have the meanings
    for i, m in enumerate(quoted):
        if not _DEFINE_VERB.match(text, m.end()):
            continue
        j = i
        while j > 0 and _LIST_GAP.fullmatch(text, quoted[j - 1].end(), quoted[j].start()):
            j -= 1
        for listed in quoted[j : i + 1]:
            if term := _clean_term(listed, allow_lower=True):
                found.append((listed.start(), term))

    # (the "Agreement"), (collectively, "Representatives"), (hereinafter called "X")
    for i, ch in enumerate(text):
        if ch == "(":
            close = text.find(")", i)
            found += _chain(text, i + 1, close if close != -1 else len(text), quoted)

    # is hereinafter collectively referred to as the "Information"
    for m in _REFERRED_AS.finditer(text):
        found += _chain(text, m.end(), len(text), quoted)

    # ..., the “Property”.
    for m in quoted:
        if (
            _APPOSITIVE.search(text, 0, m.start())
            and re.fullmatch(r"[\s.;]*", text[m.end() :])
            and (term := _clean_term(m))
        ):
            found.append((m.start(), term))

    terms: list[str] = []
    for _, term in sorted(found, key=lambda pair: pair[0]):
        if term not in terms:
            terms.append(term)
    return terms


def normalize_key(key: str) -> str | None:
    """"Section 2(a)" -> "2", "2.06" -> "2.06", "4E" -> "4", "Article II" -> "II"."""
    m = _KEY.match(key)
    return m.group(1) if m else None


def find_refs(text: str) -> list[str]:
    """Normalized section numbers that this sentence refers to, before the existence filter.

    Known gap: "Sections 3 and 4" gives only "3". A reference followed by "of ..."
    points outside the contract and is dropped, except "of this ..." (for example
    "of this Agreement", "of this letter agreement"). A heading such as
    "ARTICLE 1 - DEFINITIONS" is not a reference to itself.
    """
    heading_at = len(text) - len(text.lstrip()) if _HEADING_WORD.match(text.lstrip()) else -1
    refs: list[str] = []
    for m in _REF.finditer(text):
        number = m.group(1)
        if (
            m.start() == heading_at
            or _section_number(number.split(".")[0]) is None
            or _REF_OUTSIDE.match(text, m.end())
        ):
            continue
        refs.append(number)
    return refs


def section_spans(contract: Contract, key: str) -> list[Span]:
    """Spans of section key and its subsections. "2.9" falls back to "2" when it is missing."""
    norm = normalize_key(key)
    while norm is not None:
        spans = [
            s for s in contract.spans
            if s.section is not None and (s.section == norm or s.section.startswith(norm + "."))
        ]
        if spans or "." not in norm:
            return spans
        norm = norm.rsplit(".", 1)[0]
    return []


def definition_spans(contract: Contract, term: str) -> list[Span]:
    """Spans whose defines contain term, compared without case. No match gives []."""
    wanted = " ".join(term.split()).casefold()
    return [s for s in contract.spans if any(t.casefold() == wanted for t in s.defines)]


def annotate_contract(contract: Contract) -> Contract:
    """A new Contract with section, is_definition, defines and refs filled in.

    refs keeps only the numbers that section_spans() finds in this contract.
    """
    sections = detect_sections(contract.spans)
    spans = []
    for span, section in zip(contract.spans, sections, strict=True):
        defines = find_definitions(span.text)
        spans.append(span.model_copy(
            update={"section": section, "defines": defines, "is_definition": bool(defines)}
        ))
    sectioned = contract.model_copy(update={"spans": spans})

    annotated = []
    for span in spans:
        refs: list[str] = []
        for ref in find_refs(span.text):
            if ref not in refs and section_spans(sectioned, ref):
                refs.append(ref)
        annotated.append(span.model_copy(update={"refs": refs}))
    return contract.model_copy(update={"spans": annotated})
