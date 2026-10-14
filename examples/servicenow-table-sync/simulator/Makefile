# ServiceNow simulator (Python 3.12 + uv).

UV := uv
SRC := src
TESTS := tests
PORT ?= 8080

.PHONY: install build fix check test deploy clean run

install:
	@test -f pyproject.toml || { echo "simulator: no pyproject.toml yet — skipping"; exit 0; }
	$(UV) sync --dev

fix:
	$(UV) run ruff format $(SRC) $(TESTS)
	$(UV) run ruff check --fix $(SRC) $(TESTS)

check:
	$(UV) run ruff check $(SRC) $(TESTS)
	$(UV) run ruff format --check $(SRC) $(TESTS)
	$(UV) run mypy $(SRC)
	$(UV) run pytest -q

test:
	$(UV) run pytest -q

build:
	@echo "simulator: no build artifact — run 'make run' to serve locally"

deploy:
	@echo "simulator: local-only test server; not deployed"

clean:
	rm -rf .venv .pytest_cache .mypy_cache .ruff_cache
	find . -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true

# Serve the simulator on localhost for an end-to-end run against the pipeline.
run:
	$(UV) run uvicorn app:create_app --factory --host 127.0.0.1 --port $(PORT) --app-dir $(SRC)
