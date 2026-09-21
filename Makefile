# Amazon Quick Knowledge Hub — developer commands
#
# The documentation site is built with MkDocs Material from mkdocs.yml at the
# repo root, with content under docs/. Code projects live at the repository
# root. These targets wrap the common workflows.

.PHONY: help setup install serve build build-strict lint format scan check fix clean

MKDOCS := mkdocs.yml

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

setup:  ## Install docs dependencies (uv)
	uv sync --dev

install:  ## Install pinned gitleaks + Prettier (uv run install)
	uv run install

serve:  ## Live-preview the docs site at http://127.0.0.1:8000
	uv run mkdocs serve -f $(MKDOCS)

build:  ## Build the docs site into site/
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

check:  ## Run all quality + security checks (ruff, mdformat, prettier, gitleaks, typos, mkdocs)
	uv run build

fix:  ## Auto-fix everything fixable (ruff, mdformat, prettier, typos)
	uv run fix

clean:  ## Remove generated build output
	rm -rf site
	rm -rf docs/_projects docs/infrastructure \
	       docs/integration \
	       docs/examples docs/amazon-quick-on-desktop
