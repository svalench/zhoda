"""`zhoda deliberate "..."` — live verdict output via rich."""

import asyncio
from pathlib import Path

import typer
import yaml
from rich.console import Console

from .engine import ZhodaEngine
from .models import Protocol
from .providers.openrouter import OpenRouterProvider

app = typer.Typer(help="Zhoda — models argue until they reach zhoda.")
console = Console()


@app.command()
def deliberate(
    question: str,
    protocol: str | None = typer.Option(None, help="Force: vote | debate | red_team"),
    clarify: str = typer.Option("smart", help="smart | no-clarify | auto-clarify"),
    config: str = typer.Option("zhoda.yaml"),
) -> None:
    """Run a full deliberation and print the verdict."""
    cfg = yaml.safe_load(Path(config).read_text())

    async def run() -> None:
        provider = OpenRouterProvider(
            budget_usd=cfg.get("budget_per_question_usd", 0.0),
        )
        try:
            engine = ZhodaEngine(
                provider,
                cfg["council"],
                chairman=cfg.get("chairman", cfg["council"][0]),
                rounds_cap=cfg.get("rounds_cap", 4),
            )
            verdict = await engine.deliberate(
                question,
                force_protocol=Protocol(protocol) if protocol else None,
                clarify_mode=clarify,
            )
            console.print(f"\n[bold]zhoda_reached:[/bold] {verdict.zhoda_reached} "
                          f"({verdict.consensus_strength}, rounds: {verdict.rounds_taken})")
            console.print(f"[bold]decision:[/bold]\n{verdict.decision}")
            if verdict.minority_report:
                console.print(f"[bold]minority report:[/bold]\n{verdict.minority_report}")
            console.print(f"cost: {verdict.cost.requests} requests, "
                          f"${verdict.cost.usd:.4f}, transcript: {verdict.transcript_id}")
        finally:
            await provider.close()

    asyncio.run(run())


if __name__ == "__main__":
    app()
