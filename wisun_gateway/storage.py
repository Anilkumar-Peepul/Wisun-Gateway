import csv
from pathlib import Path
from .config import IP_MAC_CSV, REGISTERED_DEVICES_FILE

class Storage:
    def __init__(self):
        self.mac_to_ip = {}
        self.ip_to_mac = {}
        self.registered_mac = set()

    def load_ip_mac(self):
        if not IP_MAC_CSV.exists():
            return
        with open(IP_MAC_CSV, newline='') as f:
            for row in csv.DictReader(f):
                mac = row['MAC'].upper()
                ip = row['IP']
                self.mac_to_ip[mac] = ip
                self.ip_to_mac[ip] = mac

    def save_ip_mac(self, ip: str, mac: str):
        mac = mac.upper()
        if mac in self.mac_to_ip:
            return
        with open(IP_MAC_CSV, 'a', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=['IP', 'MAC'])
            if IP_MAC_CSV.stat().st_size == 0:
                writer.writeheader()
            writer.writerow({'IP': ip, 'MAC': mac})
        self.mac_to_ip[mac] = ip
        self.ip_to_mac[ip] = mac

    def register_device(self, mac: str, ip: str):
        self.registered_mac.add(mac)
        with open(REGISTERED_DEVICES_FILE, 'a', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([mac, ip])