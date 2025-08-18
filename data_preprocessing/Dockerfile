
FROM python:3.11-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends tini && rm -rf /var/lib/apt/lists/*
RUN useradd -m -u 10001 appuser
COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt
# COPY data_preprocessing /app/data_preprocessing
COPY . /app
RUN mkdir -p /app/shared && chown -R appuser:appuser /app
USER appuser
ENV PYTHONUNBUFFERED=1
EXPOSE 8000
ENTRYPOINT ["tini","--"]
CMD ["python","data_preprocessing/service.py"]