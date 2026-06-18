# Contributing to Norse Stack

Thank you for your interest in contributing. Norse Stack is a multi-repo project — most code lives in the individual service repositories.

## Where to Contribute

| Change type | Where |
|-------------|-------|
| Feature engine (Java) | [muninn](https://github.com/lgreene03/muninn) |
| Strategy engine (Go) | [huginn](https://github.com/lgreene03/huginn) |
| Execution gateway (Go) | [sleipnir](https://github.com/lgreene03/sleipnir) |
| Research SDK (Python) | [muninn-py](https://github.com/lgreene03/muninn-py) |
| Stack-level compose, smoke test, docs | This repo |

Each service repo has its own `CONTRIBUTING.md` with build instructions and review expectations. Please read it before opening a PR.

## Code of Conduct

This project follows the [Contributor Covenant](CODE_OF_CONDUCT.md). By participating you agree to uphold it.

## Cross-Repo Changes

Changes to Kafka wire contracts (`executions.intents.v1`, `executions.fills.v1`) require a coordinated PR pair in both huginn and sleipnir. Open both PRs as drafts, cross-link them, and request review together.

## Getting Started

```bash
git clone https://github.com/lgreene03/norse-stack.git
cd norse-stack
./scripts/clone-all.sh
docker compose up -d
./scripts/smoke.sh
```

## Style

- Follow each repo's existing conventions.
- Keep commits focused: one logical change per commit.
- Write commit messages in imperative mood ("Add feature", not "Added feature").

## Issues

Use the individual service repos for bug reports and feature requests. Use this repo for stack-level issues (compose, cross-service integration, documentation).

## License

By contributing you agree that your contributions will be licensed under the [Apache License 2.0](LICENSE).
