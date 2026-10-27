# Dockerfile for ZAMPEN Layer Chicken Analytics Pipeline on Dell OptiPlex
FROM python:3.10-slim

# Avoid interactive prompts
ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install system dependencies for OpenCV and multimedia processing
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    ffmpeg \
    libsm6 \
    libxext6 \
    libgl1 \
    libglib2.0-0 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy analytics source code
COPY . /app/analytics

# Set Python path
ENV PYTHONPATH=/app

EXPOSE 8000

# Default command starts both the FastAPI server and worker daemon
CMD ["sh", "-c", "python -m analytics.api & python -m analytics.worker"]