# src/dev/nakurity/client.py
import json
import websockets
import asyncio
from neuro_api.api import AbstractNeuroAPI, NeuroAction

"""
A Neuro client that connects out to a real Neuro backend.
"""

class NakurityClient(AbstractNeuroAPI):
    def __init__(self, websocket, router_forward_cb):
        self.websocket = websocket
        self.name = "relay-outbound"
        super().__init__(self.name)
        # router_forward_cb is expected to be an async callable that accepts a dict
        # e.g. intermediary._handle_intermediary_forward
        self.router_forward_cb = router_forward_cb
        self._reader_task: asyncio.Task | None = None
        print("[Nakurity Client] has initialized.")

    async def write_to_websocket(self, data: str):
        await self.websocket.send(data)

    async def read_from_websocket(self) -> str:
        return await self.websocket.recv()

    async def initialize(self):
        await self.send_startup_command()
        # Collect and register actions from intermediary
        actions_schema = await self.collect_registered_actions()
        if actions_schema:
            await self.register_actions(actions_schema)
        # Send environment context to Neuro
        await self.register_environment_context()

    async def handle_action(self, action: NeuroAction):
        # forward to intermediary
        await self.router_forward_cb({
            "from_neuro_outbound": True,
            "action": action.name,
            "data": json.loads(action.data or "{}"),
            "id": action.id_
        })

    async def collect_registered_actions(self):
        """Ask the intermediary (via router callback) for available actions."""
        try:
            resp = await self.router_forward_cb({"query": "get_registered_actions"})
            return resp.get("actions", {}) if isinstance(resp, dict) else {}
        except Exception as e:
            print(f"[Nakurity Client] failed to collect actions: {e}")
            return {}

    async def register_actions(self, actions_schema: dict):
        """Register integration actions with Neuro backend."""
        payload = {
            "op": "register_actions",
            "actions": actions_schema
        }
        print("[Nakurity Client] Registering actions schema with Neuro...")
        await self.send_command_data(json.dumps(payload).encode())

    async def register_environment_context(self):
        """Send context about relay environment and integrations."""
        env_context = {
            "op": "environment_context",
            "relay_name": self.name,
            "relay_description": "Acts as a multiplexed integration relay.",
            "connected_integrations": list(getattr(self.router_forward_cb, "integrations", {}).keys())
            if hasattr(self.router_forward_cb, "integrations") else [],
        }
        await self.send_command_data(json.dumps(env_context).encode())
        print("[Nakurity Client] Sent environment context to Neuro.")

    async def on_connect(self):
        print("[Nakurity Client] connected")

    async def on_disconnect(self):
        print("[Nakurity Client] disconnected")

    async def send_to_neuro(self, command_bytes: bytes):
        """Send formatted neuro command bytes to the real neuro backend."""
        await self.send_command_data(command_bytes)
    
    async def _read_loop(self):
        try:
            while True:
                await self.read_message()
        except websockets.exceptions.ConnectionClosed:
            print("[Nakurity Client] connection closed")
        except Exception as e:
            print("[Nakurity Client] read loop exception:", e)

async def connect_outbound(uri: str, router_forward_cb):
    """
    Connect to the real neuro backend and return NakurityClient instance.
    This function will create a background read loop for the websocket.
    """
    print("[Nakurity Client] trying to connect to the Neuro Backend")
    try:
        ws = await websockets.connect(uri)
    except Exception as e:
        print("[Nakurity Client] failed to connect to:", uri)
        print(e)
        # TODO: implement retry attempts
        return None

    try:
        print("[Nakurity Client] starting connection to neuro backend", uri)
        c = NakurityClient(ws, router_forward_cb)
        await c.initialize()
        # start background read loop
        loop = asyncio.get_event_loop()
        c._reader_task = loop.create_task(c._read_loop())
        return c
    except Exception as e:
        print("[Nakurity Client] has failed during initialize!")
        print(e)
        try:
            await ws.close()
        except Exception:
            pass
        return None
