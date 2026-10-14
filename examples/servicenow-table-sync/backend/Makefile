# Backend (Python 3.12 + uv), seven-verb vocabulary.
# `check` is the CI gate and must not mutate files; `fix` is the mutating counterpart.

UV := uv
SRC := src
TESTS := tests
IMAGE := service-now-ingestion-backend
CONTAINER_CLI ?= finch

.PHONY: install build fix check test deploy clean

install:
	@test -f pyproject.toml || { echo "backend: no pyproject.toml yet — skipping install"; exit 0; }
	$(UV) sync --dev

fix:
	@test -f pyproject.toml || { echo "backend: no pyproject.toml yet — skipping fix"; exit 0; }
	$(UV) run ruff format $(SRC) $(TESTS)
	$(UV) run ruff check --fix $(SRC) $(TESTS)

check:
	@test -f pyproject.toml || { echo "backend: no pyproject.toml yet — skipping check"; exit 0; }
	$(UV) run ruff check $(SRC) $(TESTS)
	$(UV) run ruff format --check $(SRC) $(TESTS)
	$(UV) run mypy $(SRC)
	$(UV) run pytest -q

test:
	@test -f pyproject.toml || { echo "backend: no pyproject.toml yet — skipping test"; exit 0; }
	$(UV) run pytest --cov=$(SRC) --cov-report=term-missing

build:
	@if test -f Dockerfile; then \
		echo "backend: building container image $(IMAGE) with $(CONTAINER_CLI)"; \
		$(CONTAINER_CLI) build -t $(IMAGE) .; \
	elif test -f pyproject.toml; then \
		$(UV) build; \
	else \
		echo "backend: nothing to build yet — skipping"; \
	fi

deploy:
	@echo "backend: deployed as Lambda container images through the cdk project"

clean:
	rm -rf .venv build dist .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage
	find . -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
