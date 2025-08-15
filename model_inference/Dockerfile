# # Use Python base image
# FROM python:3.11-slim

# # Set working directory
# WORKDIR /app

# # Copy requirements first for Docker caching
# COPY requirements.txt .

# # Install Python dependencies
# RUN pip install --upgrade pip && pip install -r requirements.txt

# # Copy rest of app code
# COPY . .

# # Expose Flask port
# EXPOSE 8000

# # Run the Flask app
# CMD ["python", "app.py"]

FROM python:3.11-slim
WORKDIR /app
COPY model_inference/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY model_inference /app/model_inference
RUN useradd -m appuser && chown -R appuser:appuser /app
USER appuser
ENV PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["python","model_inference/app.py"]

