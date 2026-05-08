import re
import time
import json
import paho.mqtt.client as mqtt
from aiocoap import Context, Message, GET, PUT, CON
import ssl
import os
from openpyxl import Workbook, load_workbook
from datetime import datetime
import csv
from pathlib import Path
import signal
from aiocoap.resource import Resource
from pydbus import SystemBus

# Configurations
GATEWAY_NAME = "TEST_GATEWAY"
CA_CERT_PATH = "./ca_cert_stage.crt"
MQTT_USERNAME = "ss_user"
MQTT_PASSWORD = "123456"
MQTT_BROKER = "e0be1176.ala.asia-southeast1.emqxsl.com"
MQTT_PORT = 8883
COAP_GET_TIMEOUT = 10  # seconds
COAP_PUT_TIMEOUT = 10   # seconds
DEVICE_SYNC_PERIOD = 30000  # seconds
DEVICE_LIVE_PERIOD = 10  # seconds
LOG_FILE = Path("BR_V1.0_LOGG_FILE")
IP_MAC_CSV = Path("ip_mac_mappings.csv")

# Define global variables
flag = 0
Frequency = 1
connected_nodes = set()
active_nodes = set()
macs = set()
previous_macs = set()
gateway_name = GATEWAY_NAME
mac_to_ip = {}
ip_to_mac = {}
Gateway_list = {}
reg_mac = set()
reg_ip = set()
SYNC_TIME = DEVICE_SYNC_PERIOD
LIVE_TIME = DEVICE_LIVE_PERIOD
CA_CERT = Path(CA_CERT_PATH)

class Node:
    def __init__(self, ipv6_address='', uri="", payload=''):
        self.ipv6_address = ipv6_address
        self.uri = uri
        self.payload = payload.encode('utf-8') if payload else payload

    async def async_get_Device_data(self, timeout=COAP_GET_TIMEOUT):
        try:
            uri = f"coap://[{self.ipv6_address}]:5683/{self.uri}"
            protocol = await Context.create_client_context()
            request = Message(code=GET, uri=uri)
            try:
                response = await asyncio.wait_for(protocol.request(request).response, timeout)
            except asyncio.TimeoutError:
                print(f"ERROR: Timeout: No response from node {self.ipv6_address} within {timeout} seconds.")
                return None
            output = response.payload.decode().strip()
            print("get data: {output} ")
            try:
                return json.loads(output)
            except json.JSONDecodeError:
                print(f"ERROR: Failed to parse Device data from node {self.ipv6_address}")
                return None
        except Exception as e:
            print(f"WARNING: Exception in async_get_Device_data for node {self.ipv6_address}: {e}")
            return None

    async def check_nodes_activity(self):
        global connected_nodes, active_nodes
        tasks = [Node(node, "settings/auto_send").async_get_Device_data(COAP_GET_TIMEOUT) for node in connected_nodes]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        active_nodes = {node for node, result in zip(connected_nodes, results) if result is not None}
        return active_nodes

    async def node_command(self):
        context = await Context.create_client_context()
        request = Message(
            mtype=CON,
            code=PUT,
            payload=self.payload,
            uri=f"coap://[{self.ipv6_address}]:5683/{self.uri}",
            token=os.urandom(2)
        )
        try:
            response = await asyncio.wait_for(context.request(request).response, timeout=COAP_PUT_TIMEOUT)
            reply = response.payload.decode("utf-8") if response.payload else ""
            print("reply : ",response)
            try:
                reply = json.loads(reply)
            except json.JSONDecodeError:
                print(f"ERROR: Failed to parse CoAP response from {self.ipv6_address}/{self.uri}: {reply}")
                return None
            print(f"INFO: Result for {self.ipv6_address} on {self.uri}: {response.code}\n{reply!r}")
            return reply
        except asyncio.TimeoutError:
            print(f"ERROR: Timeout: No response from {self.ipv6_address} on {self.uri} within {COAP_PUT_TIMEOUT} seconds")
            return None
        except Exception as e:
            print(f"WARNING: Exception CoAP request failed on {self.uri}: {e}")
            return None

class WiSunMonitor:
    def __init__(self):
        self.bus = SystemBus()
        self.proxy = self.bus.get("com.silabs.Wisun.BorderRouter", "/com/silabs/Wisun/BorderRouter")
        self.initial_mapping_done = False

    @staticmethod
    def slice_ipv6(source):
        return [source[i: i + 4] for i in range(0, len(source), 4)]

    @staticmethod
    def pretty_ipv6(ipv6):
        ipv6 = ":".join(WiSunMonitor.slice_ipv6(ipv6))
        ipv6 = re.sub("0000:", ":", ipv6)
        ipv6 = re.sub(":{2,}", "::", ipv6)
        return ipv6

    async def get_nodes(self):
        global connected_nodes, gateway_name
        try:
            nodes = await self.proxy.Nodes if "ipv6" in self.proxy.Nodes[0][1] else self.proxy.RoutingGraph
            result = set()
            if "ipv6" in self.proxy.Nodes[0][1]:
                for node in nodes:
                    ipv6 = bytes(node[1]["ipv6"][1]).hex()
                    result.add(self.pretty_ipv6(ipv6))
            else:
                for node in nodes[1:]:
                    ipv6 = bytes(node[0]).hex()
                    result.add(self.pretty_ipv6(ipv6))
            connected_nodes = result
            return tuple(result)
        except Exception as e:
            print(f"WARNING: Exception error fetching nodes: {e}")
            return []

    async def load_ip_mac_from_csv(self):
        global mac_to_ip, ip_to_mac, macs
        if IP_MAC_CSV.exists():
            try:
                with open(IP_MAC_CSV, mode='r', newline='') as csvfile:
                    reader = csv.DictReader(csvfile)
                    for row in reader:
                        ip = row['IP']
                        mac = row['MAC'].upper()
                        mac_to_ip[mac] = ip
                        ip_to_mac[ip] = mac
                        macs.add(mac)
                print(f"INFO: Loaded {len(mac_to_ip)} IP-MAC mappings from {IP_MAC_CSV}")
            except Exception as e:
                print(f"ERROR: Failed to load IP-MAC mappings from CSV: {e}")
        else:
            print(f"INFO: No existing CSV file found at {IP_MAC_CSV}")
            print(f"INFO: Creating {IP_MAC_CSV} CSV file")
            write_header = not IP_MAC_CSV.exists()  # Write header only if file doesn't exist
            with open(IP_MAC_CSV, mode='a', newline='') as csvfile:
                writer = csv.DictWriter(csvfile, fieldnames=['IP', 'MAC'])
                if write_header:
                    writer.writeheader()
            

    async def save_ip_mac_to_csv(self, new_mappings):
        try:
            # Check existing entries to avoid duplicates
            existing_mappings = set()
            if IP_MAC_CSV.exists():
                with open(IP_MAC_CSV, mode='r', newline='') as csvfile:
                    reader = csv.DictReader(csvfile)
                    for row in reader:
                        existing_mappings.add((row['IP'], row['MAC'].upper()))

            # Append new mappings that don't already exist
            write_header = not IP_MAC_CSV.exists()  # Write header only if file doesn't exist
            with open(IP_MAC_CSV, mode='a', newline='') as csvfile:
                writer = csv.DictWriter(csvfile, fieldnames=['IP', 'MAC'])
                if write_header:
                    writer.writeheader()
                for ip, mac in new_mappings:
                    if (ip, mac) not in existing_mappings:
                        writer.writerow({'IP': ip, 'MAC': mac})
                        print(f"INFO: Saved mapping to CSV - IP: {ip}, MAC: {mac}")
        except Exception as e:
            print(f"ERROR: Failed to save IP-MAC mappings to CSV: {e}")

    async def monitor_nodes(self, mqtt_client, logger):
        global connected_nodes, gateway_name, mac_to_ip, ip_to_mac, macs
        await self.get_nodes()
        print(f"INFO: Processing {len(connected_nodes)} nodes")
        
        # Load existing mappings from CSV
        await self.load_ip_mac_from_csv()

        # Fetch and print data for all devices, and collect new mappings
        new_mappings = []
        current_macs = set()
        if connected_nodes:
            print("\nFetching data from all devices (URI: info/motor_status):")
            tasks = [Node(ip, "info/motor_status").async_get_Device_data() for ip in connected_nodes]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for ip, data in zip(connected_nodes, results):
                if data is not None and not isinstance(data, Exception):
                    print(f"\nData from {ip}:")
                    print(json.dumps(data, indent=2))
                    topic = f"gateways/{gateway_name}/devices/live_data"
                    message = json.dumps(data)
                    mqtt_client.publish(topic, message)
                    logger.process_payload(data)
                    mac = data.get("d_id", "N/A").upper()
                    print(f"INFO: MAC: {mac} to IP: {ip}")
                    if mac != "N/A":
                        print(f"MACS : {current_macs}" )
                        if ip not in ip_to_mac or mac not in macs:  # Only add new mappings
                            mac_to_ip[mac] = ip
                            ip_to_mac[ip] = mac
                            current_macs.add(mac)
                            new_mappings.append((ip, mac))
                            print(f"INFO: New mapping - MAC: {mac} to IP: {ip}")
                    else:
                        print(f"WARNING: Invalid or missing MAC address (d_id) from {ip}: {mac}")
                else:
                    print(f"ERROR: Failed to fetch data from {ip}: {data}")
            macs = current_macs
            current_macs.clear()
        else:
            print("INFO: No connected nodes to process")

        # Save new mappings to CSV
        if new_mappings:
            await self.save_ip_mac_to_csv(new_mappings)

        # Print the current mappings
        print("\nMAC to IP Mapping:")
        if mac_to_ip:
            for mac, ip in mac_to_ip.items():
                print(f"MAC: {mac} -> IP: {ip}")
        else:
            print("No MAC to IP mappings available.")
        
        print("\nIP to MAC Mapping:")
        if ip_to_mac:
            for ip, mac in ip_to_mac.items():
                print(f"IP: {ip} -> MAC: {mac}")
        else:
            print("No IP to MAC mappings available.")

class MQTTClient:
    GATEWAY_TOPIC = "gateways/gateway_name/devices"
    GATEWAY_ACK_TOPIC = "gateways/gateway_name/devices/ack"
    DEVICE_CONFIG_TOPIC = "gateways/{}/devices/config"
    MOTOR_CONTROL_TOPIC = "gateways/{}/devices/motor_control"
    MOTOR_CONTROL_ACK_TOPIC = "gateways/{}/devices/motor_control/ack"
    DEVICE_STATUS_TOPIC = "gateways/{}/devices/live_data"
    DEVICE_SYNC_ACK_TOPIC = "gateways/{}/devices/sync/ack"
    DEVICE_CONFIG_ACK_TOPIC = "gateways/{}/devices/config/ack"
    MOTOR_CONFIG_TOPIC = "gateways/{}/devices/motor/config"
    MOTOR_MODE_CONFIG_TOPIC = "gateways/{}/devices/mode_change"
    MOTOR_MODE_CONFIG_ACK_TOPIC = "gateways/{}/devices/mode_change/ack"

    def __init__(self, broker, port, client_id, username, password, ca_cert, reg):
        self.broker = broker
        self.port = port
        self.client_id = client_id
        self.username = username
        self.password = password
        self.client = mqtt.Client(client_id)
        self.loop = asyncio.get_event_loop()
        self.client.username_pw_set(username, password)
        self.client.on_connect = self.on_connect
        self.client.on_disconnect = self.on_disconnect
        self.client.on_message = self.on_message
        self.client.on_subscribe = self.on_subscribe
        self.is_connected = False
        self.client.tls_set(ca_certs=str(ca_cert), tls_version=ssl.PROTOCOL_TLSv1_2)
        self.device_config_topic = None
        self.motor_control_topic = None
        self.motor_control_ack_topic = None
        self.device_status_topic = None
        self.device_sync_ack_topic = None
        self.device_config_ack_topic = None
        self.motor_config_topic = None
        self.motor_mode_config_topic = None
        self.motor_mode_config_ack_topic = None
        self.reg = reg

    def update_dynamic_topics(self, gateway_name):
        self.device_config_topic = self.DEVICE_CONFIG_TOPIC.format(gateway_name)
        self.motor_control_topic = self.MOTOR_CONTROL_TOPIC.format(gateway_name)
        self.motor_control_ack_topic = self.MOTOR_CONTROL_ACK_TOPIC.format(gateway_name)
        self.device_status_topic = self.DEVICE_STATUS_TOPIC.format(gateway_name)
        self.device_sync_ack_topic = self.DEVICE_SYNC_ACK_TOPIC.format(gateway_name)
        self.device_config_ack_topic = self.DEVICE_CONFIG_ACK_TOPIC.format(gateway_name)
        self.motor_config_topic = self.MOTOR_CONFIG_TOPIC.format(gateway_name)
        self.motor_mode_config_topic = self.MOTOR_MODE_CONFIG_TOPIC.format(gateway_name)
        self.motor_mode_config_ack_topic = self.MOTOR_MODE_CONFIG_ACK_TOPIC.format(gateway_name)

    def on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            print("INFO: Successfully connected to MQTT Broker with SSL!")
            self.is_connected = True
        else:
            print(f"ERROR: Failed to connect, return code {rc}")
            self.is_connected = False

    def on_disconnect(self, client, userdata, rc):
        print("ERROR: Disconnected from MQTT Broker. Reconnecting...")
        self.is_connected = False

    def on_subscribe(self, client, userdata, mid, granted_qos):
        print(f"INFO: Successfully subscribed with QoS {granted_qos}")

    def is_ipv6(self, address):
        ipv6_pattern = r'^([0-9a-fA-F]{1,4}:){1,7}([0-9a-fA-F]{1,4}|:)$|^([0-9a-fA-F]{1,4}:)*::$|^::$'
        return bool(re.match(ipv6_pattern, address)) or '::' in address

    def is_mac(self, address):
        mac_pattern = r'^([0-9A-Fa-f]{2}:){7}[0-9A-Fa-f]{2}$'
        return bool(re.match(mac_pattern, address))

    def on_message(self, client, userdata, msg):
        print(f"INFO: Received message on topic {msg.topic}: {msg.payload.decode('utf-8')}")
        try:
            message = json.loads(msg.payload.decode('utf-8'))
        except json.JSONDecodeError as e:
            print(f"ERROR: Invalid JSON in message on topic {msg.topic}: {e}")
            return
        if msg.topic == self.GATEWAY_TOPIC:
            name = message.get("gtw_n", "N/A")
            try:
                if name == gateway_name:
                    #self.publish(self.GATEWAY_ACK_TOPIC, json.dumps(Gateway_list))
                    print(f"INFO: Published gateway ack: {Gateway_list}")
                else:
                    print("INFO: Gateway name mismatched")
            except Exception as e:
                print(f"ERROR: Exception in processing GATEWAY_TOPIC: {e}")
        elif msg.topic == self.motor_control_topic:
            try:
                print("Motor Controlling Task")
                self.loop.create_task(self.handle_motor_control_message(message))
            except Exception as e:
                print(f"ERROR: Error scheduling motor control message: {e}")
                error_payload = {
                    "d_id": message.get("d_id", "N/A"),
                    "mtr_1": 8,
                    "mtr_2": 8
                }
                self.publish(self.motor_control_ack_topic, json.dumps(error_payload))
        elif msg.topic == self.motor_mode_config_topic:
            try:
                self.loop.create_task(self.handle_motor_mode_control_message(message))
            except Exception as e:
                print(f"ERROR: Error scheduling motor mode control message: {e}")
                error_payload = {
                    "d_id": message.get("d_id", "N/A"),
                    "mtr_1": 8,
                    "mtr_2": 8
                }
                self.publish(self.motor_mode_config_ack_topic, json.dumps(error_payload))
        elif msg.topic == self.motor_config_topic:
            try:
                d_id = message.get("d_id")
                if not self.is_mac(d_id):
                    print(f"ERROR: Invalid MAC address: {d_id}")
                    return
                ipv6 = mac_to_ip.get(d_id)
                if ipv6:
                    self.reg.append_device(d_id, ipv6)
                    print(f"INFO: Registered MAC {d_id} with IP {ipv6}")
                else:
                    print(f"ERROR: No IP found for MAC {d_id}")
            except Exception as e:
                print(f"ERROR: Exception in motor config processing: {e}")
        elif msg.topic == self.device_config_topic:
            try:
                d_id = message.get("d_id")
                ip = mac_to_ip.get(d_id)
                print(f"INFO: Config MAC: {d_id}, Config IP: {ip}")
                if ip:
                    self.loop.create_task(self.handle_config(ip, json.dumps(message)))
                else:
                    config_ack = {"d_id": d_id, "r": 0}
                    self.publish(self.device_config_ack_topic, json.dumps(config_ack))
            except Exception as e:
                print(f"ERROR: Exception in config processing: {e}")
                config_ack = {"sn": message.get("sn", ""), "d_id": message.get("d_id", "N/A"), "r": 0}
                self.publish(self.device_config_ack_topic, json.dumps(config_ack))

    async def handle_motor_control_message(self, message):
        dev_list = []
        dev_err_list = []
        tasks = []

        for device in message.get("dev", []):
            d_id = device.get("d_id")
            mtr_1 = device.get("mtr_1", None)
            mtr_2 = device.get("mtr_2", None)

            # ✅ Missing MAC
            if not d_id:
                error_payload = {"d_id": "N/A", "mtr_1": 8, "mtr_2": 8}
                dev_err_list.append(error_payload)
                continue

            # ✅ Resolve IP
            if self.is_ipv6(d_id) and d_id in connected_nodes:
                ip = d_id
            else:
                ip = mac_to_ip.get(d_id)

            if not ip:
                error_payload = {"d_id": d_id, "mtr_1": 8, "mtr_2": 8}
                dev_err_list.append(error_payload)
                continue
            # ✅ Validate mtr_1
            if mtr_1 is not None and (not isinstance(mtr_1, int) or mtr_1 not in [0, 1]):
                dev_err_list.append({"d_id": d_id, "mtr_1": 9})
                continue

            # ✅ Validate mtr_2
            if mtr_2 is not None and (not isinstance(mtr_2, int) or mtr_2 not in [0, 1]):
                dev_err_list.append({"d_id": d_id, "mtr_2": 9})
                continue

            # ✅ Build command payload only with available keys
            mc_payload = {}
            if mtr_1 is not None:
                mc_payload["mtr_1"] = mtr_1
            if mtr_2 is not None:
                mc_payload["mtr_2"] = mtr_2

            tasks.append((d_id, mtr_1, mtr_2,
                        Node(ip, "motor_control", json.dumps(mc_payload)).node_command()))

        # ✅ Execute all valid tasks
        if tasks:
            results = await asyncio.gather(*[task[3] for task in tasks], return_exceptions=True)

            for (d_id, mtr_1, mtr_2, _), data in zip(tasks, results):
                ack = {"d_id": d_id}

                if data is None:
                    ack["mtr_1"] = 10
                    ack["mtr_2"] = 10
                    dev_err_list.append(ack)
                    continue

                # ✅ If response is dict, use it; else use original
                if mtr_1 is not None or (isinstance(data, dict) and "mtr_1" in data):
                    ack["mtr_1"] = data.get("mtr_1", mtr_1) if isinstance(data, dict) else mtr_1

                if mtr_2 is not None or (isinstance(data, dict) and "mtr_2" in data):
                    ack["mtr_2"] = data.get("mtr_2", mtr_2) if isinstance(data, dict) else mtr_2

                dev_list.append(ack)

        # ✅ Final Publish Once
        if dev_list:
            final_payload = {"dev": dev_list}
            print(f"INFO: Motor Control ACK Payload: {json.dumps(final_payload)}")
            self.publish(self.motor_control_ack_topic, json.dumps(final_payload))

        if dev_err_list:
            err_payload = {"dev": dev_err_list}
            print(f"ERROR: Motor Control ACK Payload: {json.dumps(err_payload)}")
            self.publish(self.motor_control_ack_topic, json.dumps(err_payload))

    async def handle_motor_mode_control_message(self, message):
        dev_list = []
        dev_err_list = []
        tasks = []

        for device in message.get("dev", []):
            d_id = device.get("d_id")
            mtr_1 = device.get("mtr_1", None)
            mtr_2 = device.get("mtr_2", None)

            # ✅ 1. Missing d_id
            if not d_id:
                error_payload = {"d_id": d_id, "mtr_1": 8, "mtr_2": 8}
                dev_err_list.append(error_payload)
                continue

            # ✅ 2. Resolve IP
            if self.is_ipv6(d_id) and d_id in connected_nodes:
                ip = d_id
            else:
                ip = mac_to_ip.get(d_id)
            if not ip:
                error_payload = {"d_id": d_id, "mtr_1": 8, "mtr_2": 8}
                dev_err_list.append(error_payload)
                continue

            # ✅ 3. Validate values (allowed 2 or 3)
            if mtr_1 is not None and (not isinstance(mtr_1, int) or mtr_1 not in [2, 3]):
                error_payload = {"d_id": d_id, "mtr_1": 9}
                dev_err_list.append(error_payload)
                continue

            if mtr_2 is not None and (not isinstance(mtr_2, int) or mtr_2 not in [2, 3]):
                error_payload = {"d_id": d_id, "mtr_2": 9}
                dev_err_list.append(error_payload)
                continue

            # ✅ 4. Build payload (convert 2➡0 & 3➡1 before sending)
            mc_payload = {}
            send_mtr_1 = None
            send_mtr_2 = None

            if mtr_1 is not None:
                send_mtr_1 = 0 if mtr_1 == 2 else 1
                mc_payload["mtr_1"] = send_mtr_1

            if mtr_2 is not None:
                send_mtr_2 = 0 if mtr_2 == 2 else 1
                mc_payload["mtr_2"] = send_mtr_2

            tasks.append((d_id, mtr_1, mtr_2,
                        Node(ip, "cm_change", json.dumps(mc_payload)).node_command()))

        # ✅ Run all tasks
        results = await asyncio.gather(*[task[3] for task in tasks], return_exceptions=True)
        print("Mode CHange Result:",results)
        for (d_id, orig_m1, orig_m2, _), Data in zip(tasks, results):
            device_ack = {"d_id": d_id}

            # ✅ If timeout/exception
            if Data is None:
                device_ack["mtr_1"] = 10
                device_ack["mtr_2"] = 10
                dev_err_list.append(device_ack)
                continue

            try:
                # ✅ If dict returned → map values back 0→2, 1→3
                if isinstance(Data, dict):
                    if "mtr_1" in Data or orig_m1 is not None:
                        val1 = Data.get("mtr_1", 1 if orig_m1 == 3 else 0)
                        device_ack["mtr_1"] = 3 if val1 == 1 else 2 if val1 == 0 else val1

                    if "mtr_2" in Data or orig_m2 is not None:
                        val2 = Data.get("mtr_2", 1 if orig_m2 == 3 else 0)
                        device_ack["mtr_2"] = 3 if val2 == 1 else 2 if val2 == 0 else val2

                else:
                    # ✅ If no dict returned, fallback on orig values
                    if orig_m1 is not None:
                        device_ack["mtr_1"] = orig_m1
                    if orig_m2 is not None:
                        device_ack["mtr_2"] = orig_m2

                dev_list.append(device_ack)

            except Exception:
                device_ack["mtr_1"] = 10
                device_ack["mtr_2"] = 10
                dev_err_list.append(device_ack)

        # ✅ PUBLISH ONLY ONCE AT THE END

        if dev_list:
            final_payload = {"dev": dev_list}
            self.publish(self.motor_mode_config_ack_topic, json.dumps(final_payload))

        if dev_err_list:
            err_payload = {"dev": dev_err_list}
            self.publish(self.motor_mode_config_ack_topic, json.dumps(err_payload))


    async def handle_sync_device(self, ip, d_id):
        try:
            data = await Node(ip, "info/motor_status", "").node_command()
            if data is None:
                print(f"ERROR: No data for {ip}")
                return
            ack_payload = json.dumps(data)
            print(f"INFO: Sync data for {ip}: {data}")
            self.publish(self.device_sync_ack_topic, ack_payload)
        except Exception as e:
            print(f"ERROR: Error during sync for {ip}: {e}")

    async def handle_config(self, ip, message):
        sn = ""
        d_id = "N/A"

        try:
            print(f"INFO: Handling config request for {ip}")

            # ✅ Safe JSON parsing
            try:
                message_dict = json.loads(message)
                sn = message_dict.get("sn", "")
                d_id = message_dict.get("d_id", "N/A")
            except Exception as e:
                print(f"ERROR: Invalid JSON message: {e}")
                raise

            # =========================
            # STEP 1: Trigger UPDATE
            # =========================
            try:
                state = await asyncio.wait_for(
                    Node(ip, "config", message).node_command(),
                    timeout=COAP_PUT_TIMEOUT
                )
            except asyncio.TimeoutError:
                print(f"ERROR: Timeout during UPDATE_STARTED request for {ip}")
                raise

            if not (state and isinstance(state, dict) and state.get("config_sts") == "UPDATE_STARTED"):
                print(f"ERROR: UPDATE NOT STARTED for {ip}, response: {state}")

                config_ack = {"sn": sn, "d_id": d_id, "r": 0}
                self.publish(self.device_config_ack_topic, json.dumps(config_ack))
                return

            # =========================
            # STEP 2: Get final status
            # =========================
            await asyncio.sleep(5)
            try:                
                data = await asyncio.wait_for(
                    Node(ip, "config").node_command(),
                    timeout=COAP_PUT_TIMEOUT
                )
            except asyncio.TimeoutError:
                print(f"ERROR: Timeout during final config fetch for {ip}")
                raise

            print(f"DEBUG: Final config response from {ip}: {data}")

            error_statuses = {
                "PARSE_FAILED", "MAC_MISMATCH", "VERIFY_FAILED",
                "ERASE_FAILED", "WRITE_FAILED", "UPDATE_PENDING",
                "PAYLOAD_TOO_LARGE"
            }

            # =========================
            # STEP 3: Validate response
            # =========================
            if not isinstance(data, dict):
                print(f"ERROR: Invalid response format from {ip}")
                raise ValueError("Invalid response format")

            config_status = data.get("config_sts", "")

            if config_status in error_statuses:
                print(f"ERROR: Config FAILED for {ip}, status: {config_status}")

                config_ack = {"sn": sn, "d_id": d_id, "r": 0}

            elif "d_id" in data:
                print(f"INFO: Config SUCCESS for {ip}, confirmed by d_id")

                config_ack = {
                    "sn": sn,
                    "d_id": data.get("d_id", d_id),
                    "r": 1
                }

            else:
                print(f"ERROR: Missing d_id in response for {ip}")

                config_ack = {"sn": sn, "d_id": d_id, "r": 0}

            # ✅ Final publish
            self.publish(self.device_config_ack_topic, json.dumps(config_ack))

        # =========================
        # GLOBAL EXCEPTION HANDLER
        # =========================
        except asyncio.TimeoutError:
            print(f"ERROR: Timeout occurred for device {ip}")

            config_ack = {"sn": sn, "d_id": d_id, "r": 0}
            self.publish(self.device_config_ack_topic, json.dumps(config_ack))

        except ValueError as ve:
            print(f"ERROR: ValueError for {ip}: {ve}")

            config_ack = {"sn": sn, "d_id": d_id, "r": 0}
            self.publish(self.device_config_ack_topic, json.dumps(config_ack))

        except Exception as e:
            print(f"ERROR: Unexpected error during config for {ip}: {e}")

            config_ack = {"sn": sn, "d_id": d_id, "r": 0}
            self.publish(self.device_config_ack_topic, json.dumps(config_ack))

    def connect(self):
        try:
            self.client.connect(self.broker, self.port, keepalive=100)
            self.client.loop_start()
            print("INFO: Attempting to connect to MQTT broker with SSL...")
        except Exception as e:
            print(f"ERROR: Connection failed: {e}")
            raise

    async def reconnect_async(self):
        while not self.is_connected:
            try:
                self.client.connect(self.broker, self.port, keepalive=100)
                self.client.loop_start()
                print("INFO: Attempting to reconnect to MQTT broker with SSL...")
                await asyncio.sleep(2)
            except Exception as e:
                print(f"WARNING: Reconnection failed: {e}. Retrying in 5 seconds...")
                await asyncio.sleep(5)

    def publish(self, topic, payload):
        if self.is_connected:
            if isinstance(payload, bytes):
                try:
                    payload = payload.decode('utf-8')
                except Exception as e:
                    print(f"WARNING: Error decoding payload bytes: {e}")
                    return
            try:
                parsed_payload = json.loads(payload)
                date = datetime.utcnow() - datetime(1970, 1, 1)
                milliseconds = round(date.total_seconds() * 1000)
                parsed_payload.update({"t_s": milliseconds})
                payload_str = json.dumps(parsed_payload)
                self.client.publish(topic, payload_str, qos=0, retain=False)
                print(f"INFO: Published to {topic}: {payload_str}")
            except json.JSONDecodeError:
                self.client.publish(topic, payload, qos=0)
                print(f"INFO: Published raw non-JSON payload to {topic}: {payload}")
        else:
            print("ERROR: Client not connected, cannot publish.")

class PayloadAnomalyLogger:
    def __init__(self):
        self.pfa = 400
        self.lva = 410
        self.lvf = 405
        self.hva = 450
        self.hvf = 455
        self.vif = 20
        self.via = 15
        self.m_f_dr = 0.2
        self.m_f_ol = 0.3
        self.m_f_ci = 0.2
        self.m_a_dr = 0.3
        self.m_a_ol = 0.4
        self.m_a_ci = 0.15
        self.combined_log_file = Path("combined_log.csv")

    def max_diff(self, arr):
        return max(arr) - min(arr) if arr else 0

    def avg_currents(self, c):
        return sum(c) / len(c) if c else 0

    def log_payload(self, payload):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        data = {
            "timestamp": timestamp,
            "d_id": payload.get("d_id", ""),
            "p_v": payload.get("p_v", 0),
            "pwr": payload.get("pwr", 0),
            "mode": payload.get("mode", 0),
            "ll_v_1": payload.get("ll_v", [0, 0, 0])[0],
            "ll_v_2": payload.get("ll_v", [0, 0, 0])[1],
            "ll_v_3": payload.get("ll_v", [0, 0, 0])[2],
        }
        for motor in payload.get("mtr", []):
            mtr_id = motor.get("mtr_id", 0)
            prefix = f"mtr{mtr_id}_"
            data.update({
                f"{prefix}amp_1": motor.get("amp", [0, 0, 0])[0],
                f"{prefix}amp_2": motor.get("amp", [0, 0, 0])[1],
                f"{prefix}amp_3": motor.get("amp", [0, 0, 0])[2],
                f"{prefix}mtr_sts": motor.get("mtr_sts", 0),
                f"{prefix}flt": motor.get("flt", 0),
                f"{prefix}alt": motor.get("alt", 0),
                f"{prefix}l_on": motor.get("l_on", 0),
                f"{prefix}l_of": motor.get("l_of", 0),
            })
        return data

    def check_voltage_anomalies(self, ll_v):
        return {
            "In_Ph_Fl_Amly": any(v < self.pfa for v in ll_v),
            "Lo_V_Fl_Amly": any(v < self.lvf for v in ll_v),
            "Hi_V_Fl_Amly": any(v > self.hvf for v in ll_v),
            "V_Imb_Fl_Amly": self.max_diff(ll_v) > self.vif,
            "Ph_Fl_Al_Amly": any(v < self.pfa for v in ll_v),
            "Lo_V_Al_Amly": any(v < self.lva for v in ll_v),
            "Hi_V_Al_Amly": any(v > self.hva for v in ll_v),
            "V_Imb_Al_Amly": self.max_diff(ll_v) > self.via
        }

    def check_motor_anomalies(self, motor_c, mtr_id):
        avg = self.avg_currents(motor_c)
        return {
            f"Dry_M{mtr_id}_Fl_Amly": avg < self.m_f_dr,
            f"Ov_Ld_M{mtr_id}_Fl_Amly": avg > self.m_f_ol or max(motor_c) > self.m_f_ol,
            f"C_Imb_M{mtr_id}_Fl_Amly": self.max_diff(motor_c) > self.m_f_ci,
            f"Dry_M{mtr_id}_Al_Amly": avg < self.m_a_dr,
            f"Ov_Ld_M{mtr_id}_Al_Amly": avg > self.m_a_ol or max(motor_c) > self.m_a_ol,
            f"C_Imb_M{mtr_id}_Al_Amly": self.max_diff(motor_c) > self.m_a_ci
        }

    def log_combined(self, payload_data, anomaly_data):
        combined_data = {**payload_data, **anomaly_data}
        headers = [
            "timestamp", "d_id", "p_v", "pwr", "mode",
            "ll_v_1", "ll_v_2", "ll_v_3",
            "mtr1_amp_1", "mtr1_amp_2", "mtr1_amp_3", "mtr1_mtr_sts",
            "mtr1_flt", "mtr1_alt", "mtr1_l_on", "mtr1_l_of",
            "mtr2_amp_1", "mtr2_amp_2", "mtr2_amp_3", "mtr2_mtr_sts",
            "mtr2_flt", "mtr2_alt", "mtr2_l_on", "mtr2_l_of",
            "In_Ph_Fl_Amly", "Lo_V_Fl_Amly", "Hi_V_Fl_Amly", "V_Imb_Fl_Amly",
            "Ph_Fl_Al_Amly", "Lo_V_Al_Amly", "Hi_V_Al_Amly", "V_Imb_Al_Amly",
            "Dry_M1_Fl_Amly", "Ov_Ld_M1_Fl_Amly", "C_Imb_M1_Fl_Amly",
            "Dry_M1_Al_Amly", "Ov_Ld_M1_Al_Amly", "C_Imb_M1_Al_Amly",
            "Dry_M2_Fl_Amly", "Ov_Ld_M2_Fl_Amly", "C_Imb_M2_Fl_Amly",
            "Dry_M2_Al_Amly", "Ov_Ld_M2_Al_Amly", "C_Imb_M2_Al_Amly"
        ]
        file_exists = self.combined_log_file.exists()
        with open(self.combined_log_file, mode="a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            if not file_exists:
                writer.writeheader()
            writer.writerow(combined_data)

    def process_payload(self, payload):
        payload_data = self.log_payload(payload)
        anomaly_data = {"timestamp": payload_data["timestamp"], "d_id": payload_data["d_id"]}
        ll_v = payload.get("ll_v", [0, 0, 0])
        anomaly_data.update(self.check_voltage_anomalies(ll_v))
        for motor in payload.get("mtr", []):
            mtr_id = motor.get("mtr_id", 0)
            motor_c = motor.get("amp", [0, 0, 0])
            anomaly_data.update(self.check_motor_anomalies(motor_c, mtr_id))
        self.log_combined(payload_data, anomaly_data)

class RegisteredIPManager:
    def __init__(self, file_path="registered_devices.txt"):
        self.file_path = Path(file_path)
        self._initialize_file()
        self.load_devices_to_reg_sets()

    def _initialize_file(self):
        if not self.file_path.exists():
            with open(self.file_path, 'w') as f:
                f.write("mac,ip\n")

    def append_device(self, mac, ip):
        global reg_mac, reg_ip
        try:
            existing_devices = self.fetch_all_devices()
            if mac not in existing_devices or existing_devices.get(mac) != ip:
                with open(self.file_path, 'a', newline='') as f:
                    writer = csv.writer(f)
                    writer.writerow([mac, ip])
                reg_mac.add(mac)
                reg_ip.add(ip)
                print(f"INFO: Appended MAC {mac} and IP {ip} to {self.file_path}")
            else:
                print(f"INFO: MAC {mac} with IP {ip} already exists in {self.file_path}")
        except Exception as e:
            print(f"ERROR: Failed to append MAC {mac} and IP {ip} to {self.file_path}: {e}")

    def replace_device(self, old_mac, old_ip, new_mac, new_ip):
        global reg_mac, reg_ip
        try:
            lines = []
            replaced = False
            with open(self.file_path, 'r') as f:
                lines = f.readlines()
            with open(self.file_path, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(["mac", "ip"])
                for line in lines[1:]:
                    mac, ip = line.strip().split(',')
                    if mac == old_mac and ip == old_ip:
                        writer.writerow([new_mac, new_ip])
                        replaced = True
                    else:
                        writer.writerow([mac, ip])
                if not replaced:
                    print(f"WARNING: MAC {old_mac} with IP {old_ip} not found in {self.file_path}")
                    return
            if old_mac in reg_mac:
                reg_mac.remove(old_mac)
            if old_ip in reg_ip:
                reg_ip.remove(old_ip)
            reg_mac.add(new_mac)
            reg_ip.add(new_ip)
            print(f"INFO: Replaced MAC {old_mac} with IP {old_ip} with MAC {new_mac} and IP {new_ip} in {self.file_path}")
        except Exception as e:
            print(f"ERROR: Failed to replace MAC {old_mac} with IP {old_ip}: {e}")

    def delete_device(self, mac, ip):
        global reg_mac, reg_ip
        try:
            lines = []
            with open(self.file_path, 'r') as f:
                lines = f.readlines()
            with open(self.file_path, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(["mac", "ip"])
                for line in lines[1:]:
                    if line.strip() and not line.strip().startswith(f"{mac},{ip}"):
                        writer.writerow(line.strip().split(','))
            if mac in reg_mac:
                reg_mac.remove(mac)
            if ip in reg_ip:
                reg_ip.remove(ip)
            print(f"INFO: Deleted MAC {mac} and IP {ip} from {self.file_path}")
        except Exception as e:
            print(f"ERROR: Failed to delete MAC {mac} and IP {ip} from {self.file_path}: {e}")

    def fetch_all_devices(self):
        try:
            devices = {}
            with open(self.file_path, 'r') as f:
                reader = csv.reader(f)
                next(reader)  # Skip header
                for mac, ip in reader:
                    if mac and ip:
                        devices[mac] = ip
            return devices
        except Exception as e:
            print(f"ERROR: Failed to fetch devices from {self.file_path}: {e}")
            return {}

    def load_devices_to_reg_sets(self):
        global reg_mac, reg_ip, mac_to_ip, ip_to_mac
        try:
            devices = self.fetch_all_devices()
            reg_mac.update(devices.keys())
            reg_ip.update(devices.values())
            mac_to_ip.update(devices)
            ip_to_mac.update({ip: mac for mac, ip in devices.items()})
            print(f"INFO: Loaded devices to reg_mac: {reg_mac}, reg_ip: {reg_ip}")
        except Exception as e:
            print(f"ERROR: Failed to load devices from {self.file_path}: {e}")

async def periodic_monitor_nodes(mqtt_client, monitor, logger):
    while True:
        if mqtt_client.is_connected:
            await monitor.monitor_nodes(mqtt_client, logger)
        else:
            print("INFO: MQTT client not connected, skipping monitor_nodes")
        await asyncio.sleep(LIVE_TIME)

async def periodic_sync_devices(mqtt_client, monitor):
    global connected_nodes, macs
    print(f"INFO: Before MAC List : {macs}")
    macs.clear()
    print(f"INFO: After MAC List : {macs}")
    while True:
        if mqtt_client.is_connected:
            print("INFO: Running periodic sync for all active nodes")
            tasks = []
            for ip in connected_nodes:
                d_id = ip_to_mac.get(ip, "unknown")
                if d_id != "unknown":
                    tasks.append(mqtt_client.handle_sync_device(ip, d_id))
            gateway = {
                "gtw_n": gateway_name,
                "cntd_n": list(macs)  # Convert set to list
            }
            #mqtt_client.publish("gateways/gateway_name/devices/ack", json.dumps(gateway))
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            else:
                print("INFO: No active nodes to sync")
        else:
            print("INFO: MQTT client not connected, skipping sync_devices")
        await asyncio.sleep(SYNC_TIME)

async def main():
    global macs
    def handle_sigtstp(signum, frame):
        print("INFO: Received Ctrl+Z (SIGTSTP), terminating...")
        raise SystemExit("Terminated by Ctrl+Z")

    signal.signal(signal.SIGTSTP, handle_sigtstp)
    monitor = WiSunMonitor()
    reg = RegisteredIPManager(file_path="registered_devices.txt")
    logger = PayloadAnomalyLogger()
    mqtt_client = MQTTClient(
        broker=MQTT_BROKER,
        port=MQTT_PORT,
        client_id=GATEWAY_NAME,
        username=MQTT_USERNAME,
        password=MQTT_PASSWORD,
        ca_cert=CA_CERT,
        reg=reg
    )
    mqtt_client.update_dynamic_topics(gateway_name)

    try:
        mqtt_client.connect()
    except Exception as e:
        print(f"ERROR: Failed to connect to MQTT broker: {e}, exiting...")
        return
    for _ in range(10):
        if mqtt_client.is_connected:
            break
        await asyncio.sleep(1)
    if not mqtt_client.is_connected:
        print("ERROR: MQTT client failed to connect, exiting...")
        return
    topics = [
        (mqtt_client.GATEWAY_TOPIC, 0),
        (mqtt_client.motor_control_topic, 0),
        (mqtt_client.device_config_topic, 0),
        (mqtt_client.motor_config_topic, 0),
        (mqtt_client.motor_mode_config_topic, 0),
    ]
    mqtt_client.client.subscribe(topics, qos=0)
    print(f"INFO: Subscribed to topics: {[t[0] for t in topics]}")

    monitor_task = asyncio.create_task(periodic_monitor_nodes(mqtt_client, monitor, logger))
    sync_task = asyncio.create_task(periodic_sync_devices(mqtt_client, monitor))

    try:
        while True:
            try:
                if not mqtt_client.is_connected:
                    await mqtt_client.reconnect_async()
                    mqtt_client.client.subscribe(topics, qos=0)
                    print(f"INFO: Re-subscribed to topics: {[t[0] for t in topics]}")
                await asyncio.sleep(180)
            except KeyboardInterrupt:
                print("INFO: Received Ctrl+C (KeyboardInterrupt), terminating...")
                monitor_task.cancel()
                sync_task.cancel()
                try:
                    await asyncio.gather(monitor_task, sync_task, return_exceptions=True)
                except asyncio.CancelledError:
                    pass
                mqtt_client.client.loop_stop()
                mqtt_client.client.disconnect()
                raise SystemExit("Terminated by Ctrl+C")
    except asyncio.CancelledError:
        print("INFO: Periodic tasks cancelled")
        mqtt_client.client.loop_stop()
        mqtt_client.client.disconnect()      

if __name__ == "__main__":
    asyncio.run(main())
