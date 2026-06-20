FROM python:3.12-slim
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY odin.py /app/odin.py
WORKDIR /app
CMD ["python3", "-u", "odin.py"]
