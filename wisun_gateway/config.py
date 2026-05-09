from pathlib import Path
import json

BASE_DIR = Path(__file__).resolve().parent.parent

LOG_DIR = BASE_DIR / "logs"
DATA_DIR = BASE_DIR / "data"

LOG_DIR.mkdir(exist_ok=True)
DATA_DIR.mkdir(exist_ok=True)

# Files
COMBINED_LOG_FILE = LOG_DIR / "combined_log.csv"
IP_MAC_CSV = DATA_DIR / "ip_mac_mappings.csv"
REGISTERED_DEVICES_FILE = DATA_DIR / "registered_devices.txt"

# Load configs
with open(BASE_DIR / "config/gateway.json") as f:
    gateway_config = json.load(f)

with open(BASE_DIR / "config/config.json") as f:
    broker_config = json.load(f)

ENV = gateway_config.get("environment", "staging")
mqtt_cfg = broker_config[ENV]

GATEWAY_NAME = gateway_config["gateway_name"]
MQTT_BROKER = mqtt_cfg["broker"]
MQTT_PORT = mqtt_cfg["port"]
MQTT_USER = mqtt_cfg["username"]
MQTT_PASS = mqtt_cfg["password"]
CA_CERT = mqtt_cfg["ca_cert"]