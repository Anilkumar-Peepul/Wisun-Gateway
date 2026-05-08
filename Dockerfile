FROM python:3.11-slim

WORKDIR /app

# Install system dependencies (including those needed for pydbus / gi)
RUN apt-get update && apt-get install -y \
    libssl-dev \
    libffi-dev \
    build-essential \
    libgirepository1.0-dev \
    gir1.2-glib-2.0 \
    python3-gi \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first (better layer caching)
COPY requirements.txt .

# Create virtual environment and install Python packages
RUN python -m venv env && \
    env/bin/pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Run the app
CMD ["env/bin/python", "app.py"]
