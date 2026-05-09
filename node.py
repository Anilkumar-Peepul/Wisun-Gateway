import asyncio
import json
import os
from constants import LIVE_PERIOD, SYNC_PERIOD, COAP_GET_TIMEOUT, COAP_PUT_TIMEOUT
from aiocoap import Context, Message, GET, PUT, CON

class Node:
    def __init__(self, ipv6: str, uri: str, payload: str = ""):
        self.ipv6 = ipv6
        self.uri = uri
        self.payload = payload.encode() if isinstance(payload, str) else payload

    async def get(self, timeout=COAP_GET_TIMEOUT):
        return await self._request(GET, timeout)

    async def put(self, timeout=COAP_PUT_TIMEOUT):
        return await self._request(PUT, timeout, CON)

    async def _request(self, code, timeout, mtype=None):
        protocol = await Context.create_client_context()
        req = Message(code=code, uri=self._uri(), payload=self.payload)
        if mtype:
            req.mtype = mtype
            req.token = os.urandom(2)

        try:
            resp = await asyncio.wait_for(protocol.request(req).response, timeout)
            return self._parse(resp)
        except asyncio.TimeoutError:
            print(f"Timeout: {self.ipv6}/{self.uri}")
            return None
        except Exception as e:
            print(f"CoAP Error {self.ipv6}: {e}")
            return None

    def _uri(self):
        return f"coap://[{self.ipv6}]:5683/{self.uri}"

    @staticmethod
    def _parse(resp):
        if not resp.payload:
            return None
        text = resp.payload.decode().strip()
        try:
            return json.loads(text)
        except:
            return text
