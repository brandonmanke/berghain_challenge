# Berghain Challenge Agent

Opinionated Python 3.12 agent for the Berghain admission challenge. It talks to the public API, applies pluggable policies, logs progress in NDJSON, and supports resuming runs after errors.

## Quick Start (uv + Makefile)
- Install Python and create a venv:
  - `make python venv dev`
- Configure environment (auto‑loaded from `.env`):
  - `BASE_URL=https://berghain.challenges.listenlabs.ai/`
  - `PLAYER_ID=<your-uuid>`
- Run the agent (default reserve policy):
  - `make run SCENARIO=1 CAPACITY=1000 ARGS="--verbose --progress-interval 50"`

## Policies
- `reserve`: conservative “quota reserve” rule that never jeopardizes feasibility.
  - `make run ARGS="--policy reserve"`
- `window`: sliding‑window helpfulness estimator (size=500, margin=0.15) to accept more when arrivals look favorable.
  - `make run ARGS="--policy window"`
  - Tunables: `--window-size`, `--risk-margin`, `--min-observations`.
  - Example: `make run ARGS="--policy window --window-size 600 --risk-margin 0.12 --min-observations 120"`
- `ewma`: exponentially‑weighted moving average of helpfulness (alpha=0.03, warmup=100).
  - `make run ARGS="--policy ewma"`
  - Tunables: `--alpha`, `--risk-margin`, `--warmup`.
  - Example (less conservative): `make run ARGS="--policy ewma --alpha 0.05 --risk-margin 0.12 --warmup 90"`
- `attr-ewma`: per-attribute EWMA; accepts non‑helpful only if the expected helpful arrivals in remaining capacity cover every underfilled constraint with margin.
  - `make run ARGS="--policy attr-ewma"`
  - Tunables: `--alpha`, `--risk-margin`, `--warmup`.
  - Example (less conservative):
    `make run ARGS="--policy attr-ewma --alpha 0.05 --risk-margin 0.1 --warmup 80"`

## Logging & Resume
- Enable NDJSON logs for progress + recovery:
  - `make run LOG_JSON=logs/run.ndjson LOG_INTERVAL=1 ARGS="--policy ewma --verbose"`
- Auto‑resume from latest log (rebuilds policy state and resyncs indices):
  - `make resume-latest POLICY=ewma VERBOSE=1 JSON_LOG=logs/resume.ndjson LOG_INTERVAL=1`
- Resume from a specific log:
  - `make resume RESUME_LOG=logs/run.ndjson ARGS="--policy reserve --verbose"`
- Manual recovery (rare): if you see `{ "error": "Expected person N, got M" }`:
  - `make resume GAME_ID=<uuid> START_INDEX=N ARGS="--policy reserve"`

## Benchmarks
- Run multiple scenarios and summarize results:
  - `make bench SCENARIOS=1,2,3 JSON_OUT=bench.json VERBOSE=1 ARGS="--policy ewma"`

## Useful Flags (CLI)
- Connectivity: `--timeout 60`, `--retries 5`
- Progress: `--verbose`, `--progress-interval 100`, `--progress-attrs 3`
- Logging: `--log-json logs/run.ndjson`, `--log-interval 50`
- Policy: `--policy {reserve,window,ewma,attr-ewma}`

## Tuning Guide
- Safety vs. speed:
  - Increase `--risk-margin` or `--warmup` if you ever fail constraints.
  - Decrease them to cut rejections once runs are consistently safe.
- Suggested ranges:
  - `--alpha`: 0.03–0.06 (higher adapts faster).
  - `--risk-margin`: 0.10–0.25 (higher is safer; more rejections).
  - `--warmup`: 80–200 (higher is safer early).
  - `--window-size`: 300–800; `--min-observations`: 80–150.
- Practical combos:
  - Balanced attr‑ewma: `--alpha 0.05 --risk-margin 0.12 --warmup 100`.
  - Safer attr‑ewma: `--alpha 0.04 --risk-margin 0.18 --warmup 180`.

## Dev
- Lint/format/test: `make lint && make fmt && make test`
- Type check (optional): `make typecheck`

Notes
- The agent automatically handles personIndex sequencing and will “resync” if the server reports an expected index.
- For best resume fidelity, keep `LOG_INTERVAL=1`.
