"""Explainability view (values №1): the decision as a TREE — argument ->
what closed it -> who switched. Evidence labels are THREE states
(round-10 §1): sourced / unverified_claim / assumption."""

from .factions import Faction
from .models import Critique, DecisionNode, FactionSwitch, ObjectionStatus
from .verdict import SYNTHETIC_LABEL

_RESOLUTION = {
    ObjectionStatus.CLOSED: "closed by rebuttal",
    ObjectionStatus.SUPERSEDED: "addressed by platform revision",
    ObjectionStatus.OPEN: "UNRESOLVED — carried into plan constraints",
}


def _evidence_label(url: str | None, verified: bool) -> str:
    if url is None:
        return "assumption"
    return "sourced" if verified else "unverified_claim"


def build_decision_tree(
    factions: list[Faction],
    objections: list[Critique],
    switches: list[FactionSwitch],
    decision: str,
) -> DecisionNode:
    root = DecisionNode(kind="verdict", label=decision[:120])
    for faction in factions:
        fnode = DecisionNode(
            kind="faction",
            label=faction.name,
            detail={
                "members": faction.members,
                "synthetic": faction.synthetic,
                **({"note": SYNTHETIC_LABEL} if faction.synthetic else {}),
                "thesis": faction.platform.thesis if faction.platform else "",
                "claims": [
                    {
                        "claim": c.claim,
                        "evidence_url": c.evidence_url,
                        "label": c.label,  # sourced | unverified_claim | assumption
                    }
                    for c in (faction.platform.claims if faction.platform else [])
                ],
            },
        )
        for critique in objections:
            if critique.target_faction != faction.name:
                continue
            fnode.children.append(DecisionNode(
                kind="objection",
                label=critique.claim[:120],
                detail={
                    "flaw_type": str(critique.flaw_type),
                    "by": critique.author_faction,
                    "evidence": _evidence_label(
                        critique.evidence_url, critique.evidence_verified,
                    ),
                    "resolution": _RESOLUTION[critique.status],
                },
            ))
        root.children.append(fnode)
    for switch in switches:
        root.children.append(DecisionNode(
            kind="switch",
            label=f"{switch.model}: {switch.from_faction} → {switch.to_faction}",
            detail={"convinced_by": switch.convinced_by, "objection_id": switch.objection_id},
        ))
    return root
