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
        self.intermediary.on_forward_to_neuro = self._handle_intermediary_forward
        # Inbound queue
        self._recv_q = asyncio.Queue()        
        print("[Nakurity Backend] has initialized.")

    # -- implement abstract methods required by neuro_api.base classes --
    # These are minimal implementations to satisfy the abstract base classes.
    # Adapt/refine later if neuro_api expects different semantics.

    async def read_from_websocket(self) -> str:
        """
        This fulfills neuro_api's requirement.
        Waits until some data is pushed into _recv_q by intermediary forward events.
        """
        data = await self._recv_q.get()
        return data

    async def write_to_websocket(self, data: str):
        """
        Instead of writing to a single client websocket, we broadcast to all integrations/watchers.
        """
        await self.intermediary.broadcast({
            "event": "backend_message",
            "payload": data
        })

    def submit_call_async_soon(self, cb, *args):
        """
        neuro_api may call this to schedule a callback on the event loop.
        Implement using loop.call_soon to satisfy expectations.
        """
        loop = asyncio.get_event_loop()
        try:
            loop.call_soon(cb, *args)
        except Exception:
            # fallback: call safely inside loop
            loop.call_soon_threadsafe(cb, *args)

    # Called by the neuro-api when it wants to add ephemeral context
    def add_context(self, game_title: str, message: str, reply_if_not_busy: bool):
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
        return self._handle_intermediary_forward({ "from_integration": integration_name, "payload": {  } })

    # this method will be called by intermediary when integration messages arrive
    async def _handle_intermediary_forward(self, msg: dict):
        """
        msg: {"from_integration": "<name>", "payload": {...}}
        If payload contains a 'choice' event, put it on the queue; otherwise, return None or an ack.
        """
        try:
            payload = msg.get("payload", {})
            # if integration replies with { "choice": {...} } we'll route it to the waiting chooser
            if "choice" in payload:
                choice = payload["choice"]
                if not hasattr(self, "_choice_q"):
                    self._choice_q = asyncio.Queue()
                await self._choice_q.put(choice)
                return {"accepted": True}

            # push message into read queue
            await self._recv_q.put(json.dumps(payload))
            return {"accepted": True, "echo": payload}
        except Exception:
            traceback.print_exc()
            return {"error": "forward handling failed"}

    # run the neuro-api server
    async def run_server(self, host="127.0.0.1", port=8000, ssl_context: Optional[SSLContext] = None):
        print(f"[Nakurity Backend] Starting websocket server on ws://{host}:{port}")

        async def handler(websocket):
            try:
                async for message in websocket:
                    data = json.loads(message)
                    response = await self._handle_intermediary_forward(data)
                    await websocket.send(json.dumps(response))
            except Exception:
                traceback.print_exc()

        try:
            async with websockets.serve(handler, host, port, ssl=ssl_context):
                print("[Nakurity Backend] WebSocket server started.")
                await asyncio.Future()  # run forever
        except Exception as exc:
            print(f"[Nakurity Backend] Failed to start websocket server:\n{exc}")
            raise