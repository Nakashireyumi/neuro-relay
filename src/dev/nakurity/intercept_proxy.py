# src/dev/nakurity/intercept_proxy.py
import asyncio
import json
from dataclasses import dataclass, field
from typing import List, Optional

import websockets

from ..utils.loadconfig import load_config


data_default_match = ["startup", "actions/register", "context"]


@dataclass
class InterceptProxyConfig:
    listen_host: str = "127.0.0.1"
    listen_port: int = 8767
    upstream_url: str = "ws://127.0.0.1:8000"
    match_commands: List[str] = field(default_factory=lambda: data_default_match)
    # If provided, the proxy will connect to the Intermediary as an integration and
    # emit events so Neuro-OS watchers can see them.
    intermediary_host: Optional[str] = "127.0.0.1"
    intermediary_port: Optional[int] = 8765
    intermediary_auth_token: Optional[str] = None
    intermediary_integration_name: str = "intercept-proxy"


class InterceptProxy:
    """
    A transparent plaintext WebSocket proxy that sits between a Neuro integration
    and the real Neuro backend (ws://). It inspects client->backend JSON messages
    for specific "command" values and announces events to the Neuro Relay Intermediary
    so that Neuro-OS watchers can be notified when an integration has connected or sent
    a particular command.

    Usage pattern:
      Integration -> ws://listen_host:listen_port -> [InterceptProxy] -> upstream_url (real backend)
    """

    def __init__(self, cfg: InterceptProxyConfig):
        self.cfg = cfg
        self._relay_ws: Optional[websockets.WebSocketClientProtocol] = None
        self._relay_lock = asyncio.Lock()
        self._server = None

    async def start(self):
        # Start background connection to Intermediary (optional)
        asyncio.create_task(self._ensure_intermediary_connected())
        # Start proxy server
        self._server = await websockets.serve(self._handle_client, self.cfg.listen_host, self.cfg.listen_port)
        print(f"[InterceptProxy] listening on ws://{self.cfg.listen_host}:{self.cfg.listen_port} -> {self.cfg.upstream_url}")
        await self._server.wait_closed()

    async def _ensure_intermediary_connected(self):
        if not self.cfg.intermediary_host or not self.cfg.intermediary_port:
            return
        while True:
            try:
                uri = f"ws://{self.cfg.intermediary_host}:{self.cfg.intermediary_port}"
                print(f"[InterceptProxy] connecting to Intermediary at {uri}...")
                ws = await websockets.connect(uri)
                await ws.send(json.dumps({
                    "type": "integration",
                    "name": self.cfg.intermediary_integration_name,
                    "auth_token": self.cfg.intermediary_auth_token or "super-secret-token",
                }))
                async with self._relay_lock:
                    self._relay_ws = ws
                print("[InterceptProxy] connected to Intermediary (as integration '", self.cfg.intermediary_integration_name, "')")
                # Keep this connection alive until closed
                async for _ in ws:
                    pass
            except Exception as e:
                print(f"[InterceptProxy] Intermediary connection error: {e}")
            finally:
                async with self._relay_lock:
                    self._relay_ws = None
                await asyncio.sleep(2.0)

    async def _broadcast(self, payload: dict):
        # Send a message into Intermediary as if from an integration; the Intermediary will
        # fan out to Neuro-OS watchers via its existing 'integration_message' notifications.
        async with self._relay_lock:
            ws = self._relay_ws
        if not ws:
            return
        try:
            await ws.send(json.dumps(payload))
        except Exception:
            pass

    async def _handle_client(self, client_ws: websockets.WebSocketServerProtocol):
        # Connect upstream to real backend
        try:
            upstream_ws = await websockets.connect(self.cfg.upstream_url)
        except Exception as e:
            print(f"[InterceptProxy] failed to connect upstream {self.cfg.upstream_url}: {e}")
            await client_ws.close()
            return

        client_peer = getattr(client_ws, 'remote_address', None)
        print(f"[InterceptProxy] client connected from {client_peer}")

        async def pump_client_to_upstream():
            async for message in client_ws:
                # Inspect only text frames
                if isinstance(message, (bytes, bytearray)):
                    await upstream_ws.send(message)
                    continue
                try:
                    obj = json.loads(message)
                except Exception:
                    obj = None
                if isinstance(obj, dict):
                    cmd = obj.get("command")
                    if cmd and any(cmd == m for m in self.cfg.match_commands):
                        await self._broadcast({
                            "event": "integration_connected",
                            "via": "intercept-proxy",
                            "details": {
                                "client": str(client_peer),
                                "first_command": cmd,
                                "snippet": obj.get("data") if isinstance(obj.get("data"), (dict, list, str)) else None,
                            }
                        })
                await upstream_ws.send(message)

        async def pump_upstream_to_client():
            async for message in upstream_ws:
                await client_ws.send(message)

        try:
            await asyncio.wait(
                [
                    asyncio.create_task(pump_client_to_upstream()),
                    asyncio.create_task(pump_upstream_to_client()),
                ],
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            await asyncio.gather(
                self._maybe_close(client_ws),
                self._maybe_close(upstream_ws),
                return_exceptions=True,
            )
            await self._broadcast({
                "event": "integration_disconnected",
                "via": "intercept-proxy",
                "details": {"client": str(client_peer)},
            })
            print(f"[InterceptProxy] client disconnected {client_peer}")

    @staticmethod
    async def _maybe_close(ws):
        try:
            await ws.close()
        except Exception:
            pass


def config_from_yaml() -> InterceptProxyConfig:
    cfg = load_config()
    ip_cfg = cfg.get("intercept-proxy", {}) or {}

    intermediary = cfg.get("intermediary", {}) or {}
    dep = (cfg.get("dependency-authentication", {}) or {}).get("neuro-os", {})

    return InterceptProxyConfig(
        listen_host=ip_cfg.get("host", "127.0.0.1"),
        listen_port=int(ip_cfg.get("port", 8767)),
        upstream_url=ip_cfg.get("upstream_url", f"ws://{cfg.get('nakurity-client', {}).get('host', '127.0.0.1')}:{cfg.get('nakurity-client', {}).get('port', 8000)}"),
        match_commands=ip_cfg.get("match_commands", data_default_match),
        intermediary_host=intermediary.get("host", "127.0.0.1"),
        intermediary_port=int(intermediary.get("port", 8765)),
        intermediary_auth_token=intermediary.get("auth_token") or dep.get("auth_token"),
        intermediary_integration_name=ip_cfg.get("integration_name", "intercept-proxy"),
    )