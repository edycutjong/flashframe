FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install --no-cache-dir "mcp-clickhouse[chdb]"

COPY . /app

# Register package metadata so /healthz can report a real version (not to install dependencies)
RUN pip install --no-cache-dir --no-deps -e .

EXPOSE 7860
CMD uvicorn web:app --host 0.0.0.0 --port ${PORT:-7860}
