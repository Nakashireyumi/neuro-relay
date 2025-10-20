# src/dev/nakurity/link_server.py
import asyncio
import uuid
from .client import NakurityClient
from neuro_api.server import RegisterActionsData

class NakurityLink:
    def __init__(self, nakurity_client: NakurityClient):
        self.traffic = asyncio.Queue()
        self._bg_task = None

        self.nakurity_client = nakurity_client

    async def register_actions(self, data: dict):
        actions = data.get("actions", [])
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
                if item.type == "register_actions":
                    self.nakurity_client.register_actions(
                        RegisterActionsData(
                            item.get("payload", [])
                        )
                    )
                await asyncio.sleep(0.1)  # simulate async handling
            except Exception as e:
                print("[Nakurity Link] error handling traffic:", e)
            finally:
                self.traffic.task_done()

    # ---------------------- #
    #     Server Control     #
    # ---------------------- #
    async def start(self):
        self._bg_task = asyncio.create_task(await self._handle_traffic())
        print("[Nakurity Link] started")

    async def stop(self):
        if self._bg_task:
            self._bg_task.cancel()
        print("[Nakurity Link] stopped.")