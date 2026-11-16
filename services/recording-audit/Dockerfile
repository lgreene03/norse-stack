# Pinned by digest: the same python:3.12-slim the stack already has locally, so a
# rebuild is reproducible. Bump it deliberately (Dependabot's docker entry for this
# directory opens the pull request).
FROM python:3.15-slim@sha256:65a65fc7b30b06cb6df4930fd4650697cb23f4c2698ab81fc87e4b031bc36928
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
