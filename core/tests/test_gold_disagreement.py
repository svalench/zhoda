"""A vs B: точное сравнение полей, gold.jsonl не переписываем."""

from __future__ import annotations

import json
from pathlib import Path

from zhoda_core.eval.gold import (
    DISAGREEMENT_LOG,
    GOLD_ANNOTATOR_B_JSONL,
    GOLD_JSONL,
    GOLD_MERGED_DRAFT_JSONL,
    KAPPA_P5_MIN,
    LABEL_DISPUTED,
    LABEL_PROVISIONAL,
    cohen_kappa,
    dump_disagreement_log,
    dump_merged_draft,
    field_disagreements,
    load_label_rows,
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
        assert row["expected_action"] == a_rows[case_id]["expected_action"]
        assert row["abstain_policy"] == a_rows[case_id]["abstain_policy"]
        assert row["unacceptable_claims"] == a_rows[case_id]["unacceptable_claims"]
        assert row["annotator"] == a_rows[case_id]["annotator"]


def test_disagreement_log_has_empty_resolution() -> None:
    a_rows = load_label_rows(GOLD_JSONL)
    b_rows = load_label_rows(GOLD_ANNOTATOR_B_JSONL)
    diffs = field_disagreements(a_rows, b_rows)
    log = DISAGREEMENT_LOG.read_text(encoding="utf-8")
    assert "NOT STARTED" not in log
    assert "awaiting owner resolution" in log
    for case_id, field, av, bv in diffs:
        assert f"| {case_id} |" in log
        assert f"| {field} |" in log
    data_rows = [
        line
        for line in log.splitlines()
        if line.startswith("| ") and not line.startswith("| case_id")
    ]
    assert len(data_rows) == len(diffs)
    for line in data_rows:
        assert "|  |  |" in line


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
