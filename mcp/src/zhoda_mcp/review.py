"""Проекция Verdict → read-only ReviewReport (zhoda.review.v1)."""

from __future__ import annotations

from typing import Any

from zhoda_core.models import AccountingStatus, ConsensusStrength, Verdict

REVIEW_SCHEMA = "zhoda.review.v1"
DEFAULT_REVIEW_QUESTION = (
    "Should this change be accepted as written, accepted with conditions, or rejected?"
)
# G не закрыт: eval holdout READY_FOR_APPROVAL без live. Default = product debate.
DEFAULT_PROTOCOL_POLICY = "debate"
ALLOWED_PROTOCOL_POLICIES = frozenset({"debate", "short_review", "vote", "red_team"})

REC_RECOMMENDED = "recommended"
REC_CONDITIONAL = "conditional"
REC_UNRESOLVED = "unresolved"
REC_INSUFFICIENT = "insufficient_evidence"
# «approved» запрещён — даже на trusted zhoda это предложение пользователю.

RETENTION = {
    "telemetry_documents": False,
    "transcript_local": True,
    "policy": (
        "User documents stay in the local transcripts_dir snapshot. "
        "This tool does not upload sources, create issues, or send messages."
    ),
}


def recommendation_status(verdict: Verdict) -> str:
    """Не complete → не recommended. Never 'approved'."""
    if verdict.insufficient_context:
        return REC_INSUFFICIENT
    if verdict.degraded or not verdict.completeness.trusted:
        return REC_UNRESOLVED
    if verdict.zhoda_reached and verdict.decision_origin == "council":
        if verdict.attributed_conditions:
            return REC_CONDITIONAL
        return REC_RECOMMENDED
    if verdict.decision_origin == "majority_at_cap":
        return REC_CONDITIONAL
    return REC_UNRESOLVED


def _faction_nodes(verdict: Verdict) -> list[dict[str, Any]]:
    tree = verdict.decision_tree or {}
    children = tree.get("children") if isinstance(tree, dict) else None
    if not isinstance(children, list):
        return []
    out: list[dict[str, Any]] = []
    for node in children:
        if not isinstance(node, dict) or node.get("kind") != "faction":
            continue
        raw_detail = node.get("detail")
        detail: dict[str, Any] = raw_detail if isinstance(raw_detail, dict) else {}
        out.append(
            {
                "name": node.get("label") or "",
                "members": list(detail.get("members") or []),
                "thesis": detail.get("thesis") or "",
                "synthetic": bool(detail.get("synthetic")),
                "claims": list(detail.get("claims") or []),
            }
        )
    return out


def _findings_from_claims(
    claims: list[dict[str, Any]],
    *,
    evidence: dict[str, Any] | None,
    adversarial: bool,
) -> list[dict[str, Any]]:
    src_id = ""
    src_hash = ""
    access = "unavailable"
    if evidence:
        src_id = str(evidence.get("source_id") or "")
        src_hash = str(evidence.get("content_hash") or "")
        access = str(evidence.get("access") or "unavailable")
    rows: list[dict[str, Any]] = []
    for claim in claims:
        label = str(claim.get("label") or "assumption")
        rows.append(
            {
                "claim": claim.get("claim") or "",
                "claim_id": claim.get("claim_id") or "",
                "source_id": src_id,
                "source_hash": src_hash,
                "access": access,
                "evidence_status": label,
                "adversarial": adversarial,
            }
        )
    return rows


def project_review(
    verdict: Verdict,
    *,
    source_manifest: list[dict[str, Any]] | None = None,
    protocol_policy: str = DEFAULT_PROTOCOL_POLICY,
) -> dict[str, Any]:
    """Структурированный review. Plan — proposal, не команда."""
    rec = recommendation_status(verdict)
    ev = verdict.evidence.model_dump() if verdict.evidence is not None else None
    factions = _faction_nodes(verdict)
    council = [f for f in factions if not f["synthetic"]]
    adversarial = [f for f in factions if f["synthetic"]]
    factual: list[dict[str, Any]] = []
    adversarial_findings: list[dict[str, Any]] = []
    for faction in council:
        factual.extend(_findings_from_claims(faction["claims"], evidence=ev, adversarial=False))
    for faction in adversarial:
        adversarial_findings.extend(
            _findings_from_claims(faction["claims"], evidence=ev, adversarial=True)
        )
    cost = verdict.cost
    usd_status = (
        cost.usd_status.value
        if isinstance(cost.usd_status, AccountingStatus)
        else str(cost.usd_status)
    )
    plan = None
    if verdict.plan_contract is not None:
        plan = {
            "kind": "proposal_not_an_execution_command",
            "executable": False,
            "contract": verdict.plan_contract.model_dump(),
        }
    return {
        "schema": REVIEW_SCHEMA,
        "status": "review",
        "recommendation_status": rec,
        "approved": False,
        "degraded": verdict.degraded,
        "incomplete": verdict.degraded or not verdict.completeness.trusted,
        "insufficient_evidence": rec == REC_INSUFFICIENT,
        "decision": verdict.decision,
        "zhoda_reached": verdict.zhoda_reached,
        "decision_origin": verdict.decision_origin,
        "consensus_strength": (
            verdict.consensus_strength.value
            if isinstance(verdict.consensus_strength, ConsensusStrength)
            else str(verdict.consensus_strength)
        ),
        "protocol": str(verdict.protocol),
        "protocol_policy": protocol_policy,
        "factual_findings": factual,
        "council_support": [
            {k: v for k, v in f.items() if k != "claims"} for f in council
        ],
        "adversarial_findings": adversarial_findings,
        "adversarial_factions": [
            {k: v for k, v in f.items() if k != "claims"} for f in adversarial
        ],
        "dissent": {
            "minority_report": verdict.minority_report,
            "dissent_map": [d.model_dump() for d in verdict.dissent_map],
        },
        "remaining_questions": list(verdict.value_map.open_ambiguities),
        "completeness": verdict.completeness.model_dump(),
        "cost": {
            **cost.model_dump(),
            "usd_status": usd_status,
        },
        "transcript_id": verdict.transcript_id,
        "run_id": verdict.run_id,
        "replay_limited": verdict.replay_limited,
        "evidence": ev,
        "source_manifest": list(source_manifest or []),
        "plan_proposal": plan,
        "retention": RETENTION,
        "product_gate": "OPEN",
        "g_protocol_usefulness": "unknown",
    }


def incomplete_payload(
    *,
    error: str,
    message: str,
    transcript_id: str = "",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema": REVIEW_SCHEMA,
        "status": "incomplete",
        "error": error,
        "message": message,
        "recommendation_status": REC_UNRESOLVED,
        "approved": False,
        "degraded": True,
        "incomplete": True,
        "transcript_id": transcript_id,
        "retention": RETENTION,
        "product_gate": "OPEN",
        "g_protocol_usefulness": "unknown",
    }
    if extra:
        payload.update(extra)
    return payload
