FROM python:3.11-slim

WORKDIR /app
ENV HOME=/app 
RUN mkdir -p /app/.streamlit
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app

# Streamlit runs on 8501 by default
EXPOSE 8501
ENV PYTHONUNBUFFERED=1

# These can be overridden by k8s env vars:
ENV PREPROC_URL="http://data-preprocessing:8000"
ENV TRAIN_URL="http://model-training:8000"
ENV INFER_URL="http://model-inference:8000"
ENV RAW_DIR="/shared/data/raw" CLEAN_DIR="/shared/data/clean" MODEL_DIR="/shared/models"

CMD ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0"]

# --server.address=0.0.0.0: so the app listens on all interfaces inside the container, allowing the Pod/Service to route traffic to it.
# ENV PYTHONUNBUFFERED=1: To see logs immediately with kubectl logs, which helps with debugging and readiness during demos.