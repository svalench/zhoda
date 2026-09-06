"""A vs B: точное сравнение полей, gold.jsonl не переписываем."""

from __future__ import annotations

import json
from pathlib import Path

from zhoda_core.eval.gold import (
    DISAGREEMENT_LOG,
    DISAGREEMENT_STATUS,
    GOLD_ANNOTATOR_B_JSONL,
    GOLD_JSONL,
    GOLD_MERGED_DRAFT_JSONL,
    HOL_MIN_IDS,
    KAPPA_P5_MIN,
    LABEL_DISPUTED,
    LABEL_PROVISIONAL,
    OWNER_CLAIMS_RESOLUTION,
    OWNER_RESOLVED_BY,
    P5_GATE_FIELD,
    cohen_kappa,
    dump_disagreement_log,
    dump_merged_draft,
    field_disagreements,
    load_label_rows,
    p5_expected_action_unresolved,
    p5_gate_blocks,
)
from zhoda_core.eval.pilot import FROZEN_MANIFEST, file_hash

STATUS_MD = Path(__file__).resolve().parents[2] / "docs" / "eval" / "decision-review-status.md"
P5_NOT_READY = "Gold is not ready for P5"


def test_gold_jsonl_hash_unchanged_by_merge_draft() -> None:
    committed = json.loads(FROZEN_MANIFEST.read_text(encoding="utf-8"))
    assert file_hash(GOLD_JSONL) == committed["gold_hash"]
    gold = load_label_rows(GOLD_JSONL)
    assert gold
    assert all(row["label_status"] == LABEL_PROVISIONAL for row in gold.values())
    assert all("not-independent" in str(row["annotator"]) for row in gold.values())


def test_merged_draft_marks_exact_field_disputes() -> None:
    a_rows = load_label_rows(GOLD_JSONL)
    b_rows = load_label_rows(GOLD_ANNOTATOR_B_JSONL)
    assert set(a_rows) == set(b_rows)
    diffs = field_disagreements(a_rows, b_rows)
    disputed_ids = {cid for cid, _field, _av, _bv in diffs}
    draft = load_label_rows(GOLD_MERGED_DRAFT_JSONL)
    assert list(draft) == list(a_rows)
    for case_id, row in draft.items():
        if case_id in disputed_ids:
            assert row["label_status"] == LABEL_DISPUTED
            assert row["disputed_fields"]
        else:
            assert row["label_status"] == LABEL_PROVISIONAL
            assert row["disputed_fields"] == []
        assert P5_GATE_FIELD not in row["disputed_fields"]
        assert row["gate_disputed"] is False
        if case_id in HOL_MIN_IDS:
            assert row["disputed_fields"] == ["unacceptable_claims"]
        assert row["expected_action"] == a_rows[case_id]["expected_action"]
        assert row["abstain_policy"] == a_rows[case_id]["abstain_policy"]
        assert row["unacceptable_claims"] == a_rows[case_id]["unacceptable_claims"]
        assert row["annotator"] == a_rows[case_id]["annotator"]


def test_disagreement_log_has_owner_resolution() -> None:
    a_rows = load_label_rows(GOLD_JSONL)
    b_rows = load_label_rows(GOLD_ANNOTATOR_B_JSONL)
    diffs = field_disagreements(a_rows, b_rows)
    log = DISAGREEMENT_LOG.read_text(encoding="utf-8")
    assert "awaiting owner resolution" not in log
    assert DISAGREEMENT_STATUS in log
    for case_id, field, _av, _bv in diffs:
        assert f"| {case_id} |" in log
        assert f"| {field} |" in log
    data_rows = [
        line
        for line in log.splitlines()
        if line.startswith("| ") and not line.startswith("| case_id") and "---" not in line
    ]
    assert len(data_rows) == len(diffs)
    for line in data_rows:
        cols = [c.strip() for c in line.strip().strip("|").split("|")]
        assert cols[6]
        assert cols[7] == OWNER_RESOLVED_BY
        if cols[3] == P5_GATE_FIELD:
            assert cols[0] in HOL_MIN_IDS
            assert cols[6] == cols[4]
            assert "No;" in cols[4]
            assert cols[5] == "No"
        if cols[3] == "unacceptable_claims":
            assert cols[6] == OWNER_CLAIMS_RESOLUTION


def test_p5_gate_uses_disputed_fields_not_label_status() -> None:
    claims_only = {
        "hol-min-001": {
            "label_status": LABEL_DISPUTED,
            "disputed_fields": ["unacceptable_claims"],
        }
    }
    expected_open = {
        "hol-min-001": {
            "label_status": LABEL_PROVISIONAL,
            "disputed_fields": ["expected_action"],
        }
    }
    assert p5_gate_blocks(["unacceptable_claims"], LABEL_DISPUTED) is False
    assert p5_gate_blocks(["expected_action"], LABEL_PROVISIONAL) is True
    assert p5_expected_action_unresolved(claims_only) == []
    assert p5_expected_action_unresolved(expected_open) == ["hol-min-001"]
    draft = load_label_rows(GOLD_MERGED_DRAFT_JSONL)
    assert p5_expected_action_unresolved(draft) == []


def test_hol_min_no_prefix_matches_gold_label() -> None:
    from zhoda_core.benchmarks.judge import pick_matches_gold
    from zhoda_core.eval.gold import load_gold
    from zhoda_core.eval.grading import _map_to_label, closed_labels

    gold = load_gold(GOLD_JSONL)["hol-min-002"]
    labels = closed_labels(gold, ("No", "Yes"))
    assert gold.expected_action == "No; use a masked snapshot"
    assert _map_to_label(gold.expected_action, labels) == "No"
    assert pick_matches_gold("No", gold.expected_action, labels) is True
    assert pick_matches_gold("No", "No; use a masked snapshot") is True
    assert pick_matches_gold("Yes", gold.expected_action, labels) is False


def test_abstain_kappa_p5_gate_in_status() -> None:
    a_rows = load_label_rows(GOLD_JSONL)
    b_rows = load_label_rows(GOLD_ANNOTATOR_B_JSONL)
    ids = list(a_rows)
    policy_a = [str(a_rows[i]["abstain_policy"]) for i in ids]
    policy_b = [str(b_rows[i]["abstain_policy"]) for i in ids]
    kappa = cohen_kappa(policy_a, policy_b)
    status = STATUS_MD.read_text(encoding="utf-8")
    if kappa < KAPPA_P5_MIN:
        assert P5_NOT_READY in status
    else:
        assert P5_NOT_READY not in status


def test_dump_helpers_match_committed_artifacts(tmp_path: Path) -> None:
    merged = tmp_path / "gold-merged-draft.jsonl"
    log = tmp_path / "disagreement-log.md"
    dump_merged_draft(merged)
    dump_disagreement_log(log)
    assert merged.read_text(encoding="utf-8") == GOLD_MERGED_DRAFT_JSONL.read_text(encoding="utf-8")
    assert log.read_text(encoding="utf-8") == DISAGREEMENT_LOG.read_text(encoding="utf-8")
    committed = json.loads(FROZEN_MANIFEST.read_text(encoding="utf-8"))
    assert file_hash(GOLD_JSONL) == committed["gold_hash"]
