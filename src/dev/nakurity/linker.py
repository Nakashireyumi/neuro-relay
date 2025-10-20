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
        traffic_id = uuid.uuid4()
        await self.traffic.put({
            "traffic_id": str(traffic_id),
            "type": "event",
            "event": event,
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
                    await asyncio.sleep(0.5)
                    await self.traffic.put(item)
                    continue

                if item["type"] == "register_actions":
                    # forward action schema to real Neuro via NakurityClient
                    await self.nakurity_client.register_actions(item.get("payload", {}))
                elif item["type"] == "event":
                    # wrap generic integration event and forward to Neuro
                    payload = {
                        "op": "integration_event",
                        "event": item.get("event"),
                        "payload": item.get("payload")
                    }
                    await self.nakurity_client.send_command_data(
                        __import__("json").dumps(payload).encode()
                    )
                await asyncio.sleep(0)  # yield control
            except Exception as e:
                print("[Nakurity Link] error handling traffic:", e)
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
        print("[Nakurity Link] stopped.")
