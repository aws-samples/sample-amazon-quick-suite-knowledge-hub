.PHONY: install lint format test-unit test-cov clean help check

PYTHON := uv run python
PYTEST := $(PYTHON) -m pytest
PYTHONPATH := PYTHONPATH=src

help:
	@echo "Available commands:"
	@echo "  make install       - Install dependencies"
	@echo "  make lint          - Run ruff linter"
	@echo "  make format        - Format code with ruff"
	@echo "  make test-unit     - Run unit tests only"
	@echo "  make test-cov      - Run unit tests with coverage"
	@echo "  make clean         - Remove cache files"
	@echo "  make check         - Run lint and unit tests"

install:
	uv sync --dev

lint:
	uv run ruff check src/ tests/

format:
	uv run ruff format src/ tests/
	uv run ruff check --fix src/ tests/

test-unit:
	$(PYTHONPATH) $(PYTEST) tests/unit/ --no-cov -v

test-cov:
	$(PYTHONPATH) $(PYTEST) tests/unit/ --cov=src --cov-report=term-missing --cov-report=html

clean:
	rm -rf .pytest_cache .ruff_cache .hypothesis htmlcov .coverage
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true

check: lint test-unit
