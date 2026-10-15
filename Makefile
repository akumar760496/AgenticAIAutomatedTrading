# Development commands. Every target assumes `make install` has been run once.
.PHONY: help install check lint format types imports test test-cov verify verify-phase hooks clean

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-14s %s\n", $$1, $$2}'

install:  ## Create the venv and install everything, including dev tools
	uv sync --extra dev
	uv run pre-commit install

check: lint types imports test  ## Everything CI runs. This is the gate.

lint:  ## Lint (and check formatting)
	uv run ruff check src tests scripts
	uv run ruff format --check src tests scripts

format:  ## Fix formatting and auto-fixable lint
	uv run ruff format src tests scripts
	uv run ruff check --fix src tests scripts

types:  ## Type-check strictly
	uv run mypy

imports:  ## Enforce the layering rule (docs/01-architecture.md section 2)
	uv run lint-imports

test:  ## Run the test suite
	uv run pytest

test-cov:  ## Run tests with a coverage report
	uv run pytest --cov --cov-report=term-missing

verify:  ## Verify one build step: make verify STEP=0_1
	@test -n "$(STEP)" || (echo "Usage: make verify STEP=0_1"; exit 2)
	uv run python -m scripts.verify $(STEP)

verify-phase:  ## Verify every step in a phase: make verify-phase PHASE=0
	@test -n "$(PHASE)" || (echo "Usage: make verify-phase PHASE=0"; exit 2)
	@for f in scripts/verify/step_$(PHASE)_*.py; do \
		step=$$(basename $$f .py | sed 's/^step_//'); \
		uv run python -m scripts.verify $$step || exit 1; \
	done

hooks:  ## Run pre-commit over the whole repo
	uv run pre-commit run --all-files

clean:  ## Remove caches
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
