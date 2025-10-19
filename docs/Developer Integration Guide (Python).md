# 🧩 Developer Integration Guide

Developers can use the **Neuro Relay SDK pattern** to connect their integration module to the relay.
Each integration behaves like a small *microclient* that registers itself, listens for events, and replies with actions.

---

## 📁 Recommended Project Layout for a New Integration

Here’s a clean example layout for developers building a new Neuro integration that connects through the Relay:

```
my_integration/
├── src/
│   ├── __init__.py
│   ├── config.yaml              # Local configuration (API keys, etc.)
│   ├── relay_client.py          # Handles WebSocket connection to Relay
│   ├── integration_logic.py     # Core logic (AI, event handlers, etc.)
│   └── main.py                  # Entrypoint to launch the integration
│
└── requirements.txt
```

---

## ⚙️ `requirements.txt`

```txt
websockets
pyyaml
```

*(Add any service-specific dependencies here, like `requests` or `discord.py`.)*

---

## 🔧 `config.yaml`

```yaml
relay:
  host: "127.0.0.1"
  port: 8765
  auth_token: "super-secret-token"

integration:
  name: "spotify-adapter"
  description: "Syncs Spotify playback state with Neuro."
```

---

## 🧠 `relay_client.py`

A reusable helper that automatically registers your integration and handles message dispatch.

```python
import asyncio
import json
import websockets
import yaml

class RelayClient:
    def __init__(self, config_path="src/config.yaml"):
        with open(config_path, "r") as f:
            cfg = yaml.safe_load(f)
        self.host = cfg["relay"]["host"]
        self.port = cfg["relay"]["port"]
        self.auth_token = cfg["relay"]["auth_token"]
        self.name = cfg["integration"]["name"]

        self.ws = None
        self.handlers = {}

    async def connect(self):
        uri = f"ws://{self.host}:{self.port}"
        self.ws = await websockets.connect(uri)

        # Register with relay
        await self.ws.send(json.dumps({
            "type": "integration",
            "name": self.name,
            "auth_token": self.auth_token
        }))
        print(f"[RelayClient] Registered as '{self.name}'")

        # Start listening
        asyncio.create_task(self._listen())

    async def _listen(self):
        async for msg in self.ws:
            data = json.loads(msg)
            event = data.get("event")
            if event in self.handlers:
                await self.handlers[event](data)
            else:
                print(f"[RelayClient] Unhandled event: {event}")

    def on(self, event_name):
        """Decorator for handling relay events."""
        def wrapper(func):
            self.handlers[event_name] = func
            return func
        return wrapper

    async def send(self, event, payload):
        """Send a message back to the relay."""
        await self.ws.send(json.dumps({
            "event": event,
            "payload": payload,
        }))
```

---

## 🎮 `integration_logic.py`

Implements your custom integration behavior — this is where your module reacts to Neuro events or triggers actions.

```python
import asyncio

async def handle_neuro_action(data, relay):
    action = data.get("payload", {}).get("action")
    print(f"[Integration] Neuro requests action: {action}")

    # Example: reply with acknowledgment
    await relay.send("action_result", {"status": "ok", "source": relay.name})
```

---

## 🚀 `main.py`

Tie it all together:

```python
import asyncio
from src.relay_client import RelayClient
from src.integration_logic import handle_neuro_action

async def main():
    relay = RelayClient()
    await relay.connect()

    @relay.on("choose_action")
    async def on_action(data):
        await handle_neuro_action(data, relay)

    # Keep running forever
    while True:
        await asyncio.sleep(1)

if __name__ == "__main__":
    asyncio.run(main())
```

---

## 🧩 Example Interaction Flow

```text
[Neuro Backend]
    │
    ▼
[Nakurity Backend]
    │
    ▼
[Intermediary]  ←→  [Integration: spotify-adapter]
    │
    ▼
[Neuro-OS Watcher]
```

When Neuro emits an event like:

```json
{ "event": "choose_action", "payload": {"context": "song_change"} }
```

The Intermediary routes it to all connected integrations (e.g., `spotify-adapter`, `discord-status`, `game-adapter`).
Each integration can respond with:

```json
{ "event": "action_result", "payload": {"status": "ok", "action": "pause"} }
```

The **first valid response** is returned to Neuro. Others are ignored or cached for later.

---

## 🧠 Developer Checklist

| Step | Action                                                 | Example                          |
| ---- | ------------------------------------------------------ | -------------------------------- |
| 1️⃣  | Create a new project with the above structure          | `my_integration/`                |
| 2️⃣  | Edit `config.yaml` with relay connection info          | `auth_token: super-secret-token` |
| 3️⃣  | Implement your core logic in `integration_logic.py`    | Add event handlers               |
| 4️⃣  | Run `python -m src.main`                               | Integration connects to relay    |
| 5️⃣  | Verify relay logs show “Registered as spotify-adapter” | ✅                                |

---

## 💬 Supported Event Types

| Event Type           | Direction           | Description                              |
| -------------------- | ------------------- | ---------------------------------------- |
| `choose_action`      | Relay → Integration | Neuro requests a decision or action      |
| `action_result`      | Integration → Relay | Integration replies with a chosen action |
| `context_update`     | Relay → Integration | Context data (e.g. new chat, user state) |
| `integration_log`    | Integration → Relay | Custom log or telemetry message          |
| `integration_status` | Relay ↔ Watchers    | Online/offline status update             |
| `custom_event`       | Bidirectional       | Free-form event for experiments          |

---

## 🧱 Summary for Developers

✅ **Zero direct Neuro dependencies** — connect to the Relay instead
✅ **Event-based** communication model with simple decorators
✅ **Persistent & reliable** thanks to the Relay’s queuing system
✅ **Compatible across all integrations** — no backend change needed

---
