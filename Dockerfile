FROM python:3.11-slim

WORKDIR /app
COPY . /app

RUN apt-get update && apt-get install -y \
    libssl-dev libffi-dev build-essential \
    && rm -rf /var/lib/apt/lists/*

# Create virtual environment
RUN python -m venv env

# Install requirements inside venv
RUN /app/coap_env/bin/pip install --no-cache-dir -r requirements.txt

# Run app using venv python
CMD ["/app/env/bin/python", "app.py"]
