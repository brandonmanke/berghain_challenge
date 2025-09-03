# Repository Guidelines

## Project Structure & Module Organization
- `src/berghain/`: core logic
  - `agent.py`: accept/reject policy interface and implementations
  - `scenarios.py`: scenario/constraints definitions and loaders
  - `simulator.py`: environment loop and accounting
  - `metrics.py`: scoring and reporting utilities
- `scripts/`: CLIs and notebooks-to-scripts (e.g., `play.py`, `benchmark.py`)
- `tests/`: pytest suite (e.g., `test_policy.py`, `test_simulator.py`)
- `data/`: optional local inputs (no PII; small files only)

## Build, Test, and Development Commands
- Create env: `python3 -m venv .venv && source .venv/bin/activate`
- Install deps: `pip install -U pip && pip install -r requirements.txt`
- Run tests: `pytest -q`
- Lint/format: `ruff check . && black --check .` (auto-fix with `black .`)
- Type-check: `mypy src`
- Run agent: `PYTHONPATH=src python scripts/play.py --scenario 1 --capacity 1000`
- Config via `.env` (auto-loaded): `BASE_URL=https://berghain.challenges.listenlabs.ai/`, `PLAYER_ID=<uuid>`

## Coding Style & Naming Conventions
- Python 3.11+, 4-space indent, max line length 100.
- Use type hints and docstrings for public functions.
- Names: modules/functions `snake_case`, classes `CapWords`, constants `UPPER_SNAKE`.
- Files: one primary class/module per file when feasible.
- Keep policies pure/deterministic; pass `random.Random(seed)` explicitly.

## Testing Guidelines
- Framework: `pytest` with `pytest-cov` (target ≥90% for policy logic).
- Tests live in `tests/`, named `test_*.py`; functions `test_*`.
- Include scenario fixtures and property-style tests (e.g., constraints never violated).
- Quick checks: `pytest -q -k policy` for selective runs.

## Commit & Pull Request Guidelines
- Use Conventional Commits: `feat: add threshold policy`, `fix(simulator): rounding bug`, `test: add coverage for s2`.
- PRs include: description, scenario(s) touched, reproduction command, before/after metrics (e.g., rejections to fill 1000), and screenshots of summaries if applicable.
- Keep PRs small and focused; add or update tests and docs.

## Security & Configuration Tips
- No secrets in code; keep `.env` out of VCS.
- Keep network calls out of core logic; simulate inputs locally.
- Make randomness/config explicit via CLI flags or `configs/*.yaml`.
