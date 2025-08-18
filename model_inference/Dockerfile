FROM python:3.11-slim
WORKDIR /app
COPY model_inference/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY model_inference /app/model_inference
RUN useradd -m -u 10001 appuser && chown -R appuser:appuser /app
USER appuser
ENV PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["python","model_inference/app.py"]
