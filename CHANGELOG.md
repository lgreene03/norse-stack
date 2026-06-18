# Changelog

All notable changes to the Norse Stack meta-repo will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- Unified docker-compose.yml booting all four services plus infrastructure
- End-to-end smoke test validating the full trade-to-fill pipeline
- clone-all.sh script for one-command repo setup
- README with architecture diagram, service table, quick start guide
- CONTRIBUTING.md, SECURITY.md, CODE_OF_CONDUCT.md governance files
- Apache 2.0 LICENSE
- CLAUDE.md agent context

## [0.1.0] - 2026-06-18

### Added
- Initial meta-repo creation
- docker-compose.yml with muninn, huginn, sleipnir, redpanda, postgres, minio
- scripts/clone-all.sh
- scripts/smoke.sh (5-phase end-to-end pipeline test)
