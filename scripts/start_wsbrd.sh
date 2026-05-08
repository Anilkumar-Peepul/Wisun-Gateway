#!/bin/bash

set -e

BASE_DIR="/home/pi/gateway"

echo "[INFO] Generating wsbrd.conf..."

python3 $BASE_DIR/scripts/generate_wsbrd_config.py

echo "[INFO] Starting wsbrd..."

wsbrd \
-F $BASE_DIR/config/wsbrd.conf \
-D