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
    ActionResultData,
)
from neuro_api.api import NeuroAction

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
        # attach callbacks so intermediary can forward messages into this server
        # keep legacy attr for backwards-compat, but the active hook is forward_to_neuro
        self.intermediary.nakurity_outbound_client = getattr(self.intermediary, "nakurity_outbound_client", None)
        self.intermediary.forward_to_neuro = self._handle_intermediary_forward
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

        # broadcast to connected integrations via Nakurity Backend
        broadcast_msg = json.dumps({"event": "choose_action_request", "payload": ask})
        for name, ws in list(self.clients.items()):
            try:
                await ws.send(broadcast_msg)
                print(f"[Nakurity Backend] sent choose_action_request to {name}")
            except Exception:
                print(f"[Nakurity Backend] failed to send to {name}, removing.")
                self.clients.pop(name, None)

        # provide a simple async requester: wait for integration choice responses
        fut = asyncio.get_event_loop().create_future()

        async def waiter():
            try:
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
            # Check both payload["action"] and msg["action"] for compatibility
            if "action" in payload or "action" in msg:
                action_name = payload.get("action") or msg.get("action")
                action_id = msg.get("id")  # Action ID for response tracking
                data = payload.get("data", {}) or msg.get("data", {})
                integration_name = self._resolve_integration_for_action(action_name)
                
                print(f"[Nakurity Backend] routing action '{action_name}' (id={action_id}) to integration '{integration_name}'")
                
                if integration_name:
                    # Send action execution to the target integration's websocket
                    ws = self.clients.get(integration_name)
                    #print("[SERVER_DEBUG] CLIENTS INFO: LEN_CLIENTS:", len(self.clients))
                    #print("[SERVER_DEBUG] CLIENTS INFO: FIRST CLIENT:", self.clients)
                    if ws:
                        try:
                            action_msg = {
                                    "command": "action",
                                    "data": {
                                            "id": action_id,
                                            "name": action_name,
                                            "data": "string" #TODO - REPLACE WITH ACTUAL DATA
                                    }
                            }

                            print("[Nakurity Backend] ... sending action msg to integration:", action_msg)

                            await ws.send(json.dumps(action_msg).encode())

                            print(f"[Nakurity Backend] sent action to {integration_name}")
                            #print("[Nakurity Backend] by websocket", ws)
                            
                            recev = await self._recv_q.get() #filled in run_server()

                            print("[Nakurity Backend] ... RESPONSE FROM INTEGRATION: ", recev)
                            return recev
                        except Exception as e:
                            print(f"[Nakurity Backend] failed to send action to {integration_name}: {e}")
                            return {"error": f"failed to send to {integration_name}"}
                    else:
                        print(f"[Nakurity Backend] integration {integration_name} not connected")
                        return {"error": f"integration {integration_name} not connected"}
                else:
                    print(f"[Nakurity Backend] no integration found for action '{action_name}'")
                    return {"error": f"no integration owns action {action_name}"}
                
            # if "from_integration" in msg:
            #     origin = msg["from_integration"]

            #     if origin in self.clients:
            #         await self.intermediary.

            # push message into read queue
            await self._recv_q.put(json.dumps(payload))
            return {"accepted": True, "echo": payload}
        except Exception:
            traceback.print_exc()
            return {"error": "forward handling failed"}
        
    def _resolve_integration_for_action(self, action_name: str) -> Optional[str]:
        """Determine which integration owns the given action name."""
        if "." in action_name:
            return action_name.split(".")[0]
        else:
            for integration, actions in self.intermediary.action_registry.items():
                for action in actions:
                    if action_name in action.get("name", ""):
                        print(True)
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

                    #print("[SERVER_DEBUG] GOT SOMETHING FROM WEBSOCKET:", data)

                    # If message contains 'game' field (client messages), capture name
                    client_name = data.get("game") or client_name or "unknown-client"
                    # store mapping for later sends
                    if client_name not in self.clients:
                        self.clients[client_name] = websocket
                        await self.intermediary._notify_watchers({
                            "event": "integration_connected_via_backend",
                            "name": client_name
                        })

                    # Forward all messages to Intermediary → Nakurity Client → Neuro Backend (pure relay)
                    try:
                        # Check if this is an action registration (Neuro SDK format: {"command": "actions/register", "data": {"actions": [...]}})
                        if data.get("command") == "actions/register":
                            print(f"[Nakurity Backend] {client_name} registering actions")
                            actions_schema = data.get("data", {}).get("actions", [])
                            
                            # Store locally in intermediary for multiplexing
                            self.intermediary.action_registry[client_name] = actions_schema
                            
                            # Forward to real Neuro backend via NakurityLink
                            if hasattr(self.intermediary, "nakurity_outbound_client") and self.intermediary.nakurity_outbound_client:
                                await self.intermediary.nakurity_outbound_client.register_actions({
                                    "actions": actions_schema


                                })
                                print(f"[Nakurity Backend] forwarded {client_name} actions to Neuro backend")
                            
                            # Don't send response - Neuro SDK expects transparent relay
                            pass
                        else:
                            if data.get("command") == "action/result": 
                                await self._recv_q.put(data) #put it in queue so action handler will see it
                            else:
                                # Forward regular messages through Intermediary → Nakurity Client → Neuro Backend
                                print(f"[Nakurity Backend] forwarding {client_name} message to Neuro backend via Intermediary")
                                
                                # Send directly to NakurityLink for forwarding to real Neuro backend
                                if hasattr(self.intermediary, "nakurity_outbound_client") and self.intermediary.nakurity_outbound_client:
                                    await self.intermediary.nakurity_outbound_client.send_event({
                                        "event": "integration_message", 
                                        "from": client_name,
                                        "data": data
                                    })
                                else:
                                    print(f"[Nakurity Backend] WARNING: neuro backend not available, message dropped")
                        # Note: Do NOT send acknowledgment responses - the relay is transparent.
                        # The real Neuro backend will send responses/commands back through the relay.
                    except Exception as e:
                        traceback.print_exc()
                        try:
                            await websocket.send(json.dumps({"error": "failed to forward to relay"}))
                        except (websockets.exceptions.ConnectionClosed, websockets.exceptions.ConnectionClosedOK):
                            print(f"[Nakurity Backend] Client {client_name} disconnected during error response")
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