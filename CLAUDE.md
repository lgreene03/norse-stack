# CLAUDE.md

## What Is norse-stack

Meta-repository and entry point for the Norse Stack — a four-service distributed quantitative trading infrastructure. This repo contains the unified docker-compose, end-to-end smoke test, and architecture documentation. It does not contain application code; each service lives in its own repo.

## Commands

```bash
# Clone all service repos (safe to re-run)
./scripts/clone-all.sh

# Boot the full stack (builds all service Docker images)
docker compose up -d --build

# Run the end-to-end smoke test
./scripts/smoke.sh

# Tear down
docker compose down -v
```

## Service Repos

All repos are expected as sibling directories:

```
parent/
  norse-stack/     ← this repo
  muninn/          ← Java feature engine
  huginn/          ← Go strategy engine
  sleipnir/        ← Go execution gateway
  muninn-py/       ← Python research SDK
```

## Ports

| Port  | Service            |
|-------|--------------------|
| 8080  | Muninn API         |
| 8083  | Huginn API         |
| 8085  | Sleipnir API       |
| 8088  | Redpanda Console   |
| 9002  | MinIO API          |
| 9003  | MinIO Console      |
| 5437  | PostgreSQL (Muninn)|
| 5436  | PostgreSQL (Huginn)|
| 19092 | Redpanda (Kafka)   |
