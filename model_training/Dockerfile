FROM python:3.11-slim

# System deps for scientific libs (small but helpful)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy and install deps first for cache efficiency
# COPY model_training/requirements.txt /app/requirements.txt
# COPY requirements.txt .
# RUN pip install --no-cache-dir -r /app/requirements.txt

# # Copy code (training + preprocessing)
# COPY . /app/model_training
# # COPY data_preprocessing /app/data_preprocessing

# # Create the shared directory structure your app expects
# RUN mkdir -p /app/shared/data /app/shared/clean /app/shared/models

COPY requirements.txt ./

RUN python -m pip install --no-cache-dir -r requirements.txt
#COPY model_training /app/model_training
COPY . /app
RUN mkdir -p /app/shared

# Non-root for better security (create user before chown)
RUN useradd -m -u 10001 appuser && chown -R appuser:appuser /app
USER appuser

ENV PYTHONUNBUFFERED=1
EXPOSE 8000

# Default server mode; CronJob/Job will override with --mode train
CMD ["python", "model_training/app.py", "--mode", "server"]