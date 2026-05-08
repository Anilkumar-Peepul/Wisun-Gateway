#!/bin/bash

set -e

BASE_DIR="/home/peepul/Wisun-Gateway"

echo "[INFO] Generating wsbrd.conf..."

python3 $BASE_DIR/scripts/generate_wsbrd_config.py

UART_PORT=$(python3 -c "
import json
with open('$BASE_DIR/config/gateway.json') as f:
    print(json.load(f)['uart_port'])
")

echo "[INFO] UART PORT: $UART_PORT"

echo "[INFO] Starting wsbrd..."

sudo wsbrd \
-F $BASE_DIR/config/wsbrd.conf \
-u $UART_PORT \
-D
