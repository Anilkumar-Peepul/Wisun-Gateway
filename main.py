# wisun_gateway/main.py
import asyncio
import signal
import logging
from monitor import WiSunMonitor
from storage import Storage
from logger import PayloadLogger
from mqtt_handler import MQTTHandler
from config import GATEWAY_NAME
from constants import LIVE_PERIOD, SYNC_PERIOD
from node import Node

class WiSunGateway:
    def __init__(self):
        self.storage = Storage()
        self.logger = PayloadLogger()          # Improved logger with anomaly detection
        self.monitor = WiSunMonitor()
        self.mqtt = MQTTHandler(self.storage, self.logger)

        # Load existing mappings
        self.storage.load_ip_mac()

        self.logger.logger.info(f"WiSun Gateway '{GATEWAY_NAME}' initialized successfully")

    async def periodic_monitor(self):
        """Periodic live monitoring of all connected nodes"""
        while True:
            if not self.mqtt.is_connected:
                await asyncio.sleep(LIVE_PERIOD)
                continue

            try:
                connected_nodes = await self.monitor.get_nodes()
                if not connected_nodes:
                    self.logger.logger.info("No nodes connected")
                    await asyncio.sleep(LIVE_PERIOD)
                    continue

                self.logger.logger.info(f"Monitoring {len(connected_nodes)} connected nodes")

                # Fetch data from all nodes
                tasks = [Node(ip, "info/motor_status").get() for ip in connected_nodes]
                results = await asyncio.gather(*tasks, return_exceptions=True)

                for ip, result in zip(connected_nodes, results):
                    if isinstance(result, Exception) or result is None:
                        self.logger.logger.warning(f"No response from node {ip}")
                        continue

                    # Process payload with anomaly detection + CSV logging
                    self.logger.process(result)

                    # Publish live data to MQTT
                    topic = f"gateways/{GATEWAY_NAME}/devices/live_data"
                    self.mqtt.publish(topic, result)

                    # Auto save new IP-MAC mapping
                    mac = str(result.get("d_id", "")).upper()
                    if mac and mac not in self.storage.mac_to_ip:
                        self.storage.save_ip_mac(ip, mac)
                        self.logger.logger.info(f"New device mapping saved: {mac} → {ip}")

            except Exception as e:
                self.logger.logger.error(f"Error in periodic monitor: {e}")

            await asyncio.sleep(LIVE_PERIOD)

    async def start(self):
        """Start the WiSun Gateway"""
        print(f"🚀 Starting WiSun Gateway: {GATEWAY_NAME}")
        self.logger.logger.info("Gateway starting...")

        # Connect to MQTT Broker
        self.mqtt.connect()

        # Wait for MQTT connection
        for _ in range(15):
            if self.mqtt.is_connected:
                break
            await asyncio.sleep(1)

        if not self.mqtt.is_connected:
            self.logger.logger.error("Failed to connect to MQTT broker. Exiting.")
            return

        # Start background monitoring task
        monitor_task = asyncio.create_task(self.periodic_monitor())

        try:
            while True:
                await asyncio.sleep(60)  # Keep main loop alive
        except asyncio.CancelledError:
            self.logger.logger.info("Gateway shutting down...")
            monitor_task.cancel()
            try:
                await monitor_task
            except asyncio.CancelledError:
                pass
        finally:
            self.mqtt.client.loop_stop()
            self.logger.logger.info("Gateway stopped.")


# ====================== Entry Point ======================
async def main():
    def handle_shutdown(sig, frame):
        print("\n🛑 Shutdown signal received. Stopping gracefully...")
        raise SystemExit(0)

    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)

    gateway = WiSunGateway()
    await gateway.start()


if __name__ == "__main__":
    asyncio.run(main())
