"""Собрать метрики живого прогона из хронік, не из сломанного TSV логов."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

TRANSCRIPT_RE = re.compile(r"transcript:\s+([0-9a-f]{12})")


def events(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def verdict_of(ev: list[dict]) -> dict | None:
    for item in reversed(ev):
        if item.get("stage") == "verdict":
            payload = item.get("verdict")
            return payload if isinstance(payload, dict) else item
    return None


def analyze_run(run_dir: Path, transcripts_dir: Path) -> list[dict]:
    rows: list[dict] = []
    for log in sorted(run_dir.glob("*.*.log")):
        case_id, pass_n, _ext = log.name.rsplit(".", 2)
        text = log.read_text(encoding="utf-8", errors="replace")
        match = TRANSCRIPT_RE.search(text)
        if match is None:
            continue
        tid = match.group(1)
        v = verdict_of(events(transcripts_dir / f"{tid}.jsonl")) or {}
        cost = v.get("cost") or {}
        route = next((e for e in events(transcripts_dir / f"{tid}.jsonl") if e.get("stage") == "route"), {})
        rows.append(
            {
                "id": case_id,
                "pass": pass_n,
                "exit": 0 if v else 1,
                "protocol": v.get("protocol") or route.get("protocol"),
                "task_class": route.get("task_class"),
                "zhoda": v.get("zhoda_reached"),
                "strength": v.get("consensus_strength"),
                "origin": v.get("decision_origin"),
                "rounds": v.get("rounds_taken"),
                "requests": cost.get("requests"),
                "usd": cost.get("usd"),
                "cache_hits": cost.get("cache_hits"),
                "switches": len(v.get("switches") or []),
                "paths_rejected": len(v.get("paths_rejected") or []),
                "plan": v.get("plan_contract") is not None,
                "transcript": tid,
                "ic": bool(v.get("insufficient_context")),
            }
        )
    return rows


def main() -> None:
    run_dir = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    transcripts_dir = (
        Path(sys.argv[2]) if len(sys.argv) > 2 else run_dir.parents[2] / "transcripts"
    )
    rows = analyze_run(run_dir, transcripts_dir)
    out = run_dir / "analysis.json"
    out.write_text(json.dumps(rows, indent=2, default=str))
    keys = [
        "id", "pass", "protocol", "zhoda", "strength", "origin", "rounds",
        "requests", "usd", "cache_hits", "switches", "paths_rejected", "plan",
        "transcript", "ic",
    ]
    print("\t".join(keys))
    for row in rows:
        print("\t".join(str(row.get(k, "")) for k in keys))
    print(f"wrote {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
