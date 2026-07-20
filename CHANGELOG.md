# Changelog

All notable changes to the Norse Stack meta-repo will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- Unified docker-compose.yml booting the full stack (23 containers) plus infrastructure
- End-to-end smoke test validating the full trade-to-fill pipeline
- clone-all.sh script for one-command repo setup
- README with architecture diagram, service table, quick start guide
- CONTRIBUTING.md, SECURITY.md, CODE_OF_CONDUCT.md governance files
- Apache 2.0 LICENSE
- CLAUDE.md agent context
- Analytics & ML services (built from `services/`): Odin (performance/risk analytics,
  :8086), Bragi (trade explainability, :8087), Huginn AI (XGBoost ML signal predictor,
  :8092), and News Sentinel (LLM news-sentiment feed, :8089)
- Mimir point-in-time feature store (:8095) and Forseti execution TCA (:8096) services
- Heimdall market-regime detector (:8097): Gaussian HMM fit with Baum-Welch (EM),
  causal forward-filtered state, online refit, and a Mimir warm-start so it is trained
  on boot rather than waiting for the live feed. Descriptive regime labels only (no edge claim)
- Forseti market-impact & capacity endpoints (`/api/impact`, `/api/impact/schedule`,
  `/api/capacity`): square-root-law + Almgren-Chriss impact and assumed-edge capacity bound
- Console panels for both: MARKET REGIME · HEIMDALL and MARKET IMPACT & CAPACITY · FORSETI,
  wired live through the same-origin proxy (serve.py) with honest no-edge disclaimers
- Research gateway service (validation-as-a-service, walk-forward + PBO + Deflated-Sharpe,
  built from `../huginn` cmd/research, :8094)
- obi-bridge service (order-book-imbalance feature bridge, built from `services/obi-bridge`)
- Monitoring stack: Prometheus (:9091), Alertmanager (:9093), Grafana (:3001) with
  provisioned dashboards/datasources, and Tempo tracing
- Web console (`console/`) for live monitoring and HALT/RESUME breaker control

## [0.1.0] - 2026-06-18

### Added
- Initial meta-repo creation
- docker-compose.yml with muninn, huginn, sleipnir, redpanda, postgres, minio
- scripts/clone-all.sh
- scripts/smoke.sh (5-phase end-to-end pipeline test)
