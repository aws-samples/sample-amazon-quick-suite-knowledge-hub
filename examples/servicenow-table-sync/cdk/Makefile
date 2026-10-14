# CDK (TypeScript + npm), seven-verb vocabulary.
# `check` is the CI gate and must not mutate files; `fix` is the mutating counterpart.

.PHONY: install build fix check test deploy deploy-test clean _node_guard

SOLUTION_STACK := ServiceNowIngestionStack
TEST_STACK := ServiceNowIngestionTestStack

# Fail fast on an unsupported Node before npm corrupts node_modules. The tooling
# supports Node 22 or 24; Node 23 breaks unrs-resolver's postinstall (see .nvmrc).
_node_guard:
	@major=$$(node -p "process.versions.node.split('.')[0]"); \
	case "$$major" in \
	  22|24) : ;; \
	  *) echo "cdk: Node $$major is unsupported. Use Node 22 (run 'nvm use', see .nvmrc)."; exit 1 ;; \
	esac

install: _node_guard
	@if test -f package-lock.json; then npm ci; else npm install; fi

build:
	npm run build

fix:
	npm run format
	npm run lint:fix

check:
	npx tsc --noEmit
	npm run lint
	npm test

test:
	npm test

# Deploys the sync stack only. Create the ServiceNow secret first.
# The schedule is on by default. Pass CDK_ARGS="--context enableSchedule=false" to turn it off.
deploy: _node_guard
	npx cdk deploy $(SOLUTION_STACK) --require-approval never $(CDK_ARGS)

# Optional. Deploys the simulator-backed test stack, which is not part of the app unless
# enableTestStack is true. It needs no ServiceNow instance.
deploy-test: _node_guard
	npx cdk deploy $(TEST_STACK) --require-approval never --context enableTestStack=true $(CDK_ARGS)

clean:
	rm -rf cdk.out node_modules coverage
