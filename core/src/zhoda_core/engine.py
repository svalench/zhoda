"""Orchestration state machine: router -> elicitor -> positions -> factions
-> debate rounds -> consensus -> verdict.

Trust is not eliminated, it is CONCENTRATED in three auditable points
(round-3 §6): router classification, objection closure, position comparison.
Each is a separate logged call in the transcript (хроніка), which is
persisted BEFORE the verdict is returned (protocol invariant).
"""

from .anonymize import make_aliases
from .consensus import ConsensusChecker
from .debate import DebateEngine
from .elicitor import Elicitor
from .factions import FactionClusterer
from .models import Protocol, Verdict
from .providers.openrouter import OpenRouterProvider
from .router import ProtocolRouter
from .transcripts import TranscriptStore
from .verdict import VerdictBuilder


class ZhodaEngine:
    def __init__(
        self,
        provider: OpenRouterProvider,
        council: list[str],
        rounds_cap: int = 4,
        transcripts_dir: str = "transcripts",
    ) -> None:
        self.provider = provider
        self.council = council
        self.rounds_cap = rounds_cap
        self.aliases = make_aliases(council)  # real id -> anonymized alias
        self.transcripts = TranscriptStore(transcripts_dir)
        self.router = ProtocolRouter(provider, classifiers=(council[0], council[1]))
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
