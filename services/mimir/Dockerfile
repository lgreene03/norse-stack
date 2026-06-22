FROM python:3.12-slim
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
WORKDIR /app
COPY mimir.py /app/mimir.py
# Drop root: run as an unprivileged user (sec-supplychain-2). Mimir writes the
# SQLite DB under /app/data at runtime, so the data dir is created and owned by
# the unprivileged user (the code itself stays read-only).
RUN useradd --system --no-create-home --uid 10001 appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app
USER appuser
EXPOSE 8095
CMD ["python3", "-u", "mimir.py"]
