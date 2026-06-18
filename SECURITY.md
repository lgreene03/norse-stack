# Security Policy

## Reporting a Vulnerability

If you believe you have found a security vulnerability in any Norse Stack service, please report it privately using GitHub's private vulnerability reporting on the affected service's repository. For stack-level issues (Docker Compose configuration, port exposure), use this repository's advisory form.

Email the maintainer listed in the GitHub profile with subject line `[norse-stack-security]` as an alternative.

Please include:

- A description of the issue
- Steps to reproduce
- The affected service and version (git SHA or tag)
- The impact you believe it has

You will receive an acknowledgement within 7 days.

## Important Notes

Norse Stack is research infrastructure, not a production trading system. It is designed for local-first development on a single machine. If you deploy it beyond localhost:

- Put an authenticated reverse proxy in front of all API endpoints
- Do not expose Redpanda, PostgreSQL, MinIO, or Grafana ports to the public internet
- Store exchange credentials in environment variables or a secret manager, never in committed files
- See each service's SECURITY.md for service-specific hardening guidance

## Supported Versions

All services are pre-1.0. Only main branches are currently supported.
