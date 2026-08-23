"""`zhoda deliberate "..."` — interactive Stage 0 + verdict output.

Round-11 §2: empty paths_rejected on clean unanimity reads as 'clean
unanimity — nothing was disputed', not as a bare 0 ('the protocol found
nothing'). The metric stays honest; the presentation stops underselling it.
"""

import asyncio
from pathlib import Path

import typer
import yaml
from rich.console import Console

from .elicitor import ClarifyingQuestion
from .engine import ZhodaEngine
from .models import Protocol
from .providers.openrouter import OpenRouterProvider

app = typer.Typer(help="Zhoda — models argue until they reach zhoda.")
console = Console()


def ask_user(questions: list[ClarifyingQuestion]) -> list[str]:
    """Close the elicitation loop: show questions, collect answers."""
    answers = []
    for q in questions:
        console.print(f"\n[bold]{q.question}[/bold]\n[dim]{q.why_it_matters}[/dim]")
        if q.options:
            console.print("options: " + " | ".join(q.options))
        answers.append(typer.prompt("answer", default="", show_default=False))
    return answers


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
            max_concurrency=cfg.get("max_concurrency", 4),
            prices=cfg.get("prices"),
            cache_path=cfg.get("cache_path"),
        )
        try:
            judges = cfg.get("judges")
            if not judges:
                console.print("[red]no judges configured — judges must sit outside "
                              "the council; refusing to start[/red]")
                raise typer.Exit(code=2)
            in_council = [j for j in judges if j in cfg["council"]]
            if in_council:
                console.print(f"[yellow]judges sit in the council: {in_council}[/yellow]")
            escalation = cfg.get("escalation", {}) or {}
            engine = ZhodaEngine(
                provider,
                cfg["council"],
                chairman=cfg.get("chairman", cfg["council"][0]),
                judges=tuple(judges),
                router_classifiers=tuple(cfg["router_classifiers"]),
                rounds_cap=cfg.get("rounds_cap", 4),
                stability_rounds=cfg.get("stability_rounds", 2),
                devils_advocate=cfg.get("devils_advocate", True),
                ambiguity_threshold=cfg.get("ambiguity_threshold", 0.6),
                max_new_per_round=cfg.get("max_new_per_round", 3),
                max_active=cfg.get("max_active", 6),
                escalation_model=(
                    escalation.get("model") if escalation.get("enabled") else None
                ),
                transcripts_dir=cfg.get("transcripts_dir", "transcripts"),
            )
            verdict = await engine.deliberate(
                question,
                force_protocol=Protocol(protocol) if protocol else None,
                clarify_mode=clarify,
                on_questions=ask_user if clarify == "smart" else None,
            )
            console.print(f"\n[bold]zhoda_reached:[/bold] {verdict.zhoda_reached} "
                          f"({verdict.consensus_strength}, rounds: {verdict.rounds_taken})")
            if verdict.decision_origin != "council":
                console.print(f"[red]APPELLATE DECISION without consensus "
                              f"(judge: {verdict.escalated_to})[/red]")
            console.print(f"[bold]decision:[/bold]\n{verdict.decision}")
            if verdict.minority_report:
                console.print(f"[bold]minority report:[/bold]\n{verdict.minority_report}")
            if verdict.value_map.open_ambiguities:
                console.print(f"[dim]unanswered ambiguities: "
                              f"{len(verdict.value_map.open_ambiguities)}[/dim]")
            if verdict.switches:
                console.print(f"[bold]switches:[/bold] {len(verdict.switches)}")
            if verdict.paths_rejected:
                console.print(f"[bold]paths rejected:[/bold] {len(verdict.paths_rejected)}")
            elif verdict.zhoda_reached and not verdict.minority_report:
                console.print("[dim]clean unanimity — nothing was disputed, "
                              "nothing to reject[/dim]")
            else:
                console.print("[dim]paths rejected: 0[/dim]")
            if verdict.plan_contract:
                console.print(f"[bold]plan contract:[/bold] "
                              f"{len(verdict.plan_contract.steps)} steps, "
                              f"{len(verdict.plan_contract.rejected_paths)} rejected paths")
            else:
                console.print("[dim]no plan contract — no zhoda[/dim]")
            console.print(f"cost: {verdict.cost.requests} requests, "
                          f"${verdict.cost.usd:.4f}, transcript: {verdict.transcript_id}")
            if verdict.cost.breakdown:
                console.print(f"[dim]breakdown: {verdict.cost.breakdown}[/dim]")
        finally:
            await provider.close()

    asyncio.run(run())


if __name__ == "__main__":
    app()
