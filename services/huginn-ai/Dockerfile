FROM python:3.12-slim
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
WORKDIR /app
COPY huginn_ai.py /app/huginn_ai.py
# Drop root: run as an unprivileged user (sec-supplychain-2). huginn-ai persists
# its model under MODEL_DIR (default /data/huginn-ai). Pre-create that dir and
# hand both it and /app to the runtime user so model save/load keeps working
# unprivileged (otherwise os.makedirs under root-owned /data would fail and the
# HuginnAiModelPersistFailures alert would fire).
RUN useradd --system --no-create-home --uid 10001 appuser \
    && mkdir -p /data/huginn-ai \
    && chown -R appuser:appuser /app /data/huginn-ai
USER appuser
CMD ["python3", "-u", "huginn_ai.py"]
