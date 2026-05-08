import json
from pathlib import Path

BASE_DIR = Path("/home/pi/Wisun-Gateway")

gateway_json = BASE_DIR / "config/gateway.json"
template_file = BASE_DIR / "config/wsbrd.conf.template"
output_file = BASE_DIR / "config/wsbrd.conf"

with open(gateway_json) as f:
    gateway = json.load(f)

network_name = gateway["network_name"]
uart_port = gateway["uart_port"]

with open(template_file) as f:
    template = f.read()

template = template.replace("{{NETWORK_NAME}}", network_name)
template = template.replace("{{UART_PORT}}", uart_port)

with open(output_file, "w") as f:
    f.write(template)

print("[INFO] wsbrd.conf generated successfully")
