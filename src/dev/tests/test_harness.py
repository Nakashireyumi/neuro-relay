# src/dev/nakurity/test_harness.py
import asyncio
import json
import websockets

AUTH_TOKEN = "super-secret-token"

async def fake_integration():
    # Connect to Nakurity Backend (not Intermediary) - this is the correct pipeline
    uri = "ws://127.0.0.1:8001"
    async with websockets.connect(uri) as ws:
        # Send a startup message to identify as spotify integration
        await ws.send(json.dumps({
            "game": "spotify",
            "status": "ready"
        }))
        
        # listen for messages from Nakurity Backend
        async for msg in ws:
            try:
                data = json.loads(msg)
                print(f"[Integration] received: {data}")
                
                # Look for choose_action broadcast from Nakurity Backend
                if data.get("event") == "choose_action_request" and "payload" in data:
                    payload = data["payload"]
                    if payload.get("type") == "choose_action_request" and payload.get("actions"):
                        choice = {
                            "choice": {
                                "selected": payload["actions"][0]["name"],
                                "data": {"volume": 50}
                            }
                        }
                        await ws.send(json.dumps(choice))
                        print("[Integration] sent choice")
                        break
            except json.JSONDecodeError:
                print(f"[Integration] received non-JSON: {msg}")

async def fake_watcher():
    uri = "ws://127.0.0.1:8765"
    async with websockets.connect(uri) as ws:
        await ws.send(json.dumps({
            "type": "neuro-os",
            "name": "neuroos",
            "auth_token": AUTH_TOKEN
        }))
        # send a command to spotify after a bit
        await asyncio.sleep(2)
        await ws.send(json.dumps({
            "target": "spotify",
            "cmd": {"action": "ping"}
        }))
        print("[Watcher] sent ping")
        async for msg in ws:
            print("[Watcher] recv:", msg)

async def fake_neuro_request():
    # Connect directly to the fake Neuro backend
    uri = "ws://127.0.0.1:8001"
    async with websockets.connect(uri) as ws:
        # simulate Neuro asking for an action
        request = {
            "op": "choose_force_action",
            "game_title": "TestGame",
            "state": {},
            "query": "Choose an action",
            "ephemeral_context": {},
            "actions": [{"name": "action1"}, {"name": "action2"}],
        }
        await ws.send(json.dumps(request))
        print("[Neuro] sent choose_force_action")

async def run_all():
    await asyncio.sleep(2)  # wait for servers to start
    await asyncio.gather(
        fake_integration(),
        fake_watcher(),
        fake_neuro_request()
    )

if __name__ == "__main__":
    asyncio.run(run_all())
