FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    build-essential \
    pkg-config \
    libssl-dev \
    libffi-dev \
    libgirepository1.0-dev \
    gir1.2-glib-2.0 \
    python3-gi \
    python3-gi-cairo \
    libcairo2-dev \
    net-tools \
    iputils-ping \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY . .

# Create runtime folders
RUN mkdir -p logs data/offline_logs

# Start application
CMD ["python", "app.py"]