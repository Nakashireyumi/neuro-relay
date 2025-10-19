# src/dev/nakurity/test_harness.py
import asyncio
import json
import websockets

AUTH_TOKEN = "super-secret-token"

async def fake_integration():
    uri = "ws://127.0.0.1:8765"
    async with websockets.connect(uri) as ws:
        await ws.send(json.dumps({
            "type": "integration",
            "name": "spotify",
            "auth_token": AUTH_TOKEN
        }))
        # listen for action request from Neuro relay
        async for msg in ws:
            data = json.loads(msg)
            if data.get("event") == "choose_action_request":
                choice = {
                    "choice": {
                        "selected": data["payload"]["actions"][0]["name"],
                        "data": {"volume": 50}
                    }
                }
                await ws.send(json.dumps(choice))
                print("[Integration] sent choice")
                break

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
    uri = "ws://127.0.0.1:8000"
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
