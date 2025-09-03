PY_VER ?= 3.12
SCENARIO ?= 1
CAPACITY ?= 1000
TIMEOUT ?=
RETRIES ?=
ARGS ?=

.PHONY: help python venv sync dev run bench resume resume-latest test lint fmt typecheck clean

help:
	@echo "Targets:"
	@echo "  python   - Install Python $(PY_VER) via uv"
	@echo "  venv     - Create venv with uv (Python $(PY_VER))"
	@echo "  sync     - Install runtime deps"
	@echo "  dev      - Install dev deps"
	@echo "  run      - Run agent (vars: SCENARIO, CAPACITY, TIMEOUT, RETRIES, ARGS)"
	@echo "  test     - Run pytest"
	@echo "  lint     - Ruff checks"
	@echo "  fmt      - Black format"
	@echo "  typecheck- Mypy type checks"
	@echo "  clean    - Remove caches"

python:
	uv python install $(PY_VER)

venv: python
	uv venv --python $(PY_VER)

sync:
	uv sync

dev:
	uv sync --extra dev || uv sync

run:
	uv run python scripts/play.py --scenario $(SCENARIO) --capacity $(CAPACITY) \
		$(if $(TIMEOUT),--timeout $(TIMEOUT),) $(if $(RETRIES),--retries $(RETRIES),) \
		$(if $(LOG_JSON),--log-json $(LOG_JSON),) $(if $(LOG_INTERVAL),--log-interval $(LOG_INTERVAL),) $(ARGS)

bench:
	uv run python scripts/bench.py $(if $(SCENARIOS),--scenarios $(SCENARIOS),) \
		$(if $(CAPACITY),--capacity $(CAPACITY),) $(if $(TIMEOUT),--timeout $(TIMEOUT),) \
		$(if $(RETRIES),--retries $(RETRIES),) $(if $(VERBOSE),--verbose,) \
		$(if $(JSON_OUT),--json-out $(JSON_OUT),)

resume:
	uv run python scripts/play.py --scenario $(SCENARIO) --capacity $(CAPACITY) \
		$(if $(TIMEOUT),--timeout $(TIMEOUT),) $(if $(RETRIES),--retries $(RETRIES),) \
		$(if $(LOG_JSON),--log-json $(LOG_JSON),) $(if $(LOG_INTERVAL),--log-interval $(LOG_INTERVAL),) \
		$(if $(RESUME_LOG),--resume-from-log $(RESUME_LOG),) $(if $(GAME_ID),--game-id $(GAME_ID),) \
		$(if $(START_INDEX),--start-index $(START_INDEX),) $(ARGS)

resume-latest:
	uv run python scripts/resume.py $(if $(POLICY),--policy $(POLICY),) $(if $(VERBOSE),--verbose,) \
		$(if $(JSON_LOG),--json-log $(JSON_LOG),) $(if $(LOG_INTERVAL),--log-interval $(LOG_INTERVAL),)

test:
	PYTHONPATH=src uv run pytest -q

lint:
	uvx ruff check .

fmt:
	uvx black .

typecheck:
	uvx mypy src

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache **/__pycache__
