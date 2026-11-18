"""Stage 0 acceptance: the dataset loads and matches the published numbers."""

from collections import Counter

import pytest

from provenclause.eval.dataset import load_gold
from provenclause.eval.sample import load_test40, render, sample_test40, TEST40_PATH
from provenclause.parse import load_contractnli, read_split_json

pytestmark = pytest.mark.usefixtures("contractnli_available")


@pytest.mark.parametrize(("split", "n"), [("train", 423), ("dev", 61), ("test", 123)])
def test_split_sizes(split, n):
    assert len(load_contractnli(split)) == n


def test_607_unique_contract_ids():
    ids = [c.contract_id for split in ("train", "dev", "test") for c in load_contractnli(split)]
    assert len(ids) == len(set(ids)) == 607


@pytest.mark.parametrize("split", ["train", "dev", "test"])
def test_span_text_matches_contract_text(split):
    for contract in load_contractnli(split):
        assert [s.span_id for s in contract.spans] == list(range(len(contract.spans)))
        for span in contract.spans:
            assert span.text == contract.text[span.start : span.end]


def test_test_label_distribution():
    labels = Counter(label for label, _ in load_gold("test").values())
    assert labels == {"Entailment": 968, "NotMentioned": 903, "Contradiction": 220}


@pytest.mark.parametrize("split", ["train", "dev", "test"])
def test_gold_shape(split):
    gold = load_gold(split)
    contracts = {c.contract_id: c for c in load_contractnli(split)}
    assert len(gold) == len(contracts) * 17
    for (contract_id, _), (label, span_ids) in gold.items():
        if label == "NotMentioned":
            assert span_ids == []
        else:
            assert span_ids, "Entailment / Contradiction must cite evidence"
        assert all(0 <= i < len(contracts[contract_id].spans) for i in span_ids)


def test_test40_is_reproducible():
    assert sample_test40() == sample_test40()
    assert TEST40_PATH.read_text(encoding="utf-8") == render(sample_test40())


def test_test40_ids_are_test_contracts():
    ids = load_test40()
    test_ids = {str(doc["id"]) for doc in read_split_json("test")["documents"]}
    assert len(ids) == len(set(ids)) == 40
    assert set(ids) <= test_ids
