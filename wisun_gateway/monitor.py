import re
from pydbus import SystemBus
from .node import Node
from .config import GATEWAY_NAME
from .constants import *

class WiSunMonitor:
    def __init__(self):
        self.bus = SystemBus()
        self.proxy = self.bus.get("com.silabs.Wisun.BorderRouter", "/com/silabs/Wisun/BorderRouter")

    @staticmethod
    def pretty_ipv6(raw: str) -> str:
        ipv6 = ":".join(raw[i:i+4] for i in range(0, len(raw), 4))
        ipv6 = re.sub(r"0000:", ":", ipv6)
        ipv6 = re.sub(r":{2,}", "::", ipv6)
        return ipv6

    async def get_nodes(self):
        try:
            nodes = await self.proxy.Nodes
            connected = set()
            for node in nodes:
                if isinstance(node, tuple) and len(node) > 1:
                    ipv6_hex = bytes(node[1].get("ipv6", [0]))[1:].hex() if isinstance(node[1], dict) else bytes(node[0]).hex()
                    connected.add(self.pretty_ipv6(ipv6_hex))
            return connected
        except Exception as e:
            print(f"Error fetching nodes: {e}")
            return set()