#!/bin/bash

set -e

BASE_DIR="/home/pi/gateway"

echo "[INFO] Starting Gateway Docker Container..."

cd $BASE_DIR

docker compose up -d