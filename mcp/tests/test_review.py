"""Read-only decision-review: bounds, projection, cancel, demo without keys."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from zhoda_core.engine import ZhodaEngine
from zhoda_core.models import (
    AccountingStatus,
    ConsensusStrength,
    CostReport,
    Protocol,
    RunCompleteness,
    Verdict,
)
from zhoda_core.providers.openrouter import (
    BudgetExceededError,
    OpenRouterProvider,
    QuotaExceededError,
)
from zhoda_core.reputation import ReputationStorage

from zhoda_mcp.demo_review import run_demo
from zhoda_mcp.readonly import FORBIDDEN_ADAPTERS, ReadOnlyViolation, invoke_adapter
from zhoda_mcp.render import render_review_md
from zhoda_mcp.review import project_review, recommendation_status
from zhoda_mcp.runtime import Runtime
from zhoda_mcp.server import mcp
from zhoda_mcp.sources import SourceError, assemble_context, resolve_allowed_file
from test_runtime import CFG, FakeEngine, _runtime, _verdict


def test_six_tools_include_review() -> None:
    names = set(mcp._tool_manager._tools)
    assert "zhoda_review" in names
    assert "zhoda_deliberate" in names
    assert len(names) == 6


def test_write_adapters_are_refused() -> None:
    for name in ("apply_migration", "execute_shell", "create_issue", "fetch_url"):
        with pytest.raises(ReadOnlyViolation):
            invoke_adapter(name, {})
    assert "auto_merge" in FORBIDDEN_ADAPTERS
    assert not hasattr(Runtime, "write_file")
    assert not hasattr(Runtime, "apply_migration")


def test_url_and_traversal_rejected(tmp_path: Path) -> None:
    root = tmp_path / "ok"
    root.mkdir()
    (root / "adr.md").write_text("hello", encoding="utf-8")
    with pytest.raises(SourceError) as url:
        resolve_allowed_file("https://example.com/adr.md", [str(root)])
    assert url.value.code == "url_fetch_forbidden"
    with pytest.raises(SourceError) as trav:
        resolve_allowed_file(str(root / ".." / "secret.md"), [str(root)])
    assert trav.value.code == "path_outside_allowed_roots"
    with pytest.raises(SourceError):
        assemble_context(source_paths=[str(root / "adr.md")], allowed_roots=[])
    ok, man = assemble_context(
        source_paths=[str(root / "adr.md")], allowed_roots=[str(root)]
    )
    assert "hello" in ok
    assert man[0]["origin"] == "file"


def test_symlink_escape_rejected(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("escaped", encoding="utf-8")
    link = allowed / "sneak.md"
    link.symlink_to(outside)
    with pytest.raises(SourceError) as exc:
        resolve_allowed_file(str(link), [str(allowed)])
    assert exc.value.code == "path_outside_allowed_roots"


def test_oversized_and_secrets() -> None:
    with pytest.raises(SourceError) as big:
        assemble_context(source_text="x" * 30_000)
    assert big.value.code == "oversized_source"
    text, man = assemble_context(source_text="token sk-abcdefghijk and password=hunter2")
    assert "sk-abcdefghijk" not in text
    assert "[redacted]" in text
    assert man[0]["redacted"] is True


def _complete(**updates: Any) -> Verdict:
    data = _verdict().model_dump()
    data.update(updates)
    return Verdict.model_validate(data)


def test_projection_never_says_approved() -> None:
    v = _complete(
        degraded=True,
        zhoda_reached=False,
        decision_origin="degraded",
        completeness=RunCompleteness(policy="t").model_dump(),
        cost=CostReport(usd_status=AccountingStatus.UNKNOWN).model_dump(),
        insufficient_context=True,
    )
    report = project_review(v)
    assert report["approved"] is False
    assert report["recommendation_status"] == "insufficient_evidence"
    assert report["cost"]["usd_status"] == "unknown"
    md = render_review_md(report)
    assert "degraded" in md
    assert "unknown" in md
    assert "always false" in md


def test_incomplete_run_is_not_recommended() -> None:
    comp = RunCompleteness(policy="t")
    comp.register("council", "m1")
    comp.fail("council", "m1", "missing")
    v = _complete(
        zhoda_reached=False,
        degraded=True,
        decision_origin="degraded",
        completeness=comp.model_dump(),
        insufficient_context=False,
    )
    assert recommendation_status(v) == "unresolved"
    report = project_review(v)
    assert report["incomplete"] is True
    assert report["recommendation_status"] != "recommended"


def test_adversarial_split_from_council() -> None:
    tree = {
        "kind": "verdict",
        "label": "x",
        "children": [
            {
                "kind": "faction",
                "label": "Real",
                "detail": {
                    "members": ["m1"],
                    "synthetic": False,
                    "thesis": "keep uniqueness",
                    "claims": [
                        {
                            "claim": "keep invoices_pkey",
                            "label": "sourced",
                            "claim_id": "1",
                        }
                    ],
                },
            },
            {
                "kind": "faction",
                "label": "DA",
                "detail": {
                    "members": ["advocate"],
                    "synthetic": True,
                    "thesis": "drop it",
                    "claims": [
                        {
                            "claim": "drop for speed",
                            "label": "assumption",
                            "claim_id": "2",
                        }
                    ],
                },
            },
        ],
    }
    v = _complete(decision_tree=tree, consensus_strength=ConsensusStrength.SPLIT)
    report = project_review(v)
    assert report["council_support"][0]["name"] == "Real"
    assert report["adversarial_factions"][0]["synthetic"] is True
    assert report["factual_findings"][0]["claim"] == "keep invoices_pkey"
    assert report["adversarial_findings"][0]["adversarial"] is True


@pytest.mark.asyncio
async def test_review_confirm_false_never_runs(tmp_path: Path) -> None:
    rt, engine = _runtime(tmp_path)
    out = await rt.review("review this", confirm=False, protocol_policy="debate")
    assert out["status"] == "estimate"
    assert out["approved"] is False
    assert out["estimate"]["read_only"] is True
    assert out["estimate"]["executable"] is False
    assert engine.calls == []


@pytest.mark.asyncio
async def test_review_maps_verdict(tmp_path: Path) -> None:
    rt, engine = _runtime(tmp_path)
    out = await rt.review(
        "review this",
        confirm=True,
        source_text="Keep the unique constraint on invoices.id.",
        protocol_policy="debate",
        format="md",
    )
    assert out["status"] == "review"
    assert out["approved"] is False
    assert out["protocol_policy"] == "debate"
    assert "markdown" in out
    assert engine.calls[0][1]["force_protocol"] == Protocol.DEBATE
    assert engine.calls[0][1]["clarify_mode"] == "no-clarify"
    assert "unique constraint" in engine.calls[0][1]["context"]


@pytest.mark.asyncio
async def test_review_allowed_file(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    root.mkdir()
    path = root / "adr.md"
    path.write_text("Keep the unique constraint on invoices.id.", encoding="utf-8")
    rt, engine = _runtime(tmp_path)
    out = await rt.review(
        "review",
        confirm=True,
        source_paths=[str(path)],
        allowed_roots=[str(root)],
    )
    assert out["status"] == "review"
    assert "unique constraint" in engine.calls[0][1]["context"]


@pytest.mark.asyncio
async def test_review_cancel_is_incomplete(tmp_path: Path) -> None:
    rt, _ = _runtime(tmp_path, FakeEngine(error=asyncio.CancelledError()))
    out = await rt.review("q", confirm=True, source_text="body")
    assert out["status"] == "incomplete"
    assert out["error"] == "cancelled"
    assert out["approved"] is False
    assert out["recommendation_status"] == "unresolved"


@pytest.mark.asyncio
async def test_review_timeout_is_incomplete(tmp_path: Path) -> None:
    class Slow(FakeEngine):
        async def deliberate(self, question: str, **kwargs: Any) -> Verdict:
            del question, kwargs
            await asyncio.sleep(2)
            return _verdict()

    rt, _ = _runtime(tmp_path, Slow())
    out = await rt.review("q", confirm=True, source_text="body", timeout_s=0.05)
    assert out["error"] == "timeout"
    assert out["status"] == "incomplete"


@pytest.mark.asyncio
async def test_review_invalid_policy(tmp_path: Path) -> None:
    rt, engine = _runtime(tmp_path)
    out = await rt.review("q", confirm=True, protocol_policy="council")
    assert out["error"] == "invalid_protocol_policy"
    assert engine.calls == []


def test_offline_demo_without_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    report = run_demo()
    assert report["approved"] is False
    assert report["cost"]["usd_status"] == "unknown"
    blob = json.dumps(report)
    assert "unique constraint" in blob.casefold()
    assert "customer emails" in blob.casefold()
    assert report["demo"]["live"] is False
    assert report["demo"]["product_gate"] == "OPEN"


class _SlowProvider(OpenRouterProvider):
    def __init__(self) -> None:
        super().__init__(api_key="test")

    async def complete(self, model: str, prompt: str, **kwargs: object) -> str:
        del model, prompt, kwargs
        await asyncio.sleep(30)
        return "{}"


def _engine_runtime(tmp_path: Path, provider: OpenRouterProvider) -> tuple[Runtime, ZhodaEngine]:
    engine = ZhodaEngine(
        provider,
        list(CFG["council"]),
        chairman=str(CFG["chairman"]),
        judges=tuple(CFG["judges"]),
        router_classifiers=tuple(CFG["router_classifiers"]),
        transcripts_dir=str(tmp_path / "tr"),
        alias_seed=42,
    )

    def factory(_rounds: int | None) -> tuple[ZhodaEngine, OpenRouterProvider]:
        return engine, provider

    rt = Runtime(
        dict(CFG),
        transcripts=engine.transcripts,
        reputation=ReputationStorage(tmp_path / "rep.json"),
        session_factory=factory,
    )
    return rt, engine


@pytest.mark.asyncio
async def test_yaml_zero_budget_usd_cannot_raise_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """yaml 0 = :free only. budget_usd не поднимает кап. Проверка через make_provider."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    from zhoda_core.config import make_provider
    from zhoda_core.transcripts import TranscriptStore

    cfg = {
        **CFG,
        "budget_per_question_usd": 0.0,
        "prices": {"paid/model": 0.01},
    }
    rt = Runtime(
        cfg,
        transcripts=TranscriptStore(str(tmp_path / "tr")),
        reputation=ReputationStorage(tmp_path / "rep.json"),
    )
    assert rt.session_factory is None
    clamped = rt._budget_cfg(10.0)
    assert clamped is not None
    assert clamped.get("error") is None
    assert float(clamped["budget_per_question_usd"]) == 0.0
    provider = make_provider(clamped)
    assert provider.budget_usd == 0.0
    provider.begin_question()
    with pytest.raises(BudgetExceededError, match=":free"):
        await provider.complete("paid/model", "hi")
    await provider.close()

    est = await rt.review("q", confirm=False, source_text="body", budget_usd=10.0)
    assert est["status"] == "estimate"
    assert est["estimate"]["budget_usd"] == 0.0
    assert ":free" in est["estimate"]["note"]

    rt.cfg = {**CFG, "budget_per_question_usd": 10.0}
    lowered = rt._budget_cfg(4.0)
    assert lowered is not None
    assert float(lowered["budget_per_question_usd"]) == 4.0
    raised = rt._budget_cfg(50.0)
    assert raised is not None
    assert float(raised["budget_per_question_usd"]) == 10.0

    bad = await rt.review("q", confirm=False, source_text="body", budget_usd=-1)
    assert bad["status"] == "incomplete"
    assert bad["approved"] is False
    assert bad["error"] == "invalid_budget"


@pytest.mark.asyncio
async def test_review_quota_is_incomplete(tmp_path: Path) -> None:
    rt, _ = _runtime(tmp_path, FakeEngine(error=QuotaExceededError("daily cap")))
    out = await rt.review("q", confirm=True, source_text="body")
    assert out["status"] == "incomplete"
    assert out["error"] == "quota_exceeded"
    assert out["approved"] is False
    assert out["incomplete"] is True
    assert "silently" in out["hint"]


@pytest.mark.asyncio
async def test_review_cancel_engine_cleans_accounting(tmp_path: Path) -> None:
    """Host cancel: MCP ловит CancelledError, engine end_question, не verdict."""
    provider = _SlowProvider()
    rt, engine = _engine_runtime(tmp_path, provider)
    task = asyncio.create_task(
        rt.review("q", confirm=True, source_text="body"),
    )
    for _ in range(100):
        await asyncio.sleep(0.01)
        if engine.last_transcript_id:
            break
    assert engine.last_transcript_id
    task.cancel()
    out = await task
    assert out["status"] == "incomplete"
    assert out["error"] == "cancelled"
    assert out["approved"] is False
    assert provider._active_run is None
    tid = engine.last_transcript_id
    events = engine.transcripts.read(tid)
    stages = [str(event.get("stage")) for event in events]
    assert stages[0] == "start"
    assert stages[-1] == "error"
    assert "verdict" not in stages
    assert events[-1].get("terminal") is True


@pytest.mark.asyncio
async def test_review_timeout_engine_cleans_accounting(tmp_path: Path) -> None:
    provider = _SlowProvider()
    rt, engine = _engine_runtime(tmp_path, provider)
    out = await rt.review(
        "q", confirm=True, source_text="body", timeout_s=0.05,
    )
    assert out["status"] == "incomplete"
    assert out["error"] == "timeout"
    assert out["approved"] is False
    assert provider._active_run is None
    tid = engine.last_transcript_id
    assert tid
    events = engine.transcripts.read(tid)
    stages = [str(event.get("stage")) for event in events]
    assert stages[-1] == "error"
    assert "verdict" not in stages
