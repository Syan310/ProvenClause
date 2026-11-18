import pytest
from pydantic import ValidationError

from provenclause.checklist import load_checklist
from provenclause.parse import read_split_json

VALID_ITEM = """\
- id: nda-5
  name: Sharing with employees
  hypothesis: "Receiving Party may share some Confidential Information."
  guidance: Entailment if employees may receive it.
  risk_label_for_recipient: Contradiction
"""


def write(tmp_path, text):
    (tmp_path / "c.yaml").write_text(text, encoding="utf-8")
    return tmp_path


def test_nda_17_loads():
    checklist = load_checklist("nda_17")
    assert checklist.checklist_id == "nda_17"
    assert len(checklist.items) == 17


@pytest.mark.usefixtures("contractnli_available")
def test_nda_17_matches_dataset_labels():
    labels = read_split_json("test")["labels"]
    items = {item.id: item for item in load_checklist("nda_17").items}
    assert set(items) == set(labels)
    for item_id, label in labels.items():
        assert items[item_id].hypothesis == label["hypothesis"]
        assert items[item_id].name == label["short_description"]


def test_valid_file(tmp_path):
    checklist = load_checklist("c", write(tmp_path, VALID_ITEM))
    assert checklist.items[0].risk_label_for_recipient == "Contradiction"


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("risk_label_for_recipient: Contradiction", "risk_label_for_recipient: Maybe"),
        ("risk_label_for_recipient: Contradiction", "risk_label: Contradiction"),
        # YAML reads a bare `no` as False; it must not pass as a string
        ("name: Sharing with employees", "name: no"),
        ("  guidance: Entailment if employees may receive it.\n", ""),
    ],
    ids=["bad-label", "misspelled-key", "yaml-bool", "missing-key"],
)
def test_invalid_item_rejected(tmp_path, old, new):
    with pytest.raises(ValidationError):
        load_checklist("c", write(tmp_path, VALID_ITEM.replace(old, new)))


def test_duplicate_id_rejected(tmp_path):
    with pytest.raises(ValueError, match="duplicate"):
        load_checklist("c", write(tmp_path, VALID_ITEM + VALID_ITEM))


def test_top_level_must_be_list(tmp_path):
    with pytest.raises(ValueError, match="list"):
        load_checklist("c", write(tmp_path, "id: nda-5\n"))
