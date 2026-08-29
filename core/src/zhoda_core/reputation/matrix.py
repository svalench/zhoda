"""Per-domain ELO matrix with softmax vote weighting.

Effective rating of a model for a question is the dot product of the
question's domain vector and the model's per-domain ELO row:

    R_eff(m) = sum_k p_k * ELO[m, k]

Vote weight in consensus (Stage 4) is a temperature-scaled softmax over
effective ratings with a floor, so no faction is fully silenced:

    w_m = softmax(R_eff(m) / tau)

Updates after a debate scale K by the domain vector, so a security debate
moves mostly the security coordinate of each participant's rating.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Dict, Iterable, List, Mapping, Optional

from .domains import Domain, DomainVector

DEFAULT_RATING = 1000.0
DEFAULT_K = 32.0
DEFAULT_TAU = 400.0
MIN_WEIGHT = 0.05


class ReputationEventType(str, Enum):
    CRITIQUE_ACCEPTED = "critique_accepted"    # model's critique was accepted (+)
    FLAW_CONFIRMED = "flaw_confirmed"          # model's claim proven flawed (-)
    BENEFICIAL_SWITCH = "beneficial_switch"    # model switched toward the winning side (+)


_EVENT_DELTAS: Mapping[ReputationEventType, float] = {
    ReputationEventType.CRITIQUE_ACCEPTED: 16.0,
    ReputationEventType.FLAW_CONFIRMED: -16.0,
    ReputationEventType.BENEFICIAL_SWITCH: 8.0,
}


@dataclass(frozen=True)
class ReputationEvent:
    model: str
    type: ReputationEventType
    domain_vector: DomainVector
    magnitude: float = 1.0


class DomainEloMatrix:
    """Sparse model x domain ELO matrix."""

    def __init__(
        self,
        ratings: Optional[Mapping[str, Mapping[str, float]]] = None,
        k: float = DEFAULT_K,
        tau: float = DEFAULT_TAU,
    ) -> None:
        self.k = float(k)
        self.tau = float(tau)
        self._ratings: Dict[str, Dict[Domain, float]] = {}
        for model, row in (ratings or {}).items():
            self._ratings[model] = {Domain(d): float(v) for d, v in row.items()}

    # ------------------------------------------------------------------ read
    def get(self, model: str, domain: Domain) -> float:
        return self._ratings.get(model, {}).get(domain, DEFAULT_RATING)

    def models(self) -> List[str]:
        return sorted(self._ratings)

    def effective_rating(self, model: str, domain_vector: DomainVector) -> float:
        row = self._ratings.get(model, {})
        return sum(p * row.get(d, DEFAULT_RATING) for d, p in domain_vector.items())

    def vote_weights(
        self, models: Iterable[str], domain_vector: DomainVector
    ) -> Dict[str, float]:
        """Softmax over effective ratings with a floor, renormalized."""
        models = list(models)
        if not models:
            return {}
        eff = {m: self.effective_rating(m, domain_vector) for m in models}
        peak = max(eff.values())
        exps = {m: math.exp((r - peak) / self.tau) for m, r in eff.items()}
        total = sum(exps.values())
        weights = {m: max(v / total, MIN_WEIGHT) for m, v in exps.items()}
        norm = sum(weights.values())
        return {m: w / norm for m, w in weights.items()}

    # ----------------------------------------------------------------- write
    @staticmethod
    def expected_score(ra: float, rb: float) -> float:
        return 1.0 / (1.0 + math.pow(10.0, (rb - ra) / 400.0))

    def _row(self, model: str) -> Dict[Domain, float]:
        return self._ratings.setdefault(model, {})

    def record_match(
        self,
        winner: str,
        loser: str,
        domain_vector: DomainVector,
        k: Optional[float] = None,
        draw: bool = False,
    ) -> None:
        """Pairwise ELO update, distributed across domains by the vector."""
        k = self.k if k is None else float(k)
        ra = self.effective_rating(winner, domain_vector)
        rb = self.effective_rating(loser, domain_vector)
        ea = self.expected_score(ra, rb)
        sa = 0.5 if draw else 1.0
        delta = k * (sa - ea)
        for domain, weight in domain_vector.items():
            row_a = self._row(winner)
            row_b = self._row(loser)
            row_a[domain] = row_a.get(domain, DEFAULT_RATING) + delta * weight
            row_b[domain] = row_b.get(domain, DEFAULT_RATING) - delta * weight

    def record_event(self, event: ReputationEvent) -> None:
        """Atomic outcome event (accepted critique, confirmed flaw, switch)."""
        base = _EVENT_DELTAS[event.type] * event.magnitude
        row = self._row(event.model)
        for domain, weight in event.domain_vector.items():
            row[domain] = row.get(domain, DEFAULT_RATING) + base * weight

    # ---------------------------------------------------------- serialization
    def to_dict(self) -> dict:
        return {
            "k": self.k,
            "tau": self.tau,
            "ratings": {
                model: {d.value: v for d, v in row.items()}
                for model, row in self._ratings.items()
            },
        }

    @classmethod
    def from_dict(cls, data: Mapping) -> "DomainEloMatrix":
        return cls(
            ratings=data.get("ratings", {}),
            k=data.get("k", DEFAULT_K),
            tau=data.get("tau", DEFAULT_TAU),
        )
