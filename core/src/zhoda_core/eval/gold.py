"""Gold sidecar. Импортировать только из scoring/validate-gold, не из runner."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from .cases import SPECS, PilotSpec

LABEL_PROVISIONAL = "provisional"
LABEL_DISPUTED = "disputed"
ANNOTATOR_NOT_INDEPENDENT = "protocol-author-not-independent"
COMPARE_FIELDS = ("expected_action", "abstain_policy", "unacceptable_claims")
ABSTAIN_POLICIES = ("required", "forbidden", "allowed")
KAPPA_P5_MIN = 0.6

REPO_ROOT = Path(__file__).resolve().parents[4]
PILOT_DIR = REPO_ROOT / "core" / "eval" / "pilot"
GOLD_JSONL = PILOT_DIR / "gold.jsonl"
GOLD_ANNOTATOR_B_JSONL = PILOT_DIR / "gold-annotator-b.jsonl"
GOLD_MERGED_DRAFT_JSONL = PILOT_DIR / "gold-merged-draft.jsonl"
DISAGREEMENT_LOG = REPO_ROOT / "docs" / "eval" / "disagreement-log.md"


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


def load_label_rows(path: Path) -> dict[str, dict[str, object]]:
    """Сырые JSONL-строки по id. Не требует label_status (файл B)."""
    out: dict[str, dict[str, object]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        raw = json.loads(line)
        out[str(raw["id"])] = raw
    return out


def field_disagreements(
    a_rows: Mapping[str, Mapping[str, object]],
    b_rows: Mapping[str, Mapping[str, object]],
) -> list[tuple[str, str, object, object]]:
    """Точное сравнение COMPARE_FIELDS. Семантику не схлопываем — это adjudication."""
    diffs: list[tuple[str, str, object, object]] = []
    for case_id in a_rows:
        a = a_rows[case_id]
        b = b_rows[case_id]
        for field in COMPARE_FIELDS:
            av = a[field]
            bv = b[field]
            if av != bv:
                diffs.append((case_id, field, av, bv))
    return diffs


def agreement_rate(pairs: Sequence[tuple[object, object]]) -> float:
    if not pairs:
        return 1.0
    return sum(1 for a, b in pairs if a == b) / len(pairs)


def cohen_kappa(
    a: Sequence[str],
    b: Sequence[str],
    labels: Sequence[str] = ABSTAIN_POLICIES,
) -> float:
    """Cohen's κ. pe=1 и полное согласие → 1.0 (иначе 0.0)."""
    n = len(a)
    if n == 0 or n != len(b):
        raise ValueError("cohen_kappa: длины меток должны совпадать и быть > 0")
    po = sum(1 for x, y in zip(a, b, strict=True) if x == y) / n
    pe = 0.0
    for lab in labels:
        pa = sum(1 for x in a if x == lab) / n
        pb = sum(1 for y in b if y == lab) / n
        pe += pa * pb
    if pe >= 1.0:
        return 1.0 if po >= 1.0 else 0.0
    return (po - pe) / (1.0 - pe)


def _cell(value: object) -> str:
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, ensure_ascii=False)
    return text.replace("|", "\\|").replace("\n", " ")


def render_disagreement_log(
    a_rows: Mapping[str, Mapping[str, object]],
    b_rows: Mapping[str, Mapping[str, object]],
    diffs: Sequence[tuple[str, str, object, object]],
) -> str:
    ids = list(a_rows)
    n = len(ids)
    action_pairs = [(a_rows[i]["expected_action"], b_rows[i]["expected_action"]) for i in ids]
    policy_a = [str(a_rows[i]["abstain_policy"]) for i in ids]
    policy_b = [str(b_rows[i]["abstain_policy"]) for i in ids]
    claims_pairs = [
        (a_rows[i]["unacceptable_claims"], b_rows[i]["unacceptable_claims"]) for i in ids
    ]
    action_n = sum(1 for a, b in action_pairs if a == b)
    policy_n = sum(1 for a, b in zip(policy_a, policy_b, strict=True) if a == b)
    claims_n = sum(1 for a, b in claims_pairs if a == b)
    action_p = agreement_rate(action_pairs)
    policy_p = agreement_rate(list(zip(policy_a, policy_b, strict=True)))
    claims_p = agreement_rate(claims_pairs)
    kappa = cohen_kappa(policy_a, policy_b)
    annotator_a = str(next(iter(a_rows.values()))["annotator"])
    annotator_b = str(next(iter(b_rows.values()))["annotator"])
    disputed = sorted({cid for cid, _field, _av, _bv in diffs})
    lines = [
        "# Disagreement log — pilot gold",
        "",
        "Status: **OPEN — awaiting owner resolution**. Not adjudicated.",
        "",
        "Comparison is **exact** on `expected_action`, `abstain_policy`, and",
        "`unacceptable_claims` (list equality, order-sensitive). Paraphrases are",
        "logged, not merged. `resolution` / `resolved_by` stay empty until the owner",
        "fills them. Do not treat this table as agreement.",
        "",
        f"- n_cases: {n}",
        f"- expected_action exact: {action_n}/{n} = {action_p:.3f}",
        f"- abstain_policy exact: {policy_n}/{n} = {policy_p:.3f}",
        f"- abstain_policy Cohen's κ: {kappa:.3f} (labels {', '.join(ABSTAIN_POLICIES)})",
        f"- unacceptable_claims exact: {claims_n}/{n} = {claims_p:.3f}",
        f"- disagreement rows: {len(diffs)}",
        f"- disputed case ids: {len(disputed)}",
        f"- kappa P5 gate ({KAPPA_P5_MIN}): "
        + ("triggered (gold not ready for P5)" if kappa < KAPPA_P5_MIN else "not triggered"),
        "",
        "| case_id | annotator_a | annotator_b | field | a_value | b_value "
        "| resolution | resolved_by | notes |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for case_id, field, av, bv in diffs:
        notes = "exact mismatch; owner adjudicates"
        lines.append(
            f"| {case_id} | {annotator_a} | {annotator_b} | {field} | "
            f"{_cell(av)} | {_cell(bv)} |  |  | {notes} |"
        )
    lines.append("")
    return "\n".join(lines)


def _str_list(value: object) -> list[str]:
    if not isinstance(value, list):
        raise TypeError("expected list of strings")
    return [str(item) for item in value]


def merged_draft_payloads(
    a_rows: Mapping[str, Mapping[str, object]],
    diffs: Sequence[tuple[str, str, object, object]],
) -> list[dict[str, object]]:
    """Черновик = строки A; спорные кейсы только помечаем, значения не выбираем."""
    disputed_fields: dict[str, list[str]] = {}
    for case_id, field, _av, _bv in diffs:
        bucket = disputed_fields.setdefault(case_id, [])
        if field not in bucket:
            bucket.append(field)
    out: list[dict[str, object]] = []
    for case_id, row in a_rows.items():
        fields = disputed_fields.get(case_id, [])
        out.append(
            {
                "id": row["id"],
                "expected_action": row["expected_action"],
                "allowed_alternatives": _str_list(row["allowed_alternatives"]),
                "gold_conditions": _str_list(row["gold_conditions"]),
                "unacceptable_claims": _str_list(row["unacceptable_claims"]),
                "abstain_policy": row["abstain_policy"],
                "label_status": LABEL_DISPUTED if fields else row["label_status"],
                "annotator": row["annotator"],
                "disputed_fields": fields,
            }
        )
    return out


def dump_merged_draft(path: Path) -> str:
    a_rows = load_label_rows(GOLD_JSONL)
    b_rows = load_label_rows(GOLD_ANNOTATOR_B_JSONL)
    diffs = field_disagreements(a_rows, b_rows)
    lines = [json.dumps(row, ensure_ascii=False) for row in merged_draft_payloads(a_rows, diffs)]
    text = "\n".join(lines) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return hashlib.sha256(text.encode()).hexdigest()


def dump_disagreement_log(path: Path) -> str:
    a_rows = load_label_rows(GOLD_JSONL)
    b_rows = load_label_rows(GOLD_ANNOTATOR_B_JSONL)
    diffs = field_disagreements(a_rows, b_rows)
    text = render_disagreement_log(a_rows, b_rows, diffs)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return hashlib.sha256(text.encode()).hexdigest()
