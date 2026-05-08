import asyncio
import re
import time
import json
import paho.mqtt.client as mqtt
from aiocoap import Context, Message, GET, PUT, CON
import ssl
import os
import logging
from openpyxl import Workbook, load_workbook
from datetime import datetime
import csv
from pathlib import Path
import signal
from aiocoap.resource import Resource
from pydbus import SystemBus

# Define global variables
flag = 0
Frequency = 1
connected_nodes = set()
active_nodes = set()
macs = set()
mac_to_ip = {}
ip_to_mac = {}
Gateway_list = {}
reg_mac = set()
reg_ip = set()
SYNC_TIME = 180
LIVE_TIME = 20

# Load configuration from config.json
CONFIG_FILE = "./config.json"

def load_config():
    global network_name
    try:
        with open(CONFIG_FILE, 'r') as f:
            config = json.load(f)
            network_name = config.get("network_name", "GROWEL_AQUA_GTW_3")
            return {
                "broker": config.get("broker", "e2b4bba3.ala.asia-southeast1.emqxsl.com"),
                "port": config.get("port", 8883),
                "client_id": config.get("client_id", "Growel AQUA 3"),
                "username": config.get("username", "ss_user"),
                "password": config.get("password", "123456"),
                "ca_cert": config.get("ca_cert", "./ca_cert.crt")
            }
    except Exception as e:
        logging.error(f"Failed to load config from {CONFIG_FILE}: {e}")
        print(f"ERROR: Failed to load config from {CONFIG_FILE}: {e}")
        return {
            "broker": "e2B4bba3.ala.asia-southeast1.emqxsl.com",
            "port": 8883,
            "client_id": "Growel Aqua 3",
            "username": "ss_user",
            "password": "123456",
            "ca_cert": "./ca_cert.crt"
        }

# Configure logging
logging.basicConfig(
    filename='BR_V1.0_LOGG_FILE',
    filemode='w',
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

class Node:
    def __init__(self, ipv6_address='', uri="", payload=''):
        self.ipv6_address = ipv6_address
        self.uri = uri
        self.payload = payload.encode('utf-8') if payload else payload

    async def async_get_Device_data(self, timeout=40):
        try:
            uri = f"coap://[{self.ipv6_address}]:5683/{self.uri}"
            protocol = await Context.create_client_context()
            request = Message(code=GET, uri=uri)
            try:
                response = await asyncio.wait_for(protocol.request(request).response, timeout)
            except asyncio.TimeoutError:
                if flag == 0:
                    print(f"ERROR: Timeout: No response from node {self.ipv6_address} within {timeout} seconds.")
                elif flag == 1:
                    logging.error(f"Timeout: No response from node {self.ipv6_address} within {timeout} seconds.")
                return None
            except Exception as e:
                if flag == 0:
                    print(f"WARNING: Exception during CoAP request to {self.ipv6_address}: {e}")
                elif flag == 1:
                    logging.warning(f"Exception during CoAP request to {self.ipv6_address}: {e}")
                return None
            output = response.payload.decode().strip()
            if flag == 0:
                print(f"INFO: Within {timeout} seconds")
                print(f"INFO: Device data from node {self.ipv6_address}: {output}")
            elif flag == 1:
                logging.info(f"Within {timeout} seconds")
                logging.info(f"Device data from node {self.ipv6_address}: {output}")
            try:
                return json.loads(output)
            except json.JSONDecodeError:
                if flag == 0:
                    print(f"ERROR: Failed to parse Device data from node {self.ipv6_address}")
                    return None
                elif flag == 1:
                    logging.error(f"Failed to parse Device data from node {self.ipv6_address}")
                    return None
        except Exception as e:
            if flag == 0:
                print(f"WARNING: Exception in async_get_Device_data for node {self.ipv6_address}: {e}")
                return None
            elif flag == 1:
                logging.warning(f"Exception in async_get_Device_data for node {self.ipv6_address}: {e}")
                return None

    async def check_nodes_activity(self):
        global connected_nodes, active_nodes, mac_to_ip, ip_to_mac
        if flag == 0:
            print(f"Connected Nodes: {connected_nodes}")
        else:
            logging.info(f"Connected Nodes: {connected_nodes}")
        tasks = [Node(node, "settings/auto_send").async_get_Device_data(40) for node in connected_nodes]
        results = await asyncio.gather(*tasks)
        active_nodes = {node for node, result in zip(connected_nodes, results) if result is not None}
        return active_nodes

    async def node_command(self):
        context = await Context.create_client_context()
        request1 = Message(
            mtype=CON,
            code=PUT,
            payload=self.payload,
            uri=f"coap://[{self.ipv6_address}]:5683/{self.uri}",
            token=os.urandom(2)
        )
        try:
            response = await asyncio.wait_for(context.request(request1).response, timeout=120)
            reply = response.payload.decode("utf-8") if response.payload else ""
            reply = json.loads(reply)
            print(f"PUT response from {self.ipv6_address}/{self.uri}: {reply} (Code: {response.code})")
            if flag == 0:
                print(f"INFO: Result for {self.ipv6_address} on {self.uri}: {response.code}\n{reply!r}")
            elif flag == 1:
                logging.info(f"Result for {self.ipv6_address} on {self.uri}: {response.code}\n{reply!r}")
            if response.code:
                if flag == 0:
                    print(f"INFO: The node {self.ipv6_address} successfully processed the request on {self.uri}.")
                elif flag == 1:
                    logging.info(f"The node {self.ipv6_address} successfully processed the request on {self.uri}.")
            return reply
        except asyncio.TimeoutError:
            if flag == 0:
                print(f"ERROR: Timeout: No response from {self.ipv6_address} on {self.uri} within 5 seconds")
                return None
            elif flag == 1:
                logging.error(f"Timeout: No response from {self.ipv6_address} on {self.uri} within 5 seconds")
                return None
        except Exception as e:
            if flag == 0:
                print(f"WARNING: Exception CoAP request failed on {self.uri}: {e}")
            elif flag == 1:
                logging.warning(f"Exception CoAP request failed on {self.uri}: {e}")
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
        global connected_nodes, network_name
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
            if flag == 0:
                print(f"WARNING: Exception error fetching nodes: {e}")
            elif flag == 1:
                logging.warning(f"Exception error fetching nodes: {e}")
            return []

    async def update_mac_ip_mapping(self, mqtt_client, reg):
        global connected_nodes, network_name, mac_to_ip, ip_to_mac, reg_mac, macs, Gateway_list
        previous_nodes = connected_nodes.copy()
        await self.get_nodes()
        new_nodes = connected_nodes - previous_nodes if self.initial_mapping_done else connected_nodes
        if len(macs) == 0:
            new_nodes = connected_nodes
        if new_nodes:
            logging.info(f"New nodes detected: {new_nodes}")
            print(f"{datetime.now()} - INFO - New nodes detected: {new_nodes}")
            logging.info("Updating MAC-to-IP mapping")
            print(f"{datetime.now()} - INFO - Updating MAC-to-IP mapping")
            tasks = [Node(node, "info/all").async_get_Device_data(40) for node in new_nodes]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for ip, data in zip(new_nodes, results):
                if data is not None and not isinstance(data, Exception):
                    mac = data.get("MAC", "N/A").upper()
                    if mac != "N/A":
                        if mac not in macs:
                            logging.info(f"New MAC address detected for IP {ip}: {mac}")
                            print(f"{datetime.now()} - INFO - New MAC address detected for IP {ip}: {mac}")
                        mac_to_ip[mac] = ip
                        ip_to_mac[ip] = mac
                        macs.add(mac)
                        if mac in reg_mac:
                            reg.append_ip(ip)
                        logging.info(f"Mapped MAC {mac} to IP {ip}")
                        print(f"{datetime.now()} - INFO - Mapped MAC {mac} to IP {ip}")
                    else:
                        logging.warning(f"No valid MAC address in response from {ip}")
                        print(f"{datetime.now()} - WARNING - No valid MAC address in response from {ip}")
                else:
                    logging.error(f"Failed to fetch MAC from {ip}: {data}")
                    print(f"{datetime.now()} - ERROR - Failed to fetch MAC from {ip}: {data}")
            Gateway_list = {"gtw_n": network_name, "cntd_n": list(macs)}
            if len(macs) != 0 and network_name:
                mqtt_client.publish("gateways/gateway_name/devices/ack", json.dumps(Gateway_list))
            logging.info(f"MAC-to-IP mapping: {mac_to_ip}")
            print(f"{datetime.now()} - INFO - MAC-to-IP mapping: {mac_to_ip}")
            logging.info(f"IP-to-MAC mapping: {ip_to_mac}")
            print(f"{datetime.now()} - INFO - IP-to-MAC mapping: {ip_to_mac}")
            self.initial_mapping_done = True
        else:
            print("No New Node Connected")

    async def monitor_nodes(self, mqtt_client, logger):
        global connected_nodes, network_name, Gateway_list, reg_ip
        if flag == 0:
            print("INFO: Handling monitoring")
        elif flag == 1:
            logging.info("Handling monitoring")
        try:
            if connected_nodes:
                node = Node()
                tasks = [Node(node_item, "info/motor_status").async_get_Device_data() for node_item in connected_nodes]
                data = await asyncio.gather(*tasks, return_exceptions=True)
                for node_item, data_item in zip(connected_nodes, data):
                    if data_item is not None and not isinstance(data_item, Exception):
                        if flag == 0:
                            print(f"INFO: Data from {node_item}: {data_item}")
                        elif flag == 1:
                            logging.info(f"Data from {node_item}: {data_item}")
                        topic = f"gateways/{network_name}/devices/live_data"
                        message = json.dumps(data_item)
                        mqtt_client.publish(topic, message)
                        logger.process_payload(data_item)
                    else:
                        if flag == 0:
                            print(f"ERROR: No data for {node_item}")
                        elif flag == 1:
                            logging.error(f"No data for {node_item}")
            else:
                if flag == 0:
                    print("ERROR: No Nodes are Connected")
                elif flag == 1:
                    logging.error("No Nodes are Connected")
        except Exception as e:
            if flag == 0:
                print(f"WARNING: Exception error during monitoring: {e}")
            elif flag == 1:
                logging.warning(f"Exception error during monitoring: {e}")

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
    MOTOR_SCHEDULE_TOPIC = "gateways/{}/devices/schedule"
    MOTOR_SCHEDULE_ACK_TOPIC = "gateways/{}/devices/schedule/ack"
    
    def __init__(self, reg):
        config = load_config()
        self.broker = config["broker"]
        self.port = config["port"]
        self.client_id = config["client_id"]
        self.username = config["username"]
        self.password = config["password"]
        self.client = mqtt.Client(self.client_id)
        self.loop = asyncio.get_event_loop()
        self.client.username_pw_set(self.username, self.password)
        self.client.on_connect = self.on_connect
        self.client.on_disconnect = self.on_disconnect
        self.client.on_message = self.on_message
        self.client.on_subscribe = self.on_subscribe
        self.is_connected = False
        self.client.tls_set(ca_certs=config["ca_cert"], tls_version=ssl.PROTOCOL_TLSv1_2)
        self.device_config_topic = None
        self.motor_control_topic = None
        self.motor_control_ack_topic = None
        self.device_status_topic = None
        self.device_sync_ack_topic = None
        self.device_config_ack_topic = None
        self.motor_config_topic = None
        self.motor_mode_config_topic = None
        self.motor_mode_config_ack_topic = None
        self.motor_schedule_topic = None
        self.motor_schedule_ack_topic = None
        self.reg = reg

    def update_dynamic_topics(self, network_name):
        self.device_config_topic = self.DEVICE_CONFIG_TOPIC.format(network_name)
        self.motor_control_topic = self.MOTOR_CONTROL_TOPIC.format(network_name)
        self.motor_control_ack_topic = self.MOTOR_CONTROL_ACK_TOPIC.format(network_name)
        self.device_status_topic = self.DEVICE_STATUS_TOPIC.format(network_name)
        self.device_sync_ack_topic = self.DEVICE_SYNC_ACK_TOPIC.format(network_name)
        self.device_config_ack_topic = self.DEVICE_CONFIG_ACK_TOPIC.format(network_name)
        self.motor_config_topic = self.MOTOR_CONFIG_TOPIC.format(network_name)
        self.motor_mode_config_topic = self.MOTOR_MODE_CONFIG_TOPIC.format(network_name)
        self.motor_mode_config_ack_topic = self.MOTOR_MODE_CONFIG_ACK_TOPIC.format(network_name)
        self.motor_schedule_topic = self.MOTOR_SCHEDULE_TOPIC.format(network_name)
        self.motor_schedule_ack_topic = self.MOTOR_SCHEDULE_ACK_TOPIC.format(network_name)

    def on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            if flag == 0:
                print("INFO: Successfully connected to MQTT Broker with SSL!")
            elif flag == 1:
                logging.info("Successfully connected to MQTT Broker with SSL!")
            self.is_connected = True
        else:
            if flag == 0:
                print(f"ERROR: Failed to connect, return code {rc}")
            elif flag == 1:
                logging.error(f"Failed to connect, return code {rc}")
            self.is_connected = False

    def on_disconnect(self, client, userdata, rc):
        if flag == 0:
            print("ERROR: Disconnected from MQTT Broker. Reconnecting...")
        elif flag == 1:
            logging.error("Disconnected from MQTT Broker. Reconnecting...")
        self.is_connected = False

    def on_subscribe(self, client, userdata, mid, granted_qos):
        if flag == 0:
            print(f"INFO: Successfully subscribed with QoS {granted_qos}")
        elif flag == 1:
            logging.info(f"Successfully subscribed with QoS {granted_qos}")

    def is_ipv6(self, address):
        ipv6_pattern = r'^([0-9a-fA-F]{1,4}:){1,7}([0-9a-fA-F]{1,4}|:)$|^([0-9a-fA-F]{1,4}:)*::$|^::$'
        return bool(re.match(ipv6_pattern, address)) or '::' in address

    def is_mac(self, address):
        mac_pattern = r'^([0-9A-Fa-f]{2}:){7}[0-9A-Fa-f]{2}$'
        return bool(re.match(mac_pattern, address))

    def on_message(self, client, userdata, msg):
        if flag == 0:
            print(f"INFO: Received message on topic {msg.topic}: {msg.payload.decode('utf-8')}")
        elif flag == 1:
            logging.info(f"Received message on topic {msg.topic}: {msg.payload.decode('utf-8')}")
        try:
            message = json.loads(msg.payload.decode('utf-8'))
        except json.JSONDecodeError as e:
            if flag == 0:
                print(f"ERROR: Invalid JSON in message on topic {msg.topic}: {e}")
            elif flag == 1:
                logging.error(f"Invalid JSON in message on topic {msg.topic}: {e}")
            return
        if msg.topic == self.GATEWAY_TOPIC:
            global reg_mac, reg_ip, mac_to_ip, ip_to_mac
            try:
                macs_addresses = message['cntd_n']
                macs_addresses = set(macs_addresses)
                RegisteredIPManager(file_path="Registered_Macs.txt").append_multi_ip(macs_addresses)
            except Exception as e:
                if flag == 0:
                    print(f"ERROR: Exception in Motor Config processing: {e}")
                elif flag == 1:
                    logging.error(f"Exception in Motor Config processing: {e}")
        elif msg.topic == self.motor_control_topic:
            try:
                self.loop.create_task(self.handle_motor_control_message(message))
            except Exception as e:
                if flag == 0:
                    print(f"ERROR: Error scheduling motor control message: {e}")
                else:
                    logging.error(f"Error scheduling motor control message: {e}")
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
                if flag == 0:
                    print(f"ERROR: Error scheduling motor mode control message: {e}")
                else:
                    logging.error(f"Error scheduling motor mode control message: {e}")
                error_payload = {
                    "d_id": message.get("d_id", "N/A"),
                    "mtr_1": 8,
                    "mtr_2": 8
                }
                self.publish(self.motor_mode_config_ack_topic, json.dumps(error_payload))
        elif msg.topic == self.motor_config_topic:
            global reg_mac, reg_ip, mac_to_ip, ip_to_mac
            try:
                macs_addresses = message['cntd_n']
                macs_addresses = set(macs_addresses)
                RegisteredIPManager(file_path="Registered_Macs.txt").append_multi_ip(macs_addresses)
            except Exception as e:
                if flag == 0:
                    print(f"ERROR: Exception in Motor Config processing: {e}")
                elif flag == 1:
                    logging.error(f"Exception in Motor Config processing: {e}")
        elif msg.topic == self.device_config_topic:
            try:
                d_id = message.get("d_id")
                ip = mac_to_ip.get(d_id)
                if flag == 0:
                    print(f"INFO: Config MAC: {d_id}, Config IP: {ip}")
                if ip:
                    self.loop.create_task(self.handle_config(ip, json.dumps(message)))
                else:
                    config_ack = {"r": 0}
                    self.publish(self.device_config_ack_topic, json.dumps(config_ack))
            except Exception as e:
                if flag == 0:
                    print(f"ERROR: Exception in config processing: {e}")
                elif flag == 1:
                    logging.error(f"Exception in config processing: {e}")
                config_ack = {"sn": message.get("sn", ""), "d_id": d_id, "r": 0}
                self.publish(self.device_config_ack_topic, json.dumps(config_ack))
        elif msg.topic == self.motor_schedule_topic:
            try:
                d_id = message.get("d_id")
                ip = mac_to_ip.get(d_id)
                if flag == 0:
                    print(f"INFO: Scheduling MAC: {d_id}, IP: {ip}")
                else:
                    logging.info(f"INFO: Scheduling MAC: {d_id}, IP: {ip}")
                #message = json.loads(message)
                del message['d_id']
                self.loop.create_task(self.handle_scheduling_task(ip, json.dumps(message)))
                if flag == 0:
                    print(f"Updated Scheduling Payload: {json.dumps(message)}")
                else:
                    logging.info(f"Updated Scheduling Payload: {json.dumps(message)}")
            except Exception as e:
                if flag == 0:
                    print(f"ERROR: Exception in Scheduling processing: {e}")
                elif flag == 1:
                    logging.error(f"Exception in Scheduling processing: {e}")

    async def handle_scheduling_task(self, ip_address, schedule_message):
        try:
            state = await asyncio.wait_for(Node(ip_address, "create_schedule", schedule_message).node_command(), timeout=50)
            message = state #json.loads(state)
            sch_status = message.get("schedule_sts")
            print("Status",sch_status)
            if sch_status == "SCHEDULE_UPDATE_STARTED":
                if flag == 0:
                    print(f"Scheduling Status is {sch_status}")
                else:
                    logging.info(f"Scheduling Status is {sch_status}")
                time.sleep(3)
                state = await asyncio.wait_for(Node(ip_address, "create_schedule", '').async_get_Device_data(), timeout=50)
                #sch_resp = json.loads(state)
                if sch_resp == state:
                    self.publish(self.motor_schedule_ack_topic, schedule_message)
        except Exception as e:
            if flag == 0:
                print(f"Scheduling Error as {e}")
            else:
                logging.info(f"Scheduling Error as {e}")

    async def handle_motor_control_message(self, message):
        dev_list = []
        dev_err_list = []
        tasks = []
        
        for device in message.get("dev", []):
            d_id = device.get("d_id", None)
            mtr_1 = device.get("mtr_1", None)
            mtr_2 = device.get("mtr_2", None)
            
            if not d_id:
                if flag == 0:
                    print(f"ERROR: Missing d_id in device entry")
                else:
                    logging.error(f"Missing d_id in device entry")
                error_payload = {"d_id": "N/A", "mtr_1": 8, "mtr_2": 8}
                dev_err_list.append(error_payload)
                self.publish(self.motor_control_ack_topic, json.dumps({"dev": [error_payload]}))
                continue

            ip = None
            if self.is_ipv6(d_id) and d_id in connected_nodes:
                ip = d_id
            else:
                ip = mac_to_ip.get(d_id)
            if not ip:
                if flag == 0:
                    print(f"ERROR: No IPv6 address found for device {d_id}")
                else:
                    logging.error(f"No IPv6 address found for device {d_id}")
                error_payload = {"d_id": d_id, "mtr_1": 8, "mtr_2": 8}
                dev_err_list.append(error_payload)
                self.publish(self.motor_control_ack_topic, json.dumps({"dev": [error_payload]}))
                continue
            
            if mtr_1 is not None and (not isinstance(mtr_1, int) or mtr_1 not in [0, 1]):
                if flag == 0:
                    print(f"ERROR: Invalid mtr_1 value for device {d_id}: {mtr_1}")
                else:
                    logging.error(f"Invalid mtr_1 value for device {d_id}: {mtr_1}")
                error_payload = {"d_id": d_id, "mtr_1": 9, "mtr_2": 9}
                dev_err_list.append(error_payload)
                self.publish(self.motor_control_ack_topic, json.dumps({"dev": [error_payload]}))
                continue

            if mtr_2 is not None and (not isinstance(mtr_2, int) or mtr_2 not in [0, 1]):
                if flag == 0:
                    print(f"ERROR: Invalid mtr_2 value for device {d_id}: {mtr_2}")
                else:
                    logging.error(f"Invalid mtr_2 value for device {d_id}: {mtr_2}")
                error_payload = {"d_id": d_id, "mtr_1": 9, "mtr_2": 9}
                dev_err_list.append(error_payload)
                self.publish(self.motor_control_ack_topic, json.dumps({"dev": [error_payload]}))
                continue

            mc_payload = {}
            if mtr_1 is not None:
                mc_payload["mtr_1"] = mtr_1
            if mtr_2 is not None:
                mc_payload["mtr_2"] = mtr_2
            if flag == 0:
                print(f"INFO: Device ID: {d_id}, Motor 1: {mtr_1}, Motor 2: {mtr_2}, IP: {ip}")
                print(f"INFO: Motor control payload: {mc_payload}")
            else:
                logging.info(f"Device ID: {d_id}, Motor 1: {mtr_1}, Motor 2: {mtr_2}, IP: {ip}")
                logging.info(f"Motor control payload: {mc_payload}")
            tasks.append((d_id, mtr_1, mtr_2, Node(ip, "motor_control", json.dumps(mc_payload)).node_command()))

        results = await asyncio.gather(*[task[3] for task in tasks], return_exceptions=True)
        for (d_id, mtr_1, mtr_2, _), Data in zip(tasks, results):
            device_ack = {"d_id": d_id}
            if isinstance(Data, Exception):
                if flag == 0:
                    print(f"ERROR: CoAP request failed for device {d_id}: {Data}")
                else:
                    logging.error(f"CoAP request failed for device {d_id}: {Data}")
                device_ack["mtr_1"] = 10
                device_ack["mtr_2"] = 10
                dev_err_list.append(device_ack)
                self.publish(self.motor_control_ack_topic, json.dumps({"dev": [device_ack]}))
                continue
            
            try:
                if mtr_1 is not None or (isinstance(Data, dict) and "mtr_1" in Data):
                    device_ack["mtr_1"] = Data.get("mtr_1", mtr_1) if isinstance(Data, dict) else mtr_1
                if mtr_2 is not None or (isinstance(Data, dict) and "mtr_2" in Data):
                    device_ack["mtr_2"] = Data.get("mtr_2", mtr_2) if isinstance(Data, dict) else mtr_2
                dev_list.append(device_ack)
                if flag == 0:
                    print(f"INFO: Motor Control ACK Device: {device_ack}")
                else:
                    logging.info(f"Motor Control ACK Device: {device_ack}")
            except Exception as e:
                if flag == 0:
                    print(f"ERROR: Error processing CoAP response for device {d_id}: {e}")
                else:
                    logging.error(f"Error processing CoAP response for device {d_id}: {e}")
                device_ack["mtr_1"] = 10
                device_ack["mtr_2"] = 10
                dev_err_list.append(device_ack)
                self.publish(self.motor_control_ack_topic, json.dumps({"dev": [device_ack]}))

        if dev_list:
            final_payload = {"dev": dev_list}
            if flag == 0:
                print(f"INFO: Motor Control ACK Payload: {json.dumps(final_payload)}")
            else:
                logging.info(f"Motor Control ACK Payload: {json.dumps(final_payload)}")
            self.publish(self.motor_control_ack_topic, json.dumps(final_payload))

    async def handle_motor_mode_control_message(self, message):
        dev_list = []
        dev_err_list = []
        tasks = []
        
        for device in message.get("dev", []):
            d_id = device.get("d_id", None)
            mtr_1 = device.get("mtr_1", None)
            mtr_2 = device.get("mtr_2", None)
            if not d_id:
                if flag == 0:
                    print(f"ERROR: Missing d_id in device entry")
                else:
                    logging.error(f"Missing d_id in device entry")
                error_payload = {"d_id": d_id, "mtr_1": 6, "mtr_2": 6}
                dev_err_list.append(error_payload)
                self.publish(self.motor_mode_config_ack_topic, json.dumps({"dev": [error_payload]}))
                continue

            ip = None
            if self.is_ipv6(d_id) and d_id in connected_nodes:
                ip = d_id
            else:
                ip = mac_to_ip.get(d_id)
            if not ip:
                if flag == 0:
                    print(f"ERROR: No IPv6 address found for device {d_id}")
                else:
                    logging.error(f"No IPv6 address found for device {d_id}")
                error_payload = {"d_id": d_id, "mtr_1": 6, "mtr_2": 6}
                dev_err_list.append(error_payload)
                self.publish(self.motor_mode_config_ack_topic, json.dumps({"dev": [error_payload]}))
                continue

            if mtr_1 is not None and (not isinstance(mtr_1, int) or mtr_1 not in [0, 1]):
                if flag == 0:
                    print(f"ERROR: Invalid mtr_1 value for device  {d_id}: {mtr_1}")
                else:
                    logging.error(f"Invalid mtr_1 value for device {d_id}: {mtr_1}")
                error_payload = {"d_id": d_id, "mtr_1": 5, "mtr_2": 5}
                dev_err_list.append(error_payload)
                self.publish(self.motor_mode_config_ack_topic, json.dumps({"dev": [error_payload]}))
                continue

            if mtr_2 is not None and (not isinstance(mtr_2, int) or mtr_2 not in [0, 1]):
                if flag == 0:
                    print(f"ERROR: Invalid mtr_2 value for device {d_id}: {mtr_2}")
                else:
                    logging.error(f"Invalid mtr_2 value for device {d_id}: {mtr_2}")
                error_payload = {"d_id": d_id, "mtr_1": 5, "mtr_2": 5}
                dev_err_list.append(error_payload)
                self.publish(self.motor_mode_config_ack_topic, json.dumps({"dev": [error_payload]}))
                continue

            mc_payload = {}
            if mtr_1 is not None:
                mc_payload["mtr_1"] = mtr_1
            if mtr_2 is not None:
                mc_payload["mtr_2"] = mtr_2
            if flag == 0:
                print(f"INFO: Device ID: {d_id}, Motor 1: {mtr_1}, Motor 2: {mtr_2}, IP: {ip}")
                print(f"INFO: Motor mode control payload: {mc_payload}")
                print(f"DEBUG: Task type for {d_id}: {type(Node(ip, 'cm_change', json.dumps(mc_payload)).node_command())}")
            else:
                logging.info(f"Device ID: {d_id}, Motor 1: {mtr_1}, Motor 2: {mtr_2}, IP: {ip}")
                logging.info(f"Motor mode control payload: {mc_payload}")
            tasks.append((d_id, mtr_1, mtr_2, Node(ip, "cm_change", json.dumps(mc_payload)).node_command()))

        results = await asyncio.gather(*[task[3] for task in tasks], return_exceptions=True)
        for (d_id, mtr_1, mtr_2, _), Data in zip(tasks, results):
            device_ack = {"d_id": d_id}
            if isinstance(Data, Exception):
                if flag == 0:
                    print(f"ERROR: CoAP request failed for device {d_id}: {Data}")
                else:
                    logging.error(f"CoAP request failed for device {d_id}: {Data}")
                device_ack["mtr_1"] = 7
                device_ack["mtr_2"] = 7
                dev_err_list.append(device_ack)
                self.publish(self.motor_mode_config_ack_topic, json.dumps({"dev": [device_ack]}))
                continue
            
            try:
                if mtr_1 is not None or (isinstance(Data, dict) and "mtr_1" in Data):
                    device_ack["mtr_1"] = Data.get("mtr_1", mtr_1) if isinstance(Data, dict) else mtr_1
                if mtr_2 is not None or (isinstance(Data, dict) and "mtr_2" in Data):
                    device_ack["mtr_2"] = Data.get("mtr_2", mtr_2) if isinstance(Data, dict) else mtr_2
                dev_list.append(device_ack)
                if flag == 0:
                    print(f"INFO: Motor Mode Control ACK Device: {device_ack}")
                else:
                    logging.info(f"Motor Mode Control ACK Device: {device_ack}")
            except Exception as e:
                if flag == 0:
                    print(f"ERROR: Error processing CoAP response for device {d_id}: {e}")
                else:
                    logging.error(f"Error processing CoAP response for device {d_id}: {e}")
                device_ack["mtr_1"] = 7
                device_ack["mtr_2"] = 7
                dev_err_list.append(device_ack)
                self.publish(self.motor_mode_config_ack_topic, json.dumps({"dev": [device_ack]}))

        if dev_list:
            final_payload = {"dev": dev_list}
            if flag == 0:
                print(f"INFO: Motor Mode Control ACK Payload: {json.dumps(final_payload)}")
            else:
                logging.info(f"Motor Mode Control ACK Payload: {json.dumps(final_payload)}")
            self.publish(self.motor_mode_config_ack_topic, json.dumps(final_payload))

    async def handle_sync_device(self, ip, d_id):
        global Gateway_list
        try:
            data = await Node(ip, "info/motor_status").async_get_Device_data()
            ack_payload = json.dumps(data)
            if data:
                if flag == 0:
                    print(f"INFO: Sync data for {d_id}: {data}")
                elif flag == 1:
                    logging.info(f"Sync data for {d_id}: {data}")
            else:
                if flag == 0:
                    print(f"ERROR: No data for {d_id}")
                elif flag == 1:
                    logging.error(f"No data for {d_id}")
            if flag == 0:
                print(f"INFO: Publishing sync ack: {ack_payload}")
            elif flag == 1:
                logging.info(f"Publishing sync ack: {ack_payload}")
            self.publish(self.device_sync_ack_topic, ack_payload)
            self.publish(self.GATEWAY_ACK_TOPIC, json.dumps(Gateway_list))
        except Exception as e:
            if flag == 0:
                print(f"ERROR: Error during sync for {d_id}: {e}")
            elif flag == 1:
                logging.error(f"Error during sync for {d_id}: {e}")

    async def handle_config(self, ip, message):
        try:
            if flag == 0:
                print(f"INFO: Handling config request for {ip}")
            elif flag == 1:
                logging.info(f"Handling config request for {ip}")
            state = await asyncio.wait_for(Node(ip, "config", message).node_command(), timeout=50)
            message = json.loads(message)
            sn = message.get("sn")
            d_id = message.get("d_id")
            if flag == 0:
                print(f"INFO: CONFIG STATE for {ip}: {state}")
                print("Status", str(state.get("config_sts")))
            elif flag == 1:
                logging.info(f"CONFIG STATE for {ip}: {state}")
            if state and str(state.get("config_sts")) == "UPDATE_STARTED":
                time.sleep(5)
                data = await asyncio.wait_for(Node(ip, "config").node_command(), timeout=120)
                data = str(data.get("config_sts", ''))
                error_statuses = {"PARSE_FAILED", "MAC_MISMATCH", "VERIFY_FAILED", "ERASE_FAILED", "WRITE_FAILED", "UPDATE_PENDING", "PAYLOAD_TOO_LARGE"}
                if data not in error_statuses:
                    config_ack = {"sn": sn, "d_id": d_id, "r": 1}
                    self.publish(self.device_config_ack_topic, json.dumps(config_ack))
                else:
                    config_ack = {"sn": sn, "d_id": d_id, "r": 0}
                    self.publish(self.device_config_ack_topic, json.dumps(config_ack))
                    print(f"ERROR: Error status received: {data}")
            else:
                if flag == 0:
                    print(f"ERROR: Config update failed: {state}")
                elif flag == 1:
                    logging.error(f"Config update failed: {state}")
                config_ack = {"sn": sn, "d_id": d_id, "r": 0}
                self.publish(self.device_config_ack_topic, json.dumps(config_ack))
            if flag == 0:
                print(f"INFO: Publishing config ack")
            elif flag == 1:
                logging.info(f"Publishing config ack")
        except Exception as e:
            config_ack = {"sn": sn, "d_id": d_id, "r": 0}
            self.publish(self.device_config_ack_topic, json.dumps(config_ack))
            if flag == 0:
                print(f"ERROR: Error during config for: {e}")
            elif flag == 1:
                logging.error(f"Error during config for: {e}")

    def connect(self):
        try:
            self.client.connect(self.broker, self.port, keepalive=40)
            self.client.loop_start()
            if flag == 0:
                print("INFO: Attempting to connect to MQTT broker with SSL...")
            elif flag == 1:
                logging.info("Attempting to connect to MQTT broker with SSL...")
        except Exception as e:
            if flag == 0:
                print(f"ERROR: Connection failed: {e}")
            elif flag == 1:
                logging.error(f"Connection failed: {e}")
            raise

    async def reconnect_async(self):
        while not self.is_connected:
            try:
                self.client.connect(self.broker, self.port, keepalive=40)
                time.sleep(4)
                self.client.loop_start()
                if flag == 0:
                    print("INFO: Attempting to reconnect to MQTT broker with SSL...")
                elif flag == 1:
                    logging.info("Attempting to reconnect to MQTT broker with SSL...")
                await asyncio.sleep(2)
            except Exception as e:
                if flag == 0:
                    print(f"WARNING: Reconnection failed: {e}. Retrying in 5 seconds...")
                elif flag == 1:
                    logging.warning(f"Reconnection failed: {e}. Retrying in 5 seconds...")
                await asyncio.sleep(5)

    def publish(self, topic, payload):
        if self.is_connected:
            if isinstance(payload, bytes):
                try:
                    payload = payload.decode('utf-8')
                except Exception as e:
                    if flag == 0:
                        print(f"WARNING: Error decoding payload bytes: {e}")
                    elif flag == 1:
                        logging.warning(f"Error decoding payload bytes: {e}")
                    return
            try:
                parsed_payload = json.loads(payload)
                if flag == 0:
                    print(f"INFO: Before Publishing-- {topic}: {parsed_payload}")
                elif flag == 1:
                    logging.info(f"Before Publishing-- {topic}: {parsed_payload}")
                date = datetime.utcnow() - datetime(1970, 1, 1)
                milliseconds = round(date.total_seconds() * 1000)
                parsed_payload.update({"t_s": milliseconds})
                payload_str = json.dumps(parsed_payload)
                self.client.publish(topic, payload_str, qos=0, retain=False)
                if flag == 0:
                    print(f"INFO: Published to {topic}: {payload_str}")
                elif flag == 1:
                    logging.info(f"Published to {topic}: {payload_str}")
            except json.JSONDecodeError:
                if flag == 0:
                    print(f"INFO: Publishing raw non-JSON payload to {topic}: {payload}")
                elif flag == 1:
                    logging.info(f"Publishing raw non-JSON payload to {topic}: {payload}")
                self.client.publish(topic, payload, qos=0)
        else:
            if flag == 0:
                print("ERROR: Client not connected, cannot publish.")
            elif flag == 1:
                logging.error("Client not connected, cannot publish.")

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
    def __init__(self, file_path="registered_ips.txt"):
        self.file_path = Path(file_path)
        self._initialize_file()
        self.load_ips_to_reg_ip()

    def _initialize_file(self):
        if not self.file_path.exists():
            with open(self.file_path, 'w') as f:
                pass

    def append_ip(self, ip):
        try:
            with open(self.file_path, 'r') as f:
                existing_ips = {line.strip() for line in f if line.strip()}
            if ip not in existing_ips:
                with open(self.file_path, 'a') as f:
                    f.write(f"{ip}\n")
                if flag == 0:
                    print(f"INFO: Appended IP {ip} to {self.file_path}")
                elif flag == 1:
                    logging.info(f"Appended IP {ip} to {self.file_path}")
                reg_ip.add(ip)
            else:
                if flag == 0:
                    print(f"INFO: IP {ip} already exists in {self.file_path}")
                elif flag == 1:
                    logging.info(f"IP {ip} already exists in {self.file_path}")
        except Exception as e:
            if flag == 0:
                print(f"ERROR: Failed to append IP {ip} to {self.file_path}: {e}")
            elif flag == 1:
                logging.error(f"Failed to append IP {ip} to {self.file_path}: {e}")

    def append_multi_ip(self, ips):
        try:
            with open(self.file_path, 'r') as f:
                existing_ips = {line.strip() for line in f if line.strip()}
                for ip in ips:
                    if ip not in existing_ips:
                        with open(self.file_path, 'a') as f:
                            f.write(f"{ip}\n")
                            if flag == 0:
                                print(f"INFO: Appended MAC {ip} to {self.file_path}")
                            elif flag == 1:
                                logging.info(f"Appended MAC {ip} to {self.file_path}")
                    else:
                        if flag == 0:
                            print(f"INFO: Already existed MAC {ip} to {self.file_path}")
                        elif flag == 1:
                            logging.info(f"Already existed {ip} to {self.file_path}")
        except Exception as e:
            if flag == 0:
                print(f"ERROR: Failed to append IP {ip} to {self.file_path}: {e}")
            elif flag == 1:
                logging.error(f"Failed to append IP {ip} to {self.file_path}: {e}")

    def replace_ip(self, old_ip, new_ip):
        try:
            with open(self.file_path, 'r') as f:
                lines = f.readlines()
            lines = [line.strip() for line in lines if line.strip()]
            if old_ip in lines:
                lines = [new_ip if line == old_ip else line for line in lines]
                with open(self.file_path, 'w') as f:
                    for line in lines:
                        f.write(f"{line}\n")
                if old_ip in reg_ip:
                    reg_ip.remove(old_ip)
                reg_ip.add(new_ip)
                if flag == 0:
                    print(f"INFO: Replaced IP {old_ip} with {new_ip} in {self.file_path}")
                elif flag == 1:
                    logging.info(f"Replaced IP {old_ip} with {new_ip} in {self.file_path}")
            else:
                if flag == 0:
                    print(f"WARNING: IP {old_ip} not found in {self.file_path}")
                elif flag == 1:
                    logging.warning(f"IP {old_ip} not found in {self.file_path}")
        except Exception as e:
            if flag == 0:
                print(f"ERROR: Failed to replace IP {old_ip} with {new_ip}: {e}")
            elif flag == 1:
                logging.error(f"Failed to replace IP {old_ip} with {new_ip}: {e}")

    def delete_ip(self, ip):
        try:
            with open(self.file_path, 'r') as f:
                lines = f.readlines()
            lines = [line.strip() for line in lines if line.strip() and line.strip() != ip]
            with open(self.file_path, 'w') as f:
                for line in lines:
                    f.write(f"{line}\n")
            if ip in reg_ip:
                reg_ip.remove(ip)
            if flag == 0:
                print(f"INFO: Deleted IP {ip} from {self.file_path}")
            elif flag == 1:
                logging.info(f"Deleted IP {ip} from {self.file_path}")
        except Exception as e:
            if flag == 0:
                print(f"ERROR: Failed to delete IP {ip} from {self.file_path}: {e}")
            elif flag == 1:
                logging.error(f"Failed to delete IP {ip} from {self.file_path}: {e}")

    def fetch_all_ips(self):
        try:
            with open(self.file_path, 'r') as f:
                ips = {line.strip() for line in f if line.strip()}
            if flag == 0:
                print(f"INFO: Fetched IPs from {self.file_path}: {ips}")
            elif flag == 1:
                logging.info(f"Fetched IPs from {self.file_path}: {ips}")
            return ips
        except Exception as e:
            if flag == 0:
                print(f"ERROR: Failed to fetch IPs from {self.file_path}: {e}")
            elif flag == 1:
                logging.error(f"Failed to fetch IPs from {self.file_path}: {e}")
            return set()

    def load_ips_to_reg_ip(self):
        global reg_ip, reg_mac
        try:
            if self.file_path.name == "Registered_Macs.txt":
                device_set = reg_mac
                device_type = "MACs"
            else:
                device_set = reg_ip
                device_type = "IPs"
            
            with open(self.file_path, 'r') as f:
                device_set.update({line.strip() for line in f if line.strip()})
            
            if flag == 0:
                print(f"INFO: Loaded {device_type} to {device_type.lower()}: {device_set}")
            elif flag == 1:
                logging.info(f"Loaded {device_type} to {device_type.lower()}: {device_set}")
        except Exception as e:
            if flag == 0:
                print(f"ERROR: Failed to load {device_type} from {self.file_path}: {e}")
            elif flag == 1:
                logging.error(f"Failed to load {device_type} from {self.file_path}: {e}")

async def periodic_monitor_nodes(mqtt_client, monitor, logger):
    while True:
        if mqtt_client.is_connected:
            await monitor.monitor_nodes(mqtt_client, logger)
        else:
            if flag == 0:
                print("INFO: MQTT client not connected, skipping monitor_nodes")
            elif flag == 1:
                logging.info("MQTT client not connected, skipping monitor_nodes")
        await asyncio.sleep(LIVE_TIME)

async def periodic_sync_devices(mqtt_client, monitor):
    while True:
        if mqtt_client.is_connected:
            if flag == 0:
                print("INFO: Running periodic sync for all active nodes")
            elif flag == 1:
                logging.info("Running periodic sync for all active nodes")
            active = await Node().check_nodes_activity()
            tasks = []
            for ip in active:
                d_id = ip_to_mac.get(ip, "unknown")
                tasks.append(mqtt_client.handle_sync_device(ip, d_id))
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            else:
                if flag == 0:
                    print("INFO: No active nodes to sync")
                elif flag == 1:
                    logging.info("No active nodes to sync")
        else:
            if flag == 0:
                print("INFO: MQTT client not connected, skipping sync_devices")
            elif flag == 1:
                logging.info("MQTT client not connected, skipping sync_devices")
        await asyncio.sleep(SYNC_TIME)

async def main():
    def handle_sigtstp(signum, frame):
        if flag == 0:
            print("INFO: Received Ctrl+Z (SIGTSTP), terminating...")
        elif flag == 1:
            logging.info("Received Ctrl+Z (SIGTSTP), terminating...")
        raise SystemExit("Terminated by Ctrl+Z")

    signal.signal(signal.SIGTSTP, handle_sigtstp)
    monitor = WiSunMonitor()
    reg = RegisteredIPManager()
    logger = PayloadAnomalyLogger()
    config = load_config()
    ca_cert = config["ca_cert"]
    await monitor.get_nodes()
    if not network_name:
        if flag == 0:
            print("ERROR: Failed to set network_name, exiting...")
        elif flag == 1:
            logging.error("Failed to set network_name, exiting...")
        return
    mqtt_client = MQTTClient(reg)
    mqtt_client.update_dynamic_topics(network_name)

    try:
        mqtt_client.connect()
    except Exception as e:
        if flag == 0:
            print(f"ERROR: Failed to connect to MQTT broker: {e}, exiting...")
        elif flag == 1:
            logging.error(f"Failed to connect to MQTT broker: {e}, exiting...")
        return
    for _ in range(10):
        if mqtt_client.is_connected:
            break
        await asyncio.sleep(1)
    if not mqtt_client.is_connected:
        if flag == 0:
            print("ERROR: MQTT client failed to connect, exiting...")
        elif flag == 1:
            logging.error("MQTT client failed to connect, exiting...")
        return
    topics = [
        (mqtt_client.GATEWAY_TOPIC, 0),
        (mqtt_client.motor_control_topic, 0),
        (mqtt_client.device_config_topic, 0),
        (mqtt_client.motor_config_topic, 0),
        (mqtt_client.motor_mode_config_topic, 0),
        (mqtt_client.motor_schedule_topic, 0)
    ]
    mqtt_client.client.subscribe(topics, qos=0)
    await monitor.update_mac_ip_mapping(mqtt_client, reg)
    if flag == 0:
        print(f"INFO: Subscribed to topics: {[t[0] for t in topics]}")
    elif flag == 1:
        logging.info(f"Subscribed to topics: {[t[0] for t in topics]}")

    monitor_task = asyncio.create_task(periodic_monitor_nodes(mqtt_client, monitor, logger))
    sync_task = asyncio.create_task(periodic_sync_devices(mqtt_client, monitor))

    try:
        while True:
            try:
                if not mqtt_client.is_connected:
                    await mqtt_client.reconnect_async()
                    mqtt_client.client.subscribe(topics, qos=0)
                    if flag == 0:
                        print(f"INFO: Re-subscribed to topics: {[t[0] for t in topics]}")
                    elif flag == 1:
                        logging.info(f"Re-subscribed to topics: {[t[0] for t in topics]}")
                await monitor.update_mac_ip_mapping(mqtt_client, reg)
                reg.load_ips_to_reg_ip()
                RegisteredIPManager(file_path="Registered_Macs.txt").load_ips_to_reg_ip()
                await asyncio.sleep(60)
            except KeyboardInterrupt:
                if flag == 0:
                    print("INFO: Received Ctrl+C (KeyboardInterrupt), terminating...")
                elif flag == 1:
                    logging.info("Received Ctrl+C (KeyboardInterrupt), terminating...")
                monitor_task.cancel()
                sync_task.cancel()
                mqtt_client.client.loop_stop()
                mqtt_client.client.disconnect()
                raise SystemExit("Terminated by Ctrl+C")
            except Exception as e:
                if flag == 0:
                    print(f"WARNING: Non-critical error in main loop: {e}, continuing...")
                elif flag == 1:
                    logging.warning(f"Non-critical error in main loop: {e}, continuing...")
                await asyncio.sleep(1)
    except asyncio.CancelledError:
        if flag == 0:
            print("INFO: Periodic tasks cancelled")
        elif flag == 1:
            logging.info("Periodic tasks cancelled")
        mqtt_client.client.loop_stop()
        mqtt_client.client.disconnect()

if __name__ == "__main__":
    print(f"DEBUG: Type of main: {type(main)}")
    try:
        asyncio.run(main())
    except SystemExit as e:
        if flag == 0:
            print(f"INFO: Program terminated: {e}")
        elif flag == 1:
            logging.info(f"Program terminated: {e}")
    except Exception as e:
        if flag == 0:
            print(f"ERROR: Unexpected error in asyncio.run: {e}")
        elif flag == 1:
            logging.error(f"Unexpected error in asyncio.run: {e}")
