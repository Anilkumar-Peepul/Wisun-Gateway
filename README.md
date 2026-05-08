# Wisun-Gateway
Wi-SUN Border Router with CoAP + EMQX Cloud+ Offline Date Logger
gateway/
│
├── app.py
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── README.md
│
├── config/
│   ├── config.json
│   ├── gateway.json
│   └── wsbrd.conf
│
├── certs/
│   ├── stage_ca.pem
│   └── live_ca.pem
│
├── scripts/
│   ├── start_wsbrd.sh
│   ├── install.sh
│   ├── setup_services.sh
│   └── health_check.sh
│
├── services/
│   └── wsbrd.service
│
├── data/
│   ├── .gitkeep
│   └── offline_logs/
│
├── logs/
│   └── .gitkeep
│
└── docs/
    └── architecture.md
