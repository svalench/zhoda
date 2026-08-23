"""Orchestration state machine: router -> elicitor -> positions -> factions
-> debate rounds -> consensus -> verdict. Every deliberation is persisted as
a transcript (хроніка) BEFORE the verdict is returned (protocol invariant).
"""

from .consensus import ConsensusChecker
from .debate import DebateEngine
from .elicitor import Elicitor
from .factions import FactionClusterer
from .models import Protocol, Verdict
from .providers.openrouter import OpenRouterProvider
from .router import ProtocolRouter
from .verdict import VerdictBuilder


class ZhodaEngine:
    def __init__(self, provider: OpenRouterProvider, council: list[str], rounds_cap: int = 4) -> None:
        self.provider = provider
        self.council = council
        self.rounds_cap = rounds_cap
        self.router = ProtocolRouter(provider)
        self.elicitor = Elicitor(provider)
        self.clusterer = FactionClusterer(provider)
        self.debate = DebateEngine(provider)
        self.consensus = ConsensusChecker()
        self.verdicts = VerdictBuilder()

    async def deliberate(
        self,
        question: str,
        *,
        force_protocol: Protocol | None = None,
        clarify_mode: str = "smart",
    ) -> Verdict:
        """Full cycle. Protocol selection is transparent (router_confidence
        lands in the verdict). Vote protocol = cheap path, no debate rounds."""
        raise NotImplementedError  # TODO(mvp): wire stages per docs/01-core.md
