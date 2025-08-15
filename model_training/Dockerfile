# FROM python:3.11-slim

# # Set workdir
# WORKDIR /app

# # Install system dependencies (for matplotlib, etc.)
# RUN apt-get update && \
#     apt-get install -y --no-install-recommends \
#         gcc \
#         build-essential \
#         libgl1-mesa-glx \
#         && rm -rf /var/lib/apt/lists/*

# # Copy requirements.txt first for better Docker caching
# COPY requirements.txt .

# # Install Python dependencies
# RUN pip install --upgrade pip && pip install -r requirements.txt

# # Copy the rest of your app code
# COPY . .

# # Expose the port Flask runs on
# EXPOSE 8000

# # Ensure /mnt/data and /mnt/models directories exist for volume mounting
# RUN mkdir -p /mnt/data /mnt/models

# # Start the Flask server
# CMD ["python", "app.py"]
FROM python:3.11-slim

# System deps for scientific libs (small but helpful)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy and install deps first for cache efficiency
COPY model_training/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy code (training + preprocessing)
COPY model_training /app/model_training
COPY data_preprocessing /app/data_preprocessing

# Create the shared directory structure your app expects
RUN mkdir -p /app/shared/data /app/shared/clean /app/shared/models

# Non-root for better security
RUN useradd -m appuser && chown -R appuser:appuser /app
USER appuser

ENV PYTHONUNBUFFERED=1
EXPOSE 8000

# Default server mode; CronJob/Job will override with --mode train
CMD ["python", "model_training/app.py", "--mode", "server"]