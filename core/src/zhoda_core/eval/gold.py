"""Gold sidecar. Импортировать только из scoring/validate-gold, не из runner."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from .cases import SPECS, PilotSpec

LABEL_PROVISIONAL = "provisional"
ANNOTATOR_NOT_INDEPENDENT = "protocol-author-not-independent"


@dataclass(frozen=True)
class GoldRow:
    id: str
    expected_action: str
    allowed_alternatives: tuple[str, ...]
    gold_conditions: tuple[str, ...]
    unacceptable_claims: tuple[str, ...]
    abstain_policy: str
    label_status: str
    annotator: str


def gold_from_spec(spec: PilotSpec) -> GoldRow:
    return GoldRow(
        id=spec.id,
        expected_action=spec.expected_action,
        allowed_alternatives=spec.allowed_alternatives,
        gold_conditions=spec.gold_conditions,
        unacceptable_claims=spec.unacceptable_claims,
        abstain_policy=spec.abstain_policy,
        label_status=LABEL_PROVISIONAL,
        annotator=ANNOTATOR_NOT_INDEPENDENT,
    )


def gold_payload(row: GoldRow) -> dict[str, object]:
    data = asdict(row)
    data["allowed_alternatives"] = list(row.allowed_alternatives)
    data["gold_conditions"] = list(row.gold_conditions)
    data["unacceptable_claims"] = list(row.unacceptable_claims)
    return data


def dump_gold(path: Path) -> str:
    lines = [json.dumps(gold_payload(gold_from_spec(s)), ensure_ascii=False) for s in SPECS]
    text = "\n".join(lines) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return hashlib.sha256(text.encode()).hexdigest()


def load_gold(path: Path) -> dict[str, GoldRow]:
    out: dict[str, GoldRow] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        raw = json.loads(line)
        row = GoldRow(
            id=str(raw["id"]),
            expected_action=str(raw["expected_action"]),
            allowed_alternatives=tuple(raw.get("allowed_alternatives") or ()),
            gold_conditions=tuple(raw.get("gold_conditions") or ()),
            unacceptable_claims=tuple(raw.get("unacceptable_claims") or ()),
            abstain_policy=str(raw["abstain_policy"]),
            label_status=str(raw["label_status"]),
            annotator=str(raw["annotator"]),
        )
        out[row.id] = row
    return out
