# Lightweight, production-friendly image
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    RAW_DIR=/shared/data/raw \
    CLEAN_DIR=/shared/data/clean \
    MODEL_DIR=/shared/models

# System deps (ca-certificates for HTTPS; tini for signal handling)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates tini && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first (cache-friendly)
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# Copy service code
COPY service.py ./service.py

# Create a non-root user
RUN useradd -m appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# Use tini as entrypoint for better signal handling
ENTRYPOINT ["/usr/bin/tini", "--"]

# Launch the API
CMD ["uvicorn", "service:app", "--host", "0.0.0.0", "--port", "8000"]
