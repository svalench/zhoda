# Live G — 2026-09-06

Preregistration: `docs/eval/2026-09-06-preregistration.md`.  
**Не** вызывать `python -m zhoda_core.eval freeze-manifest`.

```bash
cd core
PYTHONUNBUFFERED=1 uv run python ../docs/live-runs/2026-09-06-g-pilot/run_live_g.py --self-check
PYTHONUNBUFFERED=1 uv run python ../docs/live-runs/2026-09-06-g-pilot/run_live_g.py
```

Кап: **$8.00** engine+evaluator. Arms: `zhoda`, `short_review`, `majority`.  
Gold — только после attempts. Independent validation = false.
