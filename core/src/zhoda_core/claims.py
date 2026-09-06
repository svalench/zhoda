"""Claim IDs, revision delta, supporting vs history.

Refuted/superseded не копируются в supporting findings автоматически.
"""

from __future__ import annotations

import hashlib

from .evidence import normalize_quote
from .models import Claim, ClaimState, Position


def supporting_claims(claims: list[Claim]) -> list[Claim]:
    """Только active — в findings и synthesis. History остаётся в ledger."""
    return [c for c in claims if c.state is ClaimState.ACTIVE]


def stamp_claim(
    claim: Claim,
    *,
    owner: str,
    evidence_id: str | None = None,
    provenance: str = "",
) -> Claim:
    """Стабильный id от owner+нормализованного текста. Model JSON не задаёт id."""
    if claim.claim_id:
        updates: dict[str, object] = {}
        if not claim.owner:
            updates["owner"] = owner
        if evidence_id and not claim.evidence_id:
            updates["evidence_id"] = evidence_id
        return claim.model_copy(update=updates) if updates else claim
    digest = hashlib.sha256(
        f"{owner}\0{normalize_quote(claim.claim)}".encode()
    ).hexdigest()[:12]
    verified = claim.verified
    # Incomplete/absent evidence never becomes sourced trust here.
    if evidence_id is None and not claim.evidence_url:
        verified = False
    return claim.model_copy(
        update={
            "claim_id": f"clm_{digest}",
            "owner": owner,
            "version": claim.version or 1,
            "state": claim.state or ClaimState.ACTIVE,
            "evidence_id": evidence_id,
            "provenance": provenance or claim.provenance,
            "verified": verified,
        }
    )


def stamp_position(
    position: Position,
    *,
    evidence_id: str | None = None,
    provenance: str = "position",
) -> Position:
    stamped = [
        stamp_claim(
            c, owner=position.model, evidence_id=evidence_id, provenance=provenance,
        )
        for c in position.claims
    ]
    return position.model_copy(update={"claims": stamped})


def merge_revision_claims(
    prior: list[Claim],
    incoming: list[Claim],
    *,
    owner: str,
    evidence_id: str | None = None,
) -> list[Claim]:
    """Новая platform: retain по нормализованному тексту, остальное superseded.

    incoming — формулировки модели (без id). Refuted никогда не становится
    supporting. Не перечисленные active уходят в history (superseded).
    """
    incoming_by_norm: dict[str, Claim] = {}
    for item in incoming:
        key = normalize_quote(item.claim)
        if key:
            incoming_by_norm[key] = item
    result: list[Claim] = []
    retained_norms: set[str] = set()
    for old in prior:
        key = normalize_quote(old.claim)
        if old.state is ClaimState.REFUTED:
            result.append(old)
            continue
        if old.state is ClaimState.SUPERSEDED:
            result.append(old)
            continue
        if key in incoming_by_norm:
            retained_norms.add(key)
            result.append(
                old.model_copy(
                    update={
                        "version": old.version + 1,
                        "state": ClaimState.ACTIVE,
                        "claim": incoming_by_norm[key].claim or old.claim,
                    }
                )
            )
        else:
            result.append(
                old.model_copy(
                    update={
                        "version": old.version + 1,
                        "state": ClaimState.SUPERSEDED,
                    }
                )
            )
    for key, new in incoming_by_norm.items():
        if key in retained_norms:
            continue
        result.append(
            stamp_claim(new, owner=owner, evidence_id=evidence_id, provenance="revision")
        )
    return result


def collect_ledger(positions: list[Position]) -> list[Claim]:
    """Все claims всех платформ — current+history. Minority не становится majority."""
    ledger: list[Claim] = []
    seen: set[str] = set()
    for pos in positions:
        for claim in pos.claims:
            cid = claim.claim_id or f"anon:{normalize_quote(claim.claim)}"
            key = f"{cid}:{claim.version}:{claim.state}"
            if key in seen:
                continue
            seen.add(key)
            ledger.append(claim)
    return ledger
