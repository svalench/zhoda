"""Рендер хронікі в JSON/markdown. Вердикт — последний event stage=verdict."""

from __future__ import annotations

import json
from typing import Any


def last_verdict(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    for event in reversed(events):
        if event.get("stage") != "verdict":
            continue
        inner = event.get("verdict")
        if isinstance(inner, dict):
            return inner
    return None


def render_transcript_md(transcript_id: str, events: list[dict[str, Any]]) -> str:
    lines = [f"# хроніка `{transcript_id}`", ""]
    for event in events:
        stage = str(event.get("stage", "event"))
        lines.append(f"## {stage}")
        payload = {k: v for k, v in event.items() if k not in {"stage", "ts"}}
        lines.append("```json")
        lines.append(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        lines.append("```")
        lines.append("")
    return "\n".join(lines)


def render_review_md(report: dict[str, Any]) -> str:
    """Честный markdown: degraded/unknown cost видны, approved не используется."""
    rec = str(report.get("recommendation_status") or "unresolved")
    lines = [
        "# Decision review (read-only)",
        "",
        f"- schema: `{report.get('schema')}`",
        f"- recommendation_status: **{rec}**",
        f"- approved: `{report.get('approved', False)}` (always false — proposal only)",
        f"- degraded: `{report.get('degraded')}`",
        f"- incomplete: `{report.get('incomplete')}`",
        f"- decision_origin: `{report.get('decision_origin')}`",
        f"- protocol_policy: `{report.get('protocol_policy')}` "
        f"(G usefulness: {report.get('g_protocol_usefulness', 'unknown')})",
    ]
    cost = report.get("cost") if isinstance(report.get("cost"), dict) else {}
    usd_status = cost.get("usd_status", "unknown") if cost else "unknown"
    usd = cost.get("usd") if cost else None
    lines.append(f"- usd: `{usd}` usd_status: `{usd_status}`")
    if report.get("status") == "incomplete":
        lines.append(f"- error: `{report.get('error')}` {report.get('message')}")
        lines.append("")
        return "\n".join(lines)
    lines.append(f"- transcript_id: `{report.get('transcript_id')}`")
    lines.extend(["", "## Decision", "", str(report.get("decision") or ""), ""])
    findings = report.get("factual_findings") or []
    lines.extend(["## Factual findings", ""])
    if not findings:
        lines.append("(none — missing evidence stays missing)")
    for row in findings:
        if isinstance(row, dict):
            lines.append(
                f"- {row.get('claim')} "
                f"[evidence={row.get('evidence_status')} access={row.get('access')}]"
            )
    adv = report.get("adversarial_findings") or []
    lines.extend(["", "## Adversarial findings (not council votes)", ""])
    if not adv:
        lines.append("(none)")
    for row in adv:
        if isinstance(row, dict):
            lines.append(f"- {row.get('claim')}")
    lines.extend(["", "## Remaining questions", ""])
    remain = report.get("remaining_questions") or []
    if not remain:
        lines.append("(none)")
    for item in remain:
        lines.append(f"- {item}")
    plan = report.get("plan_proposal")
    lines.extend(["", "## Plan proposal", ""])
    if not plan:
        lines.append("No plan contract (not a trusted zhoda). Not an execution command.")
    else:
        lines.append("Proposal only — not a command to execute, merge, or migrate.")
    lines.append("")
    return "\n".join(lines)
