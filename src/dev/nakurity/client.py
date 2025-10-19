# src/dev/nakurity/client.py
import json
import websockets
from neuro_api.api import AbstractNeuroAPI, NeuroAction

"""
Optional: a Neuro client that connects out to a real Neuro backend.
If you don't need the relay to connect outward, you can ignore this file.
"""

class NakurityClient(AbstractNeuroAPI):
    def __init__(self, websocket, router_forward_cb):
        self.websocket = websocket
        self.name = "relay-outbound"
        super().__init__(self.name)
        # callback to forward action into local intermediary
        self.router_forward_cb = router_forward_cb

    async def write_to_websocket(self, data: str):
        await self.websocket.send(data)

    async def read_from_websocket(self) -> str:
        return await self.websocket.recv()

    async def initialize(self):
        await self.send_startup_command()
        # could register actions here if needed

    async def handle_action(self, action: NeuroAction):
        # forward to intermediary
        await self.router_forward_cb({
            "from_neuro_outbound": True,
            "action": action.name,
            "data": json.loads(action.data or "{}"),
            "id": action.id_
        })

    async def on_connect(self):
        print("[Nakurity Client] connected")

    async def on_disconnect(self):
        print("[Nakurity Client] disconnected")

async def connect_outbound(uri: str, router_forward_cb):
    async with websockets.connect(uri) as ws:
        c = NakurityClient(ws, router_forward_cb)
        await c.initialize()
        while True:
            try:
                await c.read_message()
            except websockets.exceptions.ConnectionClosed:
                break
