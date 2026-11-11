# Pinned by digest: the same python:3.12-slim the stack already has locally, so a
# rebuild is reproducible. Bump it deliberately (Dependabot's docker entry for this
# directory opens the pull request).
FROM python:3.12-slim@sha256:d764629ce0ddd8c71fd371e9901efb324a95789d2315a47db7e4d27e78f1b0e9
# Standard library only: nothing to pip install.
ENV PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY audit.py s3client.py recording_audit.py /app/
# Drop root. The code is read-only at runtime; reports go to the /reports mount.
RUN useradd --system --no-create-home --uid 10001 appuser \
    && mkdir -p /reports \
    && chown appuser:appuser /reports
USER appuser
EXPOSE 8098
CMD ["python3", "-u", "recording_audit.py"]
