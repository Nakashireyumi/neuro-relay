# src/dev/nakurity/intermediary.py
import asyncio
import json
import traceback
from typing import Dict, Any, Optional

import websockets
from websockets.server import WebSocketServerProtocol
from ..utils.loadconfig import load_config

"""
Intermediary WebSocket server:
- Accepts connections from Neuro-OS (type="neuro-os") and other integrations (type="integration")
- First message must be registration JSON:
  {"type": "neuro-os" | "integration", "name": "<human-name>"}
- Subsequent messages are routed as JSON blobs. Binary payloads should be base64-encoded by clients.
"""

import pickle
from pathlib import Path

cfg = load_config()

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765

QUEUE_FILE = Path(cfg.get("intermediary", {}).get("relay_queue", "relay_message_queue.pkl"))
AUTH_TOKEN = cfg.get("intermediary", {}).get("auth_token", "super-secret-token")

class Intermediary:
    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT):
        self.host = host
        self.port = port
        # name -> websocket for integrations
        self.integrations: Dict[str, WebSocketServerProtocol] = {}
        # neuro-os watchers (could be multiple monitoring UIs)
        self.watchers: Dict[str, WebSocketServerProtocol] = {}

        # internal routing hooks (can be replaced by Neuro-OS)
        # `on_forward_to_neuro` should be an async callable taking (payload: dict) -> optional response
        self.on_forward_to_neuro = None  # set by server layer

        # persistent queues, help manage the chokepoint where all integration messages
        # goes pass neuro-relay. It is a relay integration after all.
        self.queue = asyncio.Queue()
        self._load_persisted_queue()

        # waits until other asyncio tasks are ready
        self._ready_event: Optional[asyncio.Event] = None

    def _load_persisted_queue(self):
        if QUEUE_FILE.exists():
            try:
                items = pickle.loads(QUEUE_FILE.read_bytes())
                for item in items:
                    self.queue.put_nowait(item)
                print(f"[Intermediary] Restored {len(items)} queued messages.")
            except Exception:
                print("[Intermediary] Failed to load persisted queue.")

    async def persist_queue(self):
        items = []
        qcopy = asyncio.Queue()
        while not self.queue.empty():
            item = await self.queue.get()
            items.append(item)
            qcopy.put_nowait(item)
        self.queue = qcopy
        QUEUE_FILE.write_bytes(pickle.dumps(items))

    async def _register(self, ws: WebSocketServerProtocol) -> Optional[Dict[str, Any]]:
        raw = await ws.recv()
        try:
            meta = json.loads(raw)
        except Exception:
            await ws.send(json.dumps({"error": "registration must be JSON"}))
            return None

        token = meta.get("auth_token")
        if token != AUTH_TOKEN:
            await ws.send(json.dumps({"error": "invalid auth token"}))
            await ws.close()
            return None

        typ = meta.get("type")
        name = meta.get("name", "unknown")

        if typ == "integration":
            self.integrations[name] = ws
            await self._notify_watchers({
                "event": "integration_connected",
                "name": name
            })
            return {"type": "integration", "name": name}
        elif typ == "neuro-os":
            self.watchers[name] = ws
            await self._notify_watchers({
                "event": "neuroos_connected",
                "name": name
            })
            return {"type": "neuro-os", "name": name}
        else:
            await ws.send(json.dumps({"error": "unknown registration type"}))
            return None

    async def _notify_watchers(self, message: dict):
        data = json.dumps(message)
        for name, w in list(self.watchers.items()):
            try:
                await w.send(data)
            except Exception:
                # watcher likely disconnected
                self.watchers.pop(name, None)

    async def _handle_integration_msg(self, origin_name: str, ws: WebSocketServerProtocol):
        """
        Integration -> Relay
        Integration sends JSON or plain text messages. We forward to Neuro via on_forward_to_neuro if present.
        Expected integration message shape:
            {"action":"name", "params": {...}, "meta": {...}}
        """
        async for raw in ws:
            # handle binary frames (e.g., file uploads)
            if isinstance(raw, (bytes, bytearray)):
                filename = f"upload_{origin_name}.bin"
                with open(filename, "wb") as f:
                    f.write(raw)
                print(f"[Relay:{origin_name}] received binary frame ({len(raw)} bytes)")
                await self._notify_watchers({
                    "event": "binary_received",
                    "from": origin_name,
                    "size": len(raw),
                    "file": filename
                })
                continue

            try:
                payload = json.loads(raw)
            except Exception:
                # treat as raw text
                payload = {"action": "raw_text", "raw": raw}
            # notify watchers
            await self._notify_watchers({
                "event": "integration_message",
                "from": origin_name,
                "payload": payload
            })

            # If the relay layer has a callback to forward to Neuro, call it and send back result
            if callable(self.on_forward_to_neuro):
                try:
                    resp = await self.on_forward_to_neuro({
                        "from_integration": origin_name,
                        "payload": payload
                    })
                    # return result to integration if something returned
                    if resp is not None:
                        await ws.send(json.dumps({"result": resp}))
                except Exception:
                    traceback.print_exc()
                    await ws.send(json.dumps({"error": "relay->neuro forward failed"}))

    async def _handle_watcher_msg(self, watcher_name: str, ws: WebSocketServerProtocol):
        """
        Watcher (Neuro-OS) -> Relay
        Watchers may send commands to integrations through the relay:
          {"target":"spotify", "cmd":{"action":"play","params":{...}}}
        """
        async for raw in ws:
            try:
                payload = json.loads(raw)
            except Exception:
                await ws.send(json.dumps({"error": "watcher messages must be JSON"}))
                continue

            target = payload.get("target")
            cmd = payload.get("cmd")
            if target and cmd and target in self.integrations:
                try:
                    await self.integrations[target].send(json.dumps({
                        "from_watcher": watcher_name,
                        "cmd": cmd
                    }))
                    await ws.send(json.dumps({"status": "sent"}))
                except Exception:
                    await ws.send(json.dumps({"error": "failed to deliver to integration"}))
            else:
                await ws.send(json.dumps({"error": "invalid target/cmd"}))

    async def _handler(self, ws: WebSocketServerProtocol):
        reg = await self._register(ws)
        if not reg:
            # registration failed; close
            await ws.close()
            return

        typ = reg["type"]
        name = reg["name"]

        try:
            if typ == "integration":
                await self._handle_integration_msg(name, ws)
            else:
                await self._handle_watcher_msg(name, ws)
        except websockets.ConnectionClosed:
            pass
        finally:
            # cleanup
            if typ == "integration":
                self.integrations.pop(name, None)
                await self._notify_watchers({"event": "integration_disconnected", "name": name})
            else:
                self.watchers.pop(name, None)
                await self._notify_watchers({"event": "neuroos_disconnected", "name": name})

    async def start(self):
        """
        Start the intermediary WebSocket server and set an internal 'ready' event when bound.
        """
        asyncio.create_task(self.retry_queue())

        # ensure we have an event users can await to know when server is ready
        self._ready_event = asyncio.Event()

        try:
            # start the server explicitly (not using `async with` so we can set ready event immediately)
            server = await websockets.serve(self._handler, self.host, self.port)
            # websockets.serve returns a Serve object and it has .wait_closed(); the server is now bound
            print(f"[Intermediary] listening on ws://{self.host}:{self.port}")
            self._ready_event.set()

            # keep serving until server is closed
            asyncio.create_task(server.wait_closed())
        except OSError as e:
            print(f"[Intermediary] Failed to start on ws://{self.host}:{self.port} -> {e}")
            self._ready_event.set()  # unblock waiters even if failed
            raise
        except Exception as e:
            print(f"[Intermediary] Unexpected error: {e}")
            traceback.print_exc()
            self._ready_event.set()
            raise

        

    # Helpers to send messages to integrations from the server layer
    async def send_to_integration(self, name: str, payload: dict):
        ws = self.integrations.get(name)
        if not ws:
            # queue if not connected
            await self.queue.put((name, payload))
            await self.persist_queue()
            print(f"[Intermediary] Queued message for {name}")
            return
        await ws.send(json.dumps(payload))

    async def retry_queue(self):
        """Periodically retry sending queued messages."""
        while True:
            if not self.queue.empty():
                name, payload = await self.queue.get()
                if name in self.integrations:
                    try:
                        await self.integrations[name].send(json.dumps(payload))
                        print(f"[Intermediary] Resent queued message to {name}")
                    except Exception:
                        # put back for later
                        await self.queue.put((name, payload))
                else:
                    await self.queue.put((name, payload))
            await asyncio.sleep(5)

    async def broadcast(self, payload: dict):
        for name, ws in list(self.integrations.items()):
            try:
                await ws.send(json.dumps(payload))
            except Exception:
                self.integrations.pop(name, None)

    async def wait_until_ready(self, timeout: float = 5.0):
        """Await until the server is ready (bounded) or raise on timeout."""
        if self._ready_event is None:
            # start() hasn't been called yet; immediate return or create+wait might be used
            self._ready_event = asyncio.Event()
        await asyncio.wait_for(self._ready_event.wait(), timeout=timeout)
