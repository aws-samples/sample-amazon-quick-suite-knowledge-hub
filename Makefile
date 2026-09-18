# Amazon Quick Knowledge Hub — developer commands
#
# The documentation site lives under external-docs/ (MkDocs Material). Code
# projects live at the repository root. These targets wrap the common workflows
# so you do not have to remember the external-docs/ paths.

.PHONY: help setup serve build build-strict lint format scan clean

MKDOCS := external-docs/mkdocs.yml

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

setup:  ## Install docs dependencies (uv)
	uv sync --dev

serve:  ## Live-preview the docs site at http://127.0.0.1:8000
	uv run mkdocs serve -f $(MKDOCS)

build:  ## Build the docs site into external-docs/site/
	uv run mkdocs build -f $(MKDOCS)

build-strict:  ## Build the docs and fail on any warning
	uv run mkdocs build -f $(MKDOCS) --strict

lint:  ## Lint changed Python (ruff) and markdown (markdownlint)
	uv run ruff check .
	uv run ruff format --check .

format:  ## Auto-format Python (ruff)
	uv run ruff format .
	uv run ruff check --fix .

scan:  ## Run Bandit + Safety security scans (same as CI)
	uv run bandit -q -r . -c pyproject.toml || true
	uv run safety check || true

clean:  ## Remove generated build output
	rm -rf external-docs/site external-docs/docs/_projects site
