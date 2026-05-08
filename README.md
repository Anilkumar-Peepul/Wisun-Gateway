# Wi-SUN Gateway

Raspberry Pi + EFR32 Wi-SUN Border Router + MQTT + CoAP Gateway

---

## Features

- Wi-SUN Border Router
- MQTT TLS Communication
- CoAP Device Monitoring
- Offline Data Logging
- Dockerized Deployment
- LTE/WiFi Connectivity

---

## Folder Structure

```text
gateway/
├── app.py
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── config/
├── certs/
├── scripts/
├── services/
├── logs/
└── data/
```

---

## Run

### Build Container

```bash
docker compose build
```

### Start

```bash
docker compose up -d
```

### Logs

```bash
docker logs -f wisun_gateway
```

---

## Wi-SUN

UART Port:

```text
/dev/ttyAMA0
```

Configured in:

```text
config/gateway.json
```

---

## Services

- wsbrd.service
- gateway.service

---

## MQTT

Supports:

- EMQX
- TLS
- Offline Recovery
