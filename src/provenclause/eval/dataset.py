"""Gold labels and evidence from the official ContractNLI package."""

from pathlib import Path

from provenclause.models import Label
from provenclause.parse import CONTRACTNLI_DIR, Split, read_split_json

# (contract_id, item_id) -> (label, gold evidence span ids)
Gold = dict[tuple[str, str], tuple[Label, list[int]]]


def load_gold(split: Split, data_dir: Path = CONTRACTNLI_DIR) -> Gold:
    gold: Gold = {}
    for doc in read_split_json(split, data_dir)["documents"]:
        # The package has exactly one annotation set per document.
        annotations = doc["annotation_sets"][0]["annotations"]
        for item_id, ann in annotations.items():
            gold[(str(doc["id"]), item_id)] = (ann["choice"], list(ann["spans"]))
    return gold
