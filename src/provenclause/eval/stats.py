"""Print dataset statistics, or one contract with its gold answers.

    uv run python -m provenclause.eval.stats
    uv run python -m provenclause.eval.stats --split dev --contract 1234
    uv run python -m provenclause.eval.stats --n1 --split dev
"""

import argparse
import statistics
import sys
from collections import Counter

from provenclause.checklist import load_checklist
from provenclause.eval.dataset import load_gold
from provenclause.eval.sample import load_test40
from provenclause.models import Contract
from provenclause.parse import find_refs, load_contractnli

LABELS = ("Entailment", "Contradiction", "NotMentioned")
NOTES_CONTRACTS = ("456", "411", "3", "563", "64")  # the 5 dev NDAs read in notes.md section 3


def print_split_stats(name: str, contracts: list[Contract], labels: Counter[str]) -> None:
    chars = [len(c.text) for c in contracts]
    n_spans = [len(c.spans) for c in contracts]
    print(
        f"{name:<7} contracts={len(contracts):>3}  "
        f"chars mean={statistics.mean(chars):>6.0f} max={max(chars):>6}  "
        f"spans mean={statistics.mean(n_spans):>5.1f}  "
        + "  ".join(f"{label}={labels[label]}" for label in LABELS)
    )


def print_all_stats() -> None:
    for split in ("train", "dev", "test"):
        contracts = load_contractnli(split)
        gold = load_gold(split)
        print_split_stats(split, contracts, Counter(label for label, _ in gold.values()))

    test40 = set(load_test40())
    contracts = [c for c in load_contractnli("test") if c.contract_id in test40]
    gold = load_gold("test")
    labels = Counter(label for (cid, _), (label, _) in gold.items() if cid in test40)
    print_split_stats("test40", contracts, labels)


def print_contract(split: str, contract_id: str) -> None:
    contract = next(c for c in load_contractnli(split) if c.contract_id == contract_id)
    gold = load_gold(split)
    print(f"# {contract.contract_id}: {contract.title} ({len(contract.spans)} spans)\n")
    for span in contract.spans:
        print(f"[S{span.span_id}] {span.text}")
    print()
    for item in load_checklist("nda_17").items:
        label, span_ids = gold[(contract_id, item.id)]
        print(f"{item.id:<7} {label:<14} {span_ids}  {item.name}")


def _distinct_terms(contract: Contract) -> list[str]:
    """Defined terms in order of first appearance, compared without case."""
    terms: dict[str, str] = {}
    for span in contract.spans:
        for term in span.defines:
            terms.setdefault(term.casefold(), term)
    return list(terms.values())


def print_n1_stats(split: str, contract_ids: list[str]) -> None:
    """Stage 2 numbers: how many spans have a section, definitions and refs per contract."""
    contracts = load_contractnli(split)
    spans = [s for c in contracts for s in c.spans]
    with_section = sum(s.section is not None for s in spans)
    n_terms = [len(_distinct_terms(c)) for c in contracts]
    n_refs = [sum(len(s.refs) for s in c.spans) for c in contracts]
    # Distinct numbers per span: found by find_refs(), but not kept because section_spans() is empty.
    n_found = sum(len(set(find_refs(s.text))) for s in spans)
    n_dropped = n_found - sum(n_refs)

    print(f"# N1 rules on {split}: {len(contracts)} contracts, {len(spans)} spans\n")
    print(f"spans with a section    {with_section}/{len(spans)} = {with_section / len(spans):.1%}")
    print(f"contracts with sections {sum(any(s.section for s in c.spans) for c in contracts)}"
          f"/{len(contracts)}")
    print(f"distinct defined terms  mean={statistics.mean(n_terms):.1f} max={max(n_terms)} "
          f"contracts with 0={n_terms.count(0)}")
    print(f"refs kept               mean={statistics.mean(n_refs):.2f} total={sum(n_refs)}")
    print(f"refs dropped            {n_dropped} of {n_found} found by find_refs()")

    by_id = {c.contract_id: c for c in contracts}
    for contract_id in contract_ids:
        contract = by_id.get(contract_id)
        if contract is None:
            continue
        keys = list(dict.fromkeys(s.section for s in contract.spans if s.section is not None))
        terms = _distinct_terms(contract)
        print(f"\n## {contract_id}: {contract.title} ({len(contract.spans)} spans)")
        print(f"sections ({len(keys)}): {keys}")
        print(f"terms ({len(terms)}): {terms}")
        for s in contract.spans:
            if s.refs:
                print(f"  S{s.span_id} [{s.section}] refs={s.refs}: {s.text[:100]}")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")  # contract text is not GBK-safe on Windows
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--split", default="dev", choices=["train", "dev", "test"])
    parser.add_argument("--contract", help="contract_id to print")
    parser.add_argument("--n1", action="store_true", help="print stage 2 N1 rule statistics")
    args = parser.parse_args()

    if args.n1:
        if args.split == "test":
            parser.error("--n1 runs on train or dev only; test is for stage 7")
        print_n1_stats(args.split, list(NOTES_CONTRACTS) if args.split == "dev" else [])
    elif args.contract:
        print_contract(args.split, args.contract)
    else:
        print_all_stats()
    return 0


if __name__ == "__main__":
    sys.exit(main())
