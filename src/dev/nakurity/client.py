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
        self.name = "development-relay"
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
        # Send required startup to set game/title on backend
        await self.send_startup_command()
        # Send development environment context
        await self.register_environment_context()
        # Optional steps disabled to avoid schema mismatches with dev backends
        # actions_schema = await self.collect_registered_actions()
        # if actions_schema:
        #     await self.register_actions(actions_schema)

    async def handle_action(self, action: NeuroAction):
        # Actions from real Neuro backend flow back to intermediary → integrations
        print(f"[Nakurity Client] received action from Neuro: {action.name}")
        await self.router_forward_cb({
            "from_neuro_backend": True,
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
        # Convert actions_schema dict to proper Action format
        actions_list = []
        for action_name, action_info in actions_schema.items():
            if isinstance(action_info, dict):
                action = {
                    "name": action_name,
                    "description": action_info.get("description", f"Action: {action_name}"),
                    "schema": action_info.get("schema")
                }
            else:
                # Simple string description
                action = {
                    "name": action_name,
                    "description": str(action_info) if action_info else f"Action: {action_name}",
                    "schema": None
                }
            actions_list.append(action)
        
        if not actions_list:
            print("[Nakurity Client] No actions to register")
            return
            
        payload = {
            "command": "actions/register",
            "game": self.name,
            "data": {"actions": actions_list}
        }
        print(f"[Nakurity Client] Registering {len(actions_list)} actions with Neuro backend")
        await self.send_command_data(json.dumps(payload).encode())

    async def register_environment_context(self):
        """Send comprehensive context about the development environment to help Neuro understand this isn't a game."""
        
        # Initial development environment explanation
        context_messages = [
            {
                "command": "context",
                "game": self.name,
                "data": {
                    "message": "🔧 DEVELOPMENT ENVIRONMENT ACTIVE: This is not a game integration. You are connected to a development relay system that allows testing and debugging of multiple game integrations simultaneously. This relay acts as a bridge between your Neuro backend and various development integrations.",
                    "silent": False
                }
            },
            {
                "command": "context", 
                "game": self.name,
                "data": {
                    "message": "🎮 INTEGRATION TESTING MODE: When you receive commands from this relay, they are coming from developers testing their game integrations. You may see test commands, mock data, or experimental features. Please respond as you normally would to help validate the integration behavior.",
                    "silent": False
                }
            },
            {
                "command": "context",
                "game": self.name, 
                "data": {
                    "message": "⚡ RELAY FUNCTIONALITY: This development relay can forward actions between multiple connected integrations and your backend. If you choose actions or respond to events, they will be routed to the appropriate integration for testing purposes.",
                    "silent": False
                }
            },
            {
                "command": "context",
                "game": self.name,
                "data": {
                    "message": "🛠️ DEVELOPER NOTE: This connection helps developers ensure their integrations work correctly with your systems before going live. Your responses help validate proper communication flows and action handling.",
                    "silent": True
                }
            }
        ]
        
        # Send each context message quickly for testing
        for i, context in enumerate(context_messages):
            await self.send_command_data(json.dumps(context).encode())
            print(f"[Nakurity Client] Sent development context message {i+1}/{len(context_messages)}")
            # Very small delay only to prevent overwhelming the connection
            if i < len(context_messages) - 1:
                await asyncio.sleep(0.05)
        
        # Send a final completion signal
        completion_payload = {
            "command": "context",
            "game": self.name,
            "data": {
                "message": "✅ DEV: Development relay context setup complete - ready for integration testing",
                "silent": True
            }
        }
        await self.send_command_data(json.dumps(completion_payload).encode())
        print("[Nakurity Client] Completed development environment context setup.")

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
            # Signal that reconnection is needed
            if hasattr(self, '_reconnect_callback'):
                asyncio.create_task(self._reconnect_callback())
        except Exception as e:
            print("[Nakurity Client] read loop exception:", e)
            # Signal that reconnection is needed
            if hasattr(self, '_reconnect_callback'):
                asyncio.create_task(self._reconnect_callback())

async def connect_outbound(uri: str, router_forward_cb, max_retries: int = 10, retry_delay: float = 2.0):
    """
    Connect to the real neuro backend and return NakurityClient instance with retry logic.
    This function will create a background read loop for the websocket.
    """
    for attempt in range(max_retries):
        print(f"[Nakurity Client] trying to connect to Neuro Backend (attempt {attempt + 1}/{max_retries})")
        try:
            ws = await websockets.connect(uri)
            print(f"[Nakurity Client] successfully connected to {uri}")
            break
        except Exception as e:
            if attempt == max_retries - 1:
                print(f"[Nakurity Client] failed to connect after {max_retries} attempts: {uri}")
                print(f"[Nakurity Client] final error: {e}")
                return None
            else:
                delay = retry_delay * (2 ** min(attempt, 6))  # Exponential backoff, max 128s
                print(f"[Nakurity Client] connection failed: {e}")
                print(f"[Nakurity Client] retrying in {delay:.1f}s...")
                await asyncio.sleep(delay)

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
