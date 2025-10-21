# src/dev/nakurity/link_server.py
import asyncio
import uuid
from .client import NakurityClient
from neuro_api.server import RegisterActionsData

class NakurityLink:
    def __init__(self, nakurity_client: NakurityClient | None):
        self.traffic = asyncio.Queue()
        self._bg_task = None
        self.nakurity_client = nakurity_client

    async def register_actions(self, data: dict):
        actions = data.get("actions", {})
        traffic_id = uuid.uuid4()
        await self.traffic.put({
            "traffic_id": str(traffic_id),
            "type": "register_actions",
            "payload": actions
        })

    async def send_event(self, data: dict):
        event = data.get("event")
        payload = data.get("data", {})
        from_integration = data.get("from", "unknown")
        traffic_id = uuid.uuid4()
        await self.traffic.put({
            "traffic_id": str(traffic_id),
            "type": "event",
            "event": event,
            "from": from_integration,
            "payload": payload
        })

    # ---------------------- #
    #   Background Handling  #
    # ---------------------- #
    async def _handle_traffic(self):
        """Continuously handle traffic items queued by API routes."""
        while True:
            item = await self.traffic.get()
            try:
                print(f"[Nakurity Link] Processing traffic item {item['traffic_id']} ({item['type']})")
                if not self.nakurity_client:
                    # no outbound client yet; requeue and wait a bit  
                    print(f"[Nakurity Link] No client available, requeuing traffic item {item['traffic_id']}")
                    await asyncio.sleep(2.0)  # Increased delay
                    await self.traffic.put(item)
                    continue

                if item["type"] == "register_actions":
                    # forward action schema to real Neuro via NakurityClient
                    await self.nakurity_client.register_actions(item.get("payload", {}))
                elif item["type"] == "event":
                    # wrap generic integration event and forward to Neuro via context command
                    event_type = item.get("event")
                    payload_data = item.get("payload", {})
                    from_integration = item.get("from", "unknown")
                    
                    # Create a context message that Neuro can understand
                    if event_type == "integration_message":
                        # For integration messages, extract the actual command/action
                        if payload_data.get("op") == "choose_force_action":
                            # Convert choose_force_action to actions/force format
                            actions = payload_data.get("actions", [])
                            action_names = [a.get("name", "") for a in actions if "name" in a]
                            payload = {
                                "command": "actions/force",
                                "game": self.nakurity_client.name,
                                "data": {
                                    "state": __import__("json").dumps(payload_data.get("state", {})),
                                    "query": payload_data.get("query", "Choose an action"),
                                    "action_names": action_names,
                                    "ephemeral_context": bool(payload_data.get("ephemeral_context"))
                                }
                            }
                        else:
                            # For other integration messages, send as context with better formatting
                            if payload_data.get("command") == "startup":
                                game_title = payload_data.get("game", "unknown-game")
                                message = f"🎮 Integration '{from_integration}' connected with game '{game_title}'"
                            elif payload_data.get("status") == "ready":
                                game_title = payload_data.get("game", from_integration)
                                message = f"✅ Integration '{game_title}' is ready via relay"
                            else:
                                message = f"📨 Message from integration '{from_integration}': {__import__('json').dumps(payload_data)}"
                            
                            payload = {
                                "command": "context",
                                "game": self.nakurity_client.name,
                                "data": {
                                    "message": message,
                                    "silent": True  # Keep integration messages quieter
                                }
                            }
                    else:
                        # For other event types, send as context with better formatting
                        if event_type == "integration_connected":
                            message = f"🔌 Integration '{from_integration}' connected to relay"
                        elif event_type == "integration_disconnected":
                            message = f"🔌 Integration '{from_integration}' disconnected from relay"
                        elif event_type == "action_test":
                            message = f"🧪 Testing action '{payload_data.get('action', 'unknown')}' from integration '{from_integration}'"
                        else:
                            message = f"📡 Event '{event_type}' from integration '{from_integration}': {__import__('json').dumps(payload_data)}"
                        
                        payload = {
                            "command": "context",
                            "game": self.nakurity_client.name,
                            "data": {
                                "message": message,
                                "silent": True  # Keep events quieter in production
                            }
                        }
                    
                    await self.nakurity_client.send_command_data(
                        __import__("json").dumps(payload).encode()
                    )
                await asyncio.sleep(0)  # yield control
            except Exception as e:
                print(f"[Nakurity Link] error handling traffic item {item.get('traffic_id', 'unknown')}:", e)
                # For critical errors, we might want to requeue the item
                if "connection" in str(e).lower() or "websocket" in str(e).lower():
                    print(f"[Nakurity Link] Connection error detected, requeuing item {item.get('traffic_id', 'unknown')}")
                    await asyncio.sleep(1.0)
                    await self.traffic.put(item)
            finally:
                self.traffic.task_done()

    # ---------------------- #
    #     Server Control     #
    # ---------------------- #
    async def start(self):
        # spawn background processor
        self._bg_task = asyncio.create_task(self._handle_traffic())
        print("[Nakurity Link] started")

    async def stop(self):
        if self._bg_task:
            self._bg_task.cancel()
            try:
                await self._bg_task
            except asyncio.CancelledError:
                pass
        print("[Nakurity Link] stopped.")
