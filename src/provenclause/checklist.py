"""N2 checklist: load a YAML checklist and validate it (F3: PyYAML + Pydantic)."""

from pathlib import Path

import yaml

from provenclause.models import Checklist, ChecklistItem
from provenclause.parse import PROJECT_ROOT

CHECKLIST_DIR = PROJECT_ROOT / "checklists"


def load_checklist(checklist_id: str, checklist_dir: Path = CHECKLIST_DIR) -> Checklist:
    path = checklist_dir / f"{checklist_id}.yaml"
    with path.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    if not isinstance(raw, list):
        raise ValueError(f"{path}: top level must be a list of checklist items")

    items = [ChecklistItem.model_validate(entry) for entry in raw]
    ids = [item.id for item in items]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise ValueError(f"{path}: duplicate item ids {duplicates}")
    return Checklist(checklist_id=checklist_id, items=items)
