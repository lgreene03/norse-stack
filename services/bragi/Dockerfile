FROM python:3.12-slim
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
WORKDIR /app
COPY bragi.py /app/bragi.py
# Drop root: run as an unprivileged user (sec-supplychain-2). The app needs no
# write access to /app at runtime, so a system user owning the code is enough.
RUN useradd --system --no-create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser
CMD ["python3", "-u", "bragi.py"]
