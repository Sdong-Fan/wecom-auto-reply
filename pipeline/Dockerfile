FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg gcc libc6-dev && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY pipeline/ ./pipeline/
COPY rag/ ./rag/

ENV PYTHONPATH=/app

ENTRYPOINT ["python", "-m", "pipeline.run_pipeline"]
