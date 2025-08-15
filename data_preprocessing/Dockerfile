# # syntax=docker/dockerfile:1
# FROM python:3.11-slim

# ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

# RUN apt-get update && apt-get install -y --no-install-recommends tini \
#   && rm -rf /var/lib/apt/lists/*

# ARG USER=appuser
# ARG UID=10001
# RUN useradd -m -u ${UID} ${USER}

# WORKDIR /app

# COPY data_preprocessing/requirements.txt .
# RUN python -m pip install --no-cache-dir -r requirements.txt

# COPY data_preprocessing/preprocessing.py ./preprocessing.py

# RUN mkdir -p /config /dataset && chown -R ${USER}:${USER} /config /dataset /app
# USER ${USER}

# # Optional but nice for K8s:
# # HEALTHCHECK CMD python -c "import os; print('ok')" || exit 1

# ENTRYPOINT ["/usr/bin/tini", "--", "python", "preprocessing.py"]
# CMD ["--config", "/config/config.yaml"]

# data_preprocessing/Dockerfile
FROM python:3.11-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends tini && rm -rf /var/lib/apt/lists/*
RUN useradd -m -u 10001 appuser
COPY data_preprocessing/requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt
COPY data_preprocessing /app/data_preprocessing
RUN mkdir -p /app/shared && chown -R appuser:appuser /app
USER appuser
ENV PYTHONUNBUFFERED=1
EXPOSE 8000
ENTRYPOINT ["tini","--"]
CMD ["python","data_preprocessing/service.py"]