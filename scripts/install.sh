#!/bin/bash

set -e

echo "[INFO] Updating packages..."

sudo apt update

echo "[INFO] Installing dependencies..."

sudo apt install -y \
docker.io \
docker-compose \
git \
python3 \
python3-pip \
python3-venv \
network-manager \
ppp \
screen \
curl

echo "[INFO] Enabling Docker..."

sudo systemctl enable docker
sudo systemctl start docker

echo "[INFO] Copying wsbrd service..."

sudo cp services/wsbrd.service /etc/systemd/system/

echo "[INFO] Reloading systemd..."

sudo systemctl daemon-reload

echo "[INFO] Enabling wsbrd service..."

sudo systemctl enable wsbrd

echo "[INFO] Creating runtime folders..."

mkdir -p logs
mkdir -p data/offline_logs

echo "[INFO] Installation completed successfully"