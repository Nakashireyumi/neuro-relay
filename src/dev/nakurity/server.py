# src/dev/nakurity/server.py
import asyncio
import json
import traceback
from typing import Optional, Tuple
from ssl import SSLContext
import websockets

# these imports come from the neuro-api package
from neuro_api.server import (
    AbstractRecordingNeuroServerClient,
    AbstractHandlerNeuroServerClient,
    AbstractNeuroServerClient,
)
from neuro_api.command import Action

from .intermediary import Intermediary

"""
Nakurity Backend acts like a Neuro backend. When Neuro-sama (or the SDK client)
connects and asks the server to choose an action, this server asks the intermediary
(Neuro-OS / integrations) for input or forwards contexts.

You must adapt method names to the exact neuro-api version; this matches the docs you shared.
"""

class NakurityBackend(
        AbstractRecordingNeuroServerClient,
        AbstractHandlerNeuroServerClient,
        AbstractNeuroServerClient,
    ):
    def __init__(self, intermediary: Intermediary):
        super().__init__()
        self.intermediary = intermediary
        # attach a callback so intermediary can call into this server
        self.intermediary.nakurity_outbound_client = self._handle_intermediary_forward
        # Inbound queue
        self._recv_q = asyncio.Queue()
        # Neuro Integration Clients list
        self.clients: dict[str, websockets.WebSocketServerProtocol] = {} 
        print("[Nakurity Backend] has initialized.")

    async def read_from_websocket(self) -> str:
        print("[Nakurity Backend (Websocket)] reading from websocket")
        # Block until someone pushes data into _recv_q (eg: integration forwarded a payload)
        data = await self._recv_q.get()
        return data

    async def write_to_websocket(self, data: str):
        print("[Nakurity Backend (Websocket)] writing to websocket")
        # data is raw string JSON. Broadcast to all connected integrations and to intermediary watchers.
        # Also allow NakurityClient (outbound) to send to real Neuro if configured (handled elsewhere).
        # Send to intermediary watchers for visibility:
        await self.intermediary._notify_watchers({
            "event": "backend_message",
            "payload": data
        })
        # attempt to send to all connected clients (they may be local neuro SDK integrations)
        for name, ws in list(self.clients.items()):
            try:
                await ws.send(data)
            except Exception:
                print(f"[Nakurity Backend] failed to send to {name}, removing.")
                self.clients.pop(name, None)

    def submit_call_async_soon(self, cb, *args):
        print("[Nakurity Backend (Calls)] submmiting async calls")
        loop = asyncio.get_event_loop()
        try:
            loop.call_soon(cb, *args)
        except Exception:
            loop.call_soon_threadsafe(cb, *args)

    # Called by the neuro-api when it wants to add ephemeral context
    def add_context(self, game_title: str, message: str, reply_if_not_busy: bool):
        print("[Nakurity Backend] Received add_context command")
        # broadcast message to all watchers (neuro-os)
        coro = self.intermediary._notify_watchers({
            "event": "add_context",
            "game_title": game_title,
            "message": message,
            "reply_if_not_busy": reply_if_not_busy
        })
        # not awaiting on purpose; it's fire and forget
        asyncio.create_task(coro)

    # Example callback: Neuro asks server to pick action from list of actions.
    # We forward the query to Neuro-OS and wait for a short response.
    async def choose_force_action(self, game_title, state, query,
                                  ephemeral_context, actions) -> Tuple[str, str]:
        """
        - game_title/state/query/ephemeral_context: as provided by neuro-api
        - actions: list of Action objects
        We will ask Neuro-OS via intermediary for a choice. Wait up to timeout seconds.
        """
        print("[Nakurity Backend] received forced_action command")
        # create simplified actions list to send
        simple_actions = [{"name": a.name, "desc": getattr(a, "desc", "")} for a in actions]
        ask = {
            "type": "choose_action_request",
            "game_title": game_title,
            "state": state,
            "query": query,
            "ephemeral_context": ephemeral_context,
            "actions": simple_actions,
        }

        # notify watchers (so Neuro-OS UI shows the request)
        await self.intermediary._notify_watchers({"event": "choose_action", "payload": ask})

        # provide a simple async requester: intermediary.on_forward_to_neuro returns responses from integrations
        # here we broadcast a request to all integrations and wait for the first valid reply
        fut = asyncio.get_event_loop().create_future()

        async def waiter():
            try:
                # send the choice request to all integrations
                await self.intermediary.broadcast({"event": "choose_action_request", "payload": ask})
                # we then wait for any integration to send back a response via the intermediary.on_forward_to_neuro pathway
                
                # wait for up to 8 seconds for a response
                resp = await asyncio.wait_for(self._wait_for_integration_choice(), timeout=8.0)
                fut.set_result(resp)
            except asyncio.TimeoutError:
                fut.set_result(None)

        # start waiting
        asyncio.create_task(waiter())
        resp = await fut

        if resp and isinstance(resp, dict):
            # expect {selected_action_name: "name", "data": "<json-string-or-dict>"}
            name = resp.get("selected")
            data = resp.get("data", "{}")
            if isinstance(data, dict):
                data = json.dumps(data)
            return name, data

        # fallback: choose first action
        fallback_name = actions[0].name
        return fallback_name, "{}"

    # internal helper: create an awaitable that gets fulfilled when an integration posts a choice
    async def _wait_for_integration_choice(self):
        # naive implementation: watch a queue or temporary file for the first "choice" event.
        # For skeleton, we'll create a simple queue attribute the intermediary will use.
        if not hasattr(self, "_choice_q"):
            self._choice_q = asyncio.Queue()
        return await self._choice_q.get()
    
    def handle_startup(self, game_title, integration_name = "Undefined Integration"):
        return self._handle_intermediary_forward(
            {
                "from_integration": integration_name,
                "payload": {
                    "game_title": game_title,
                    "metadata": {
                        "notes":
                            """This is for montioring by Neuro-OS and is not called anywhere else.
                            (The Neuro Backend gets the relay client's name)"""
                    }
                }
            }
        )

    # this method will be called by intermediary when integration messages arrive
    async def _handle_intermediary_forward(self, msg: dict):
        """
        msg: {"from_integration": "<name>", "payload": {...}}
        If payload contains a 'choice' event, put it on the queue; otherwise, return None or an ack.
        """
        print(f"[Nakurity Backend] received forwarded msg from intermediary: {msg!r}")
        try:
            payload = msg.get("payload", {})
            # if integration replies with { "choice": {...} } we'll route it to the waiting chooser
            if "choice" in payload:
                choice = payload["choice"]
                if not hasattr(self, "_choice_q"):
                    self._choice_q = asyncio.Queue()
                await self._choice_q.put(choice)
                return {"accepted": True}
            elif msg.get("query") == "get_registered_actions":
                return {"actions": await self.intermediary.collect_registered_actions()}
            
            # Neuro chose an action — find correct integration
            if "action" in payload:
                action_name = payload["action"]
                data = payload.get("data", {})
                integration_name = self._resolve_integration_for_action(action_name)
                if integration_name:
                    await self.intermediary.send_to_integration(integration_name, {
                        "event": "execute_action",
                        "action": action_name,
                        "params": data
                    })
                    return {"forwarded_to": integration_name}
                
            if "from_integration" in msg:
                origin = msg["from_integration"]

                if origin in self.clients:
                    await self.send_to_neuro_client(origin, payload)

            # push message into read queue
            await self._recv_q.put(json.dumps(payload))
            return {"accepted": True, "echo": payload}
        except Exception:
            traceback.print_exc()
            return {"error": "forward handling failed"}
        
    async def send_to_neuro_client(self, client_name: str, message: dict):
        self.intermediary.nakurity_outbound_client()
        
    def _resolve_integration_for_action(self, action_name: str) -> Optional[str]:
        """Determine which integration owns the given action name."""
        if "." in action_name:
            return action_name.split(".")[0]
        for integration, actions in self.intermediary.action_registry.items():
            if action_name in actions:
                return integration
        return None
        
    async def send_to_connected_client(self, game_title: str, command_bytes: bytes):
        """Optional helper to send server->client command to a specific local client."""
        ws = self.clients.get(game_title)
        if not ws:
            print(f"[Nakurity Backend] no client {game_title} connected")
            return False
        try:
            await ws.send(command_bytes.decode("utf-8"))
            return True
        except Exception:
            print(f"[Nakurity Backend] failed to send to {game_title}")
            self.clients.pop(game_title, None)
            return False

    # run the neuro-api server
    async def run_server(self, host="127.0.0.1", port=8000, ssl_context: Optional[SSLContext] = None):
        """
        Run a websocket server that accepts Neuro SDK client integrations.
        Each client is expected to follow the Neuro SDK spec (send JSON commands).
        Messages from connected clients are forwarded into the Intermediary via on_forward_to_neuro.
        """

        print(f"[Nakurity Backend] Starting websocket server on ws://{host}:{port}")

        async def handler(websocket):
            # for the Neuro SDK client, we expect the client to send startup with "game" name,
            # but since protocols vary, we peek at the first message to get the game/title.
            client_name = None
            try:
                async for raw in websocket:
                    try:
                        data = json.loads(raw)
                    except Exception:
                        # non-json, make a wrapper
                        data = {"raw": raw}

                    # If message contains 'game' field (client messages), capture name
                    client_name = data.get("game") or client_name or "unknown-client"
                    # store mapping for later sends
                    if client_name not in self.clients:
                        self.clients[client_name] = websocket
                        await self.intermediary._notify_watchers({
                            "event": "integration_connected_via_backend",
                            "name": client_name
                        })

                    # forward to intermediary using the same shape used in other paths:
                    fwd = {"from_integration": client_name, "payload": data}
                    try:
                        resp = await self.intermediary.nakurity_outbound_client(fwd) if callable(self.intermediary.nakurity_outbound_client) else None
                        # send ack back to client if intermediary returned something
                        if resp is not None:
                            await websocket.send(json.dumps({"result": resp}))
                    except Exception:
                        traceback.print_exc()
                        await websocket.send(json.dumps({"error": "failed to forward to relay"}))
            except websockets.ConnectionClosed:
                pass
            except Exception:
                traceback.print_exc()
            finally:
                if client_name:
                    self.clients.pop(client_name, None)
                    await self.intermediary._notify_watchers({
                        "event": "integration_disconnected_via_backend",
                        "name": client_name
                    })

        try:
            async with websockets.serve(handler, host, port, ssl=ssl_context):
                print("[Nakurity Backend] WebSocket server started.")
                await asyncio.Future()  # run forever
        except Exception as exc:
            print(f"[Nakurity Backend] Failed to start websocket server:\n{exc}")
            raise