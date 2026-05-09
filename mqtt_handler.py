# wisun_gateway/mqtt_handler.py
import asyncio
import json
import ssl
import re
import paho.mqtt.client as mqtt
from datetime import datetime
from config import *
from node import Node
from storage import Storage
from logger import PayloadLogger
from constants import *

class MQTTHandler:
    def __init__(self, storage: Storage, logger: PayloadLogger):
        self.storage = storage
        self.logger = logger
        self.gateway_name = GATEWAY_NAME
        self.is_connected = False
        self.loop = asyncio.get_event_loop()

        self.client = mqtt.Client(client_id=self.gateway_name)
        self.client.username_pw_set(MQTT_USER, MQTT_PASS)
        self.client.tls_set(ca_certs=CA_CERT, tls_version=ssl.PROTOCOL_TLSv1_2)

        self.client.on_connect = self.on_connect
        self.client.on_disconnect = self.on_disconnect
        self.client.on_message = self.on_message

    def on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            print("✅ MQTT Connected")
            self.is_connected = True
            self.subscribe_topics()
        else:
            print(f"❌ MQTT Failed: {rc}")

    def on_disconnect(self, client, userdata, rc):
        print("⚠️ MQTT Disconnected")
        self.is_connected = False

    def subscribe_topics(self):
        base = f"gateways/{self.gateway_name}/devices"
        topics = [
            (f"{base}", 0),
            (f"{base}/motor_control", 0),
            (f"{base}/config", 0),
            (f"{base}/motor/config", 0),
            (f"{base}/mode_change", 0),
        ]
        self.client.subscribe(topics)

    def on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode('utf-8'))
        except:
            return

        topic = msg.topic

        if "motor_control" in topic:
            self.loop.create_task(self.handle_motor_control(payload))
        elif "mode_change" in topic:
            self.loop.create_task(self.handle_mode_change(payload))
        elif "config" in topic and "motor" not in topic:
            self.loop.create_task(self.handle_device_config(payload))
        elif "motor/config" in topic:
            self.handle_motor_register(payload)

    # ==================== Full Motor Control ====================
    async def handle_motor_control(self, message):
        dev_list = []
        dev_err_list = []

        for device in message.get("dev", []):
            d_id = device.get("d_id")
            mtr_1 = device.get("mtr_1")
            mtr_2 = device.get("mtr_2")

            if not d_id:
                dev_err_list.append({"d_id": "N/A", "mtr_1": 8, "mtr_2": 8})
                continue

            ip = self.storage.mac_to_ip.get(d_id) or d_id

            if not ip:
                dev_err_list.append({"d_id": d_id, "mtr_1": 8, "mtr_2": 8})
                continue

            mc_payload = {}
            if mtr_1 is not None: mc_payload["mtr_1"] = mtr_1
            if mtr_2 is not None: mc_payload["mtr_2"] = mtr_2

            result = await Node(ip, "motor_control", json.dumps(mc_payload)).put()

            ack = {"d_id": d_id}
            if result is None:
                ack.update({"mtr_1": 10, "mtr_2": 10})
                dev_err_list.append(ack)
            else:
                ack.update({
                    "mtr_1": result.get("mtr_1", mtr_1),
                    "mtr_2": result.get("mtr_2", mtr_2)
                })
                dev_list.append(ack)

        if dev_list:
            self.publish(f"gateways/{self.gateway_name}/devices/motor_control/ack", {"dev": dev_list})
        if dev_err_list:
            self.publish(f"gateways/{self.gateway_name}/devices/motor_control/ack", {"dev": dev_err_list})

    # ==================== Full Mode Change ====================
    async def handle_mode_change(self, message):
        dev_list = []
        dev_err_list = []

        for device in message.get("dev", []):
            d_id = device.get("d_id")
            mtr_1 = device.get("mtr_1")
            mtr_2 = device.get("mtr_2")

            ip = self.storage.mac_to_ip.get(d_id) or d_id
            if not ip:
                dev_err_list.append({"d_id": d_id, "mtr_1": 8, "mtr_2": 8})
                continue

            send_payload = {}
            if mtr_1 is not None:
                send_payload["mtr_1"] = 0 if mtr_1 == 2 else 1
            if mtr_2 is not None:
                send_payload["mtr_2"] = 0 if mtr_2 == 2 else 1

            result = await Node(ip, "cm_change", json.dumps(send_payload)).put()

            ack = {"d_id": d_id}
            if result is None:
                ack.update({"mtr_1": 10, "mtr_2": 10})
                dev_err_list.append(ack)
            else:
                # Convert back 0→2, 1→3
                ack["mtr_1"] = 2 if result.get("mtr_1") == 0 else 3 if result.get("mtr_1") == 1 else mtr_1
                ack["mtr_2"] = 2 if result.get("mtr_2") == 0 else 3 if result.get("mtr_2") == 1 else mtr_2
                dev_list.append(ack)

        if dev_list:
            self.publish(f"gateways/{self.gateway_name}/devices/mode_change/ack", {"dev": dev_list})
        if dev_err_list:
            self.publish(f"gateways/{self.gateway_name}/devices/mode_change/ack", {"dev": dev_err_list})

    def handle_motor_register(self, msg):
        d_id = msg.get("d_id")
        ip = self.storage.mac_to_ip.get(d_id)
        if ip:
            self.storage.register_device(d_id, ip)

    async def handle_device_config(self, msg):
        # You can expand this later
        pass

    def publish(self, topic: str, payload: dict):
        if not self.is_connected:
            return
        try:
            payload["t_s"] = int(datetime.utcnow().timestamp() * 1000)
            self.client.publish(topic, json.dumps(payload), qos=0)
        except Exception as e:
            print(f"Publish error: {e}")

    def connect(self):
        self.client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
        self.client.loop_start()
