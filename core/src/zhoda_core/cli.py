"""`zhoda deliberate "..."` — live round output via rich."""

import typer

app = typer.Typer(help="Zhoda — models argue until they reach zhoda.")


@app.command()
def deliberate(
    question: str,
    protocol: str | None = typer.Option(None, help="Force: vote | debate | red_team"),
    clarify: str = typer.Option("smart", help="smart | no-clarify | auto-clarify"),
    config: str = typer.Option("zhoda.yaml"),
) -> None:
    """Run a full deliberation and print the verdict."""
    raise NotImplementedError  # TODO(mvp)


if __name__ == "__main__":
    app()
