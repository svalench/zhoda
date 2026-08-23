"""Explainability view (values №1): the decision as a TREE, not a linear
chronicle — argument -> what closed it -> who switched. This is what
physically differentiates Zhoda from council-of-the-week tools."""

from .factions import Faction
from .models import Critique, FactionSwitch, ObjectionStatus
from .models import DecisionNode

_RESOLUTION = {
    ObjectionStatus.CLOSED: "closed by rebuttal",
    ObjectionStatus.SUPERSEDED: "addressed by platform revision",
    ObjectionStatus.OPEN: "UNRESOLVED — carried into plan constraints",
}


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
                "thesis": faction.platform.thesis if faction.platform else "",
                "claims": [
                    {
                        "claim": c.claim,
                        "evidence_url": c.evidence_url,
                        "label": "sourced" if c.is_sourced else "assumption",
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
                    "evidence_url": critique.evidence_url,
                    "resolution": _RESOLUTION[critique.status],
                    "rebuttal_evidence_url": critique.rebuttal_evidence_url,
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
