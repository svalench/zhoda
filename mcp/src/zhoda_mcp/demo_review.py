"""Offline decision-review demo: scripted engine, no API keys."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import cast

from zhoda_core.evidence import bundle_from_context
from zhoda_core.models import (
    AccountingStatus,
    ConsensusStrength,
    CostReport,
    Protocol,
    RunCompleteness,
    ValueMap,
    Verdict,
)
from zhoda_core.reputation import ReputationStorage
from zhoda_core.transcripts import TranscriptStore

from zhoda_mcp.runtime import Runtime
from zhoda_mcp.sources import assemble_context

EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "review"


def _scripted_verdict(source: str) -> Verdict:
    """Демонстрационный вердикт: цитаты из sample ADR, не live LLM."""
    tree = {
        "kind": "verdict",
        "label": "Reject as written",
        "children": [
            {
                "kind": "faction",
                "label": "Keep integrity",
                "detail": {
                    "members": ["m1", "m2"],
                    "synthetic": False,
                    "thesis": "Keep the unique constraint; do not log emails.",
                    "claims": [
                        {
                            "claim": "Do not drop the unique constraint on invoices.id",
                            "claim_id": "c1",
                            "label": "sourced",
                            "state": "active",
                        },
                        {
                            "claim": "Do not log raw customer emails on every HTTP 500",
                            "claim_id": "c2",
                            "label": "sourced",
                            "state": "active",
                        },
                    ],
                },
            },
            {
                "kind": "faction",
                "label": "Devil",
                "detail": {
                    "members": ["advocate"],
                    "synthetic": True,
                    "thesis": "Ship Friday without a flag.",
                    "claims": [
                        {
                            "claim": "Adversarial: Friday 22:00 DDL with no feature flag",
                            "claim_id": "a1",
                            "label": "assumption",
                            "state": "active",
                        }
                    ],
                },
            },
        ],
    }
    completeness = RunCompleteness(policy="demo")
    completeness.register("council", "m1")
    completeness.succeed("council", "m1")
    evidence = bundle_from_context(source)
    return Verdict(
        decision="Reject as written: keep uniqueness, do not log emails, flag schema changes.",
        zhoda_reached=True,
        consensus_strength=ConsensusStrength.MAJORITY,
        protocol=Protocol.DEBATE,
        decision_origin="council",
        transcript_id="demo-offline",
        run_id="demo",
        completeness=completeness,
        cost=CostReport(usd_status=AccountingStatus.UNKNOWN, usd=0.0),
        decision_tree=cast(dict[str, object], tree),
        value_map=ValueMap(goal="review the attached change"),
        minority_report="Launch-week speed faction still wants the drop.",
        evidence=evidence,
        replay_limited=bool(evidence is not None and evidence.replay_limited),
    )


class _ScriptedEngine:
    last_transcript_id = "demo-offline"

    def __init__(self, source: str) -> None:
        self.source = source
        self.calls: list[str] = []

    async def deliberate(self, question: str, **kwargs: object) -> Verdict:
        self.calls.append(question)
        ctx = str(kwargs.get("context") or "")
        assert "unique constraint" in ctx or "unique constraint" in self.source
        return _scripted_verdict(self.source)


class _Closed:
    async def close(self) -> None:
        return None


def examples_dir() -> Path:
    if EXAMPLES.is_dir():
        return EXAMPLES
    raise FileNotFoundError(f"missing offline samples at {EXAMPLES}")


def run_demo() -> dict[str, object]:
    os.environ.pop("OPENROUTER_API_KEY", None)
    root = examples_dir()
    adr = (root / "adr-logging-pii.md").read_text(encoding="utf-8")
    plan = (root / "plan-friday-unflagged.md").read_text(encoding="utf-8")
    expected = json.loads((root / "expected.json").read_text(encoding="utf-8"))
    context, manifest = assemble_context(
        source_text=adr + "\n\n" + plan,
        source_paths=[],
        allowed_roots=[],
    )
    engine = _ScriptedEngine(context)
    tmp = Path(tempfile.mkdtemp(prefix="zhoda-review-demo-"))

    def factory(_rounds: int | None) -> tuple[_ScriptedEngine, _Closed]:
        return engine, _Closed()

    rt = Runtime(
        {
            "council": ["m1", "m2", "m3"],
            "judges": ["j1", "j2"],
            "router_classifiers": ["j1", "j2"],
            "chairman": "m1",
            "rounds_cap": 4,
            "budget_per_question_usd": 1.0,
        },
        transcripts=TranscriptStore(str(tmp / "tr")),
        reputation=ReputationStorage(tmp / "rep.json"),
        session_factory=factory,
    )
    import asyncio

    report = asyncio.run(
        rt.review(
            "Should this ADR and plan be accepted as written?",
            confirm=True,
            source_text=context,
            protocol_policy="debate",
        )
    )
    blob = json.dumps(report, ensure_ascii=False)
    for needle in expected["expected_finding_substrings"]:
        if needle.casefold() not in blob.casefold():
            raise SystemExit(f"demo missing expected finding {needle!r}")
    if report.get("approved") is True:
        raise SystemExit("demo must not set approved=true")
    if "approved" in str(report.get("recommendation_status")):
        raise SystemExit("recommendation_status must not be approved")
    report["demo"] = {
        "live": False,
        "independent_validation": False,
        "product_gate": "OPEN",
        "source_manifest": manifest,
        "note": expected["note"],
    }
    return report


def main() -> None:
    report = run_demo()
    json.dump(report, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
