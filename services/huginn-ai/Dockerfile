FROM python:3.12-slim
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY huginn_ai.py /app/huginn_ai.py
WORKDIR /app
CMD ["python3", "-u", "huginn_ai.py"]
