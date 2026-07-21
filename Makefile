# Norse Stack — top-level convenience targets.
#
# The bare-clone quickstart problem: this repo's docker-compose.yml builds the
# engine services (muninn, huginn, sleipnir) from sibling checkouts
# (../muninn, ../huginn, ../sleipnir). A fresh `git clone` of norse-stack alone
# does NOT have those siblings, so `docker compose up -d` fails with a build
# context error until they exist. `make bootstrap` fixes that in one command:
# it clones the siblings, then builds and boots the stack.

SHELL := /bin/bash

.PHONY: help bootstrap clone build up up-ghcr smoke down config bench

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

bootstrap: clone up ## One command for a bare clone: clone sibling repos, then build + boot the full stack
	@echo "Bootstrap complete. Try: make smoke"

clone: ## Clone the sibling service repos (../muninn ../huginn ../sleipnir ../muninn-py)
	./scripts/clone-all.sh

build: ## Build all service images from the sibling checkouts (requires: make clone)
	docker compose build

up: ## Build (if needed) and boot the full stack from sibling checkouts
	docker compose up -d --build

up-ghcr: ## Boot using prebuilt GHCR images for the engine services (no sibling checkouts needed; requires published images)
	docker compose -f docker-compose.yml -f docker-compose.ghcr.yml up -d

smoke: ## Run the end-to-end smoke test
	./scripts/smoke.sh

config: ## Validate the compose configuration
	docker compose config -q && echo "compose config OK"

bench: ## Run the storage-path latency microbenchmark (Mimir point-in-time store)
	python3 services/mimir/bench_pit.py

down: ## Tear down the stack and volumes
	docker compose down -v
