# ServiceNow to Amazon Quick ingestion: root automation entry point.
#
# Every sub-project exposes the same seven verbs. This Makefile runs each verb
# across all of them in a fixed order. CI runs these same targets.
#
#   make install   Install dependencies in every sub-project
#   make build     Build/package every artifact
#   make fix       Auto-format and lint --fix (mutating)
#   make check     Lint + type-check + unit tests (the CI gate; never mutates)
#   make test      Tests with coverage
#   make deploy    Deploy/publish
#   make clean     Remove build artifacts

SUBPROJECTS := backend cdk simulator

.PHONY: help install build fix check test deploy clean $(SUBPROJECTS)

help:
	@echo "ServiceNow → Amazon Quick ingestion"
	@echo ""
	@echo "  make install   Install dependencies (uv / npm) across $(SUBPROJECTS)"
	@echo "  make build     Compile/package artifacts"
	@echo "  make fix       Auto-format + lint --fix (mutates files)"
	@echo "  make check     Lint + type-check + unit tests (CI gate, non-mutating)"
	@echo "  make test      Tests with coverage"
	@echo "  make deploy    Deploy / publish"
	@echo "  make clean     Remove build artifacts"
	@echo ""
	@echo "Scope a verb to one project with: make -C <backend|cdk> <verb>"

install build fix check test deploy clean:
	@for project in $(SUBPROJECTS); do \
		echo "==> $$project: $@"; \
		$(MAKE) --no-print-directory -C $$project $@ || exit $$?; \
	done
