"""Domain-weighted reputation for Zhoda deliberation outcomes.

Implements the per-domain ELO model from the whitepaper (section 5):
accepted critiques (+), confirmed flaws (-) and beneficial switches (+)
update a model's rating only in the domains of the debated question.
Vote weights in consensus are derived from a softmax over effective
(domain-projected) ratings, so a model's voice is amplified only where
its arguments have historically been validated.
"""

from .domains import Domain, DomainVector, classify_domains
from .matrix import (
    DEFAULT_K,
    DEFAULT_RATING,
    DEFAULT_TAU,
    DomainEloMatrix,
    ReputationEvent,
    ReputationEventType,
)
from .storage import ReputationStorage

__all__ = [
    "Domain",
    "DomainVector",
    "classify_domains",
    "DomainEloMatrix",
    "ReputationEvent",
    "ReputationEventType",
    "ReputationStorage",
    "DEFAULT_K",
    "DEFAULT_RATING",
    "DEFAULT_TAU",
]
