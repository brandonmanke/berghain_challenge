# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Berghain Challenge Agent is a Python 3.12 agent for solving the Berghain admission challenge. It connects to a public API to make accept/reject decisions for candidates using pluggable admission policies, logs all progress in NDJSON format, and supports resuming interrupted runs from logs.

## Development Commands

### Setup
```bash
# Install Python and create virtual environment
make python venv dev

# Or manually with uv
uv python install 3.12
uv venv --python 3.12
uv sync --extra dev
```

### Testing & Quality
```bash
make test          # Run pytest suite
make lint          # Run ruff checks
make fmt           # Format with black
make typecheck     # Run mypy type checks
```

### Running the Agent
```bash
# Basic run with default reserve policy
make run SCENARIO=1 CAPACITY=1000 ARGS="--verbose --progress-interval 50"

# Run with specific policy
make run ARGS="--policy ewma --alpha 0.05 --risk-margin 0.12"

# Run benchmarks across multiple scenarios
make bench SCENARIOS=1,2,3 VERBOSE=1 JSON_OUT=bench.json
```

### Resuming Runs
```bash
# Resume from the latest log automatically
make resume-latest POLICY=ewma VERBOSE=1

# Resume from specific log file
make resume RESUME_LOG=logs/run.ndjson ARGS="--policy ewma --verbose"
```

## Architecture

### Core Modules (src/berghain/)

**runner.py** - Main orchestration logic
- `run_game()`: orchestrates a complete game from start to finish
- `resume_game()`: reconstructs policy state from NDJSON logs and continues an interrupted run
- `_reconstruct_from_log()`: parses log file to rebuild accepted counts, constraints, and next index
- Handles automatic index resyncing when the API reports "Expected person X, got Y" errors
- Integrates with JsonLinesLogger for NDJSON progress tracking

**policy.py** - Base admission policy (QuotaReservePolicy)
- Conservative baseline: reserves capacity to guarantee constraint feasibility
- Accept if candidate contributes to underfilled attributes (helpful)
- Otherwise accept only if aggregate slack S < R (remaining capacity)
- All policies implement: `decide()`, `update_on_accept()`, `remaining_needed()`, `record_observation()`

**ewma_policy.py** - EWMA-based relaxed policy
- Maintains single global EWMA estimate `p_hat` of helpful arrival rate
- Accepts non-helpful candidates when: `p_hat >= S/(R-1) * (1 + risk_margin)`
- Warmup phase uses conservative reserve logic until enough observations collected
- Tunables: `--alpha` (0.03-0.06), `--risk-margin` (0.10-0.25), `--warmup` (80-200)

**attr_ewma_policy.py** - Per-attribute EWMA policy (most sophisticated)
- Maintains per-attribute EWMA rates `p_hat[a]` for each constrained attribute
- Accepts candidates only if expected helpful arrivals cover every underfilled attribute with margin
- For each underfilled attribute: `count[a] + p_hat[a] * (R-1) >= minCount[a] * (1 + margin_eff)`
- Optional correlation-aware expectations inflate rates using positive correlations between attributes
- Optional top-K gating focuses checks on K tightest underfilled attributes
- Initialized with API-provided priors for faster convergence
- Resume: replays log events to rebuild EWMA state by computing helpfulness at each historical decision

**window_policy.py** - Sliding-window relaxed policy
- Maintains recent helpfulness observations in fixed-size window
- Computes empirical helpful rate from window
- Similar relaxation logic to EWMA but uses recent sample average instead of exponential weighting

**client.py** - HTTP API client wrapper
- `new_game()`: starts new game, fetches constraints and attribute statistics
- `decide_and_next()`: submits decision for current person and fetches next candidate
- Returns structured responses: DecideAndNextRunning, DecideAndNextCompleted, DecideAndNextFailed
- Automatic retry logic with exponential backoff for transient errors
- Raises ApiError for 4xx responses with parsed error messages

**logging_utils.py** - NDJSON event logger
- JsonLinesLogger writes structured events: start, request, response, progress, completed, failed, resync
- Each event includes scenario, gameId, timestamps, and context-specific fields
- Critical for resume: logs accepted decisions with full attribute vectors for state reconstruction

**types.py** - Type definitions
- Constraint, Person, NewGameResponse, DecideAndNextResponse variants
- AttributeStatistics with relativeFrequencies and correlations

### Policy State Reconstruction for Resume

When resuming from a log file, runner.py:
1. Parses the log to extract: constraints, capacity, gameId, accepted_counts, next_index
2. Instantiates the policy with the same parameters
3. Replays all logged request events to:
   - Compute "helpful" at each decision based on constraints and running counts
   - Call `policy.record_observation(helpful)` to rebuild EWMA/window state
   - Update `accepted_attribute_counts` for accepted decisions
4. Resumes from the next expected personIndex with the reconstructed state

This ensures EWMA estimates and window buffers match what they would have been if the run had never been interrupted.

## Configuration

Environment variables (auto-loaded from `.env`):
- `BASE_URL`: API endpoint (default: https://berghain.challenges.listenlabs.ai/)
- `PLAYER_ID`: UUID identifying the player (required)
- `TIMEOUT`: HTTP timeout in seconds (default: 30)
- `RETRIES`: Number of retries on transient errors (default: 3)

## Policy Tuning Guidelines

**reserve policy**: No tuning needed; always safe but may reject more than necessary.

**ewma/attr-ewma**:
- Start safe: `--alpha 0.03 --risk-margin 0.18 --warmup 150`
- Reduce rejections once stable: `--alpha 0.05 --risk-margin 0.12 --warmup 100`
- Faster adaptation when distribution is steady: `--alpha 0.06`

**attr-ewma advanced**:
- Top-K gating for many constraints: `--gate-top-k 5` (checks only 5 tightest attributes)
- Correlation-aware expectations: `--corr-aware --corr-beta 0.25` (inflates rates using positive correlations)

**window policy**:
- Larger window is more stable but slower to adapt: `--window-size 600`
- Smaller margin accepts more non-helpful arrivals: `--risk-margin 0.12`

## Testing

- Tests in `tests/` use pytest
- `test_policies.py`: unit tests for policy decision logic
- `test_reconstruct.py`: tests log parsing and state reconstruction for resume
- Run specific tests: `pytest -q -k policy`

## Logging Best Practices

- Use `LOG_INTERVAL=1` for best resume fidelity (logs every decision)
- Logs default to `logs/run-<policy>-s<scenario>-<timestamp>.ndjson`
- Each log captures: constraints, decisions with attributes, accepted counts, progress snapshots
- Logs enable offline analysis and fault-tolerant resumption

## Important Design Considerations

### Policy State Mutation in `decide()`
All relaxed policies (EWMA, window, attr-EWMA) mutate internal state during `decide()` calls:
- `ewma_policy.py:77` updates p_hat before making decision
- `window_policy.py:75` records helpfulness in sliding window
- `attr_ewma_policy.py:94` updates per-attribute EWMA rates

This is intentional - the current observation must be included in the streaming estimate. However, it means:
- Policies are NOT pure functions - calling `decide()` has side effects
- You cannot safely call `decide()` multiple times for the same candidate
- State updates happen regardless of whether the candidate is accepted

### Resume Limitations
- attr-ewma policy loses API-provided priors (`relativeFrequencies`, `correlations`) on resume
- These are not logged, so resumed runs start with less informed rate estimates
- Performance may differ slightly between fresh and resumed runs

## Code Style & Conventions

- Python 3.12+, 4-space indent, max line length 100 (Black/Ruff)
- Type hints required for all public functions
- Naming: `snake_case` (functions/variables), `CapWords` (classes), `UPPER_SNAKE` (constants)
- Docstrings: explain the "why" and algorithm, especially for policy math
- Keep policies deterministic; if randomness needed, pass explicit `random.Random(seed)`
- Conventional Commits: `feat:`, `fix:`, `test:`, `docs:`, `refactor:`

## Known Issues & Improvement Opportunities

1. **Error Parsing**: Resume logic (runner.py:272-274, runner.py:614-615) uses regex to parse "Expected person X, got Y" errors - fragile if API message format changes. Should use structured error codes if available.

2. **Logging I/O**: JsonLinesLogger opens/closes file for every write (no buffering). With LOG_INTERVAL=1, this could impact performance. Trade-off: durability vs performance.

3. **Hardcoded Policy Defaults**: Default parameters (alpha, risk_margin, warmup) appear in both runner.py and policy class definitions. If defaults change, must update multiple locations.

## TODO: Priority Improvements

### High Priority - Integration Tests

**Status**: Critical gap - currently ~6 test functions for ~1,500 LOC

**What's needed**:
- Integration tests for `runner.py:run_game()` with mocked API client
  - Test full game loop from start to completion
  - Test constraint satisfaction for various scenarios
  - Test policy state updates during game execution

- Tests for `runner.py:_reconstruct_from_log()` resume logic
  - Test parsing of NDJSON logs with all event types
  - Test state reconstruction (constraints, accepted_counts, EWMA state)
  - Test extraction of priors and correlations from logs
  - Test handling of malformed/incomplete logs

- Tests for error handling paths
  - API retry logic with transient failures
  - Resync logic when "Expected person X, got Y" errors occur
  - Network timeout handling
  - Invalid API responses

- Property-based tests
  - Constraints never violated (property: sum(remaining_needed) >= 0 at completion)
  - Capacity never exceeded (property: admitted_count <= capacity always)
  - EWMA estimates remain in [0, 1] (property: 0 <= p_hat <= 1)

**Approach**:
- Use `unittest.mock` to mock `ApiClient` responses
- Create fixture NDJSON logs for resume testing
- Use pytest parametrize for testing multiple scenarios/policies
- Add coverage reporting: `pytest --cov=src --cov-report=term-missing`

**Target**: ≥90% coverage for policy logic, ≥80% overall (per AGENTS.md)
