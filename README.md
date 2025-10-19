# 🧠 Neuro Relay

**Neuro Relay** is a modular integration bridge designed to help the **Neuro backend** manage and coordinate multiple concurrent **Neuro integrations** (e.g., external clients, Neuro-OS, game adapters, UI extensions).  
It sits between Neuro’s main backend and external integrations, handling routing, authentication, and persistent queuing.

---

## 🚀 Overview

Neuro Relay is composed of **three primary layers**:

1. **🛰️ Intermediary**  
   A WebSocket-based message router that connects:
   - **Neuro-OS watchers** (UI/monitoring clients)
   - **Integration clients** (individual apps or modules)
   It handles authentication, message forwarding, and queue persistence.

2. **🧩 Nakurity Backend (Relay Server)**  
   Acts as a *mock or proxy Neuro backend*, implementing the `neuro_api.server` interfaces.  
   It receives Neuro API calls (e.g., “choose_action”) and delegates decision-making to connected integrations via the Intermediary.

3. **🌐 Nakurity Client (Optional)**  
   A relay client that can connect **outbound** to a real Neuro backend if needed —  
   useful when extending or mirroring a live Neuro instance.

Together, these components make Neuro Relay capable of managing **many integration connections simultaneously** while maintaining reliable routing and queue persistence.

---

## 📁 Project Structure

```

src/
├── dev/
│   └── nakurity/
│       ├── **main**.py         # Entrypoint: launches both intermediary + backend
│       ├── intermediary.py     # Handles WebSocket routing & message relaying
│       ├── server.py           # Relay backend implementing neuro_api server clients
│       ├── client.py           # Optional outbound connector to real Neuro backend
│       └── **init**.py
│
└── resources/
└── authentication.yaml     # Configuration file (see below)

````

---

## ⚙️ Configuration

All runtime configuration is located in:

**`src/resources/authentication.yaml`**

```yaml
intermediary:
  host: "127.0.0.1"
  port: 8765
  auth_token: "super-secret-token"
  relay_queue: "relay_message_queue.pkl"

nakurity-backend:
  host: "127.0.0.1"
  port: 8000
````

### Explanation

| Key                              | Description                                            |
| -------------------------------- | ------------------------------------------------------ |
| **intermediary.host / port**     | WebSocket endpoint for integrations & Neuro-OS clients |
| **auth_token**                   | Shared secret for authenticating connections           |
| **relay_queue**                  | Pickle file used to persist unsent messages            |
| **nakurity-backend.host / port** | Internal Neuro relay backend endpoint                  |

---

## 🧠 How It Works

### 1. Intermediary

* Acts as the **central message broker**.
* Each client connects via WebSocket and **registers** itself using a JSON payload:

  ```json
  { "type": "integration" | "neuro-os", "name": "client-name", "auth_token": "super-secret-token" }
  ```
* Routes messages between Neuro-OS watchers and integration clients.
* Maintains a persistent **message queue** for reliability (using `relay_message_queue.pkl`).

### 2. Nakurity Backend

* Implements Neuro API server logic (`AbstractNeuroServerClient`, etc.).
* Receives events from Neuro-sama (or any Neuro backend) such as:

  * `choose_force_action()`
  * `add_context()`
* Forwards these to connected integrations via the Intermediary.
* Awaits replies or defaults to a fallback decision.

### 3. Nakurity Client (Optional)

* Connects **outward** to a *real Neuro backend* if desired.
* Used to forward integration events into a live Neuro instance instead of a local relay.

---

## 🧩 Typical Flow
This is for integrations that connect directly to the intermediary and skips the Nakurity Backend.

```text
[Integration App] ⇄ ws://127.0.0.1:8765 ⇄ [Intermediary] ⇄ [Nakurity Client] ⇄ Neuro API / Neuro-sama
                                  ↑
                             (Optional)
                        [Neuro-OS / UI Watcher]
```

Example sequence:

1. An integration (e.g., Spotify, Discord, GameAdapter) connects to `ws://127.0.0.1:8765` with its token.
2. Neuro-OS also connects as a watcher for visualization.
3. The Nakurity Backend (on port `8000`) sends or receives events from Neuro.
4. Messages are forwarded automatically between integrations and the Neuro backend through the Intermediary.

For most neuro integrations that usually connects directly to the neuro backend. This would be Neuro Relay's typical flow:

```text
[Integration App] ⇄ ws://127.0.0.1:8000 ⇄ [Nakurity Backend] ⇄ [Intermediary] ⇄ [Nakurity Client] ⇄ Neuro API / Neuro-sama
                                                ↑
                                            (Optional)
                                        [Neuro-OS / UI Watcher]
```

Example sequence:

1. An integration (e.g., Spotify, Discord, GameAdapter) connects to `ws://127.0.0.1:8000` (this would typically be the neuro backend).
2. Neuro-OS also connects as a watcher for visualization.
3. The Nakurity Backend (on port `8000`) sends or receives events from Neuro.
4. Messages are forwarded automatically between integrations and the Neuro backend through the Intermediary.

---

### 🧮 Integration Management and Queuing

Neuro Relay isn’t just a bridge — it’s a **connection manager** that stabilizes Neuro’s backend against multiple integration connections.

When multiple integrations (e.g., Spotify, Discord, Game Adapter, UI modules) connect through the **Nakurity Backend**, they are:

1. **Registered Individually** by the Intermediary.
   Each integration is given a unique name and communication channel.

2. **Managed Collectively** under a single backend session.
   The Nakurity Backend aggregates all these integrations into a *single unified integration* as seen by Neuro-sama.

3. **Auto-Queued and Resilient.**

   * If an integration disconnects, its messages are queued and retried when it reconnects.
   * If Neuro goes offline, pending actions persist in `relay_message_queue.pkl` until it returns.
   * This avoids Neuro’s typical instability when handling many simultaneous clients.

4. **Load-balanced via relay broadcast.**
   Actions and decisions are broadcast to all connected integrations. The first valid response is returned to Neuro, while others are safely ignored — ensuring responsiveness and preventing Neuro from hanging or “freezing” during multiple integration responses.

This architecture makes **Neuro Relay** act as a *neural multiplexer* — converting multiple independent integrations into one stable, unified interface for Neuro.

---

### 🧱 Why This Matters

In the default Neuro backend:

* Multiple concurrent integrations (like Discord, Spotify, or UI bots) often cause **race conditions, event overlap, or backend lockups**.
* Each integration tries to talk directly to Neuro, resulting in conflicting actions.

In **Neuro Relay**:

* Only the Nakurity Backend connects directly to Neuro.
* The Intermediary manages all other integrations in a coordinated queue system.
* From Neuro’s perspective, **there is only one stable integration** — the “Relay Integration.”
* From your perspective, **there can be as many integrations as you want**, running concurrently, safely.

---

### 💾 Example Scenario

```text
┌────────────────────────────────────────────────────┐
│                    Neuro Backend                   │
│                (sees 1 integration)                │
└───────────────▲────────────────────────────────────┘
                │
                │ single stable connection
                │
       ┌────────┴────────┐
       │ Nakurity Backend│
       │ (manages all)   │
       └────────▲────────┘
                │
    ┌───────────┴──────────────────────────┐
    │         Intermediary Queue           │
    │  [spotify] [discord] [game-adapter]  │
    │       ↕ auto queue + retry           │
    └──────────────────────────────────────┘
```

All these integrations are **merged** into one stream of decisions, actions, and contexts.
Neuro never sees the chaos underneath — it only sees one, consistent connection.

---

### ⚙️ Key Benefits

| Feature                    | Description                                       |
| -------------------------- | ------------------------------------------------- |
| **Automatic Multiplexing** | All integrations share one backend session        |
| **Queue Persistence**      | Messages are saved and retried across restarts    |
| **Fault Isolation**        | One broken integration won’t crash others         |
| **Unified Relay Identity** | Neuro only sees “Relay Integration” as its client |
| **Backward Compatibility** | Existing integrations need **zero changes**       |

---

### 🧠 Summary Addendum

> Neuro Relay acts as a *stabilizing relay layer* for the Neuro ecosystem.
> It allows multiple simultaneous integrations to coexist safely by funneling them through a single virtual integration channel — protecting Neuro from overloads and ensuring reliable coordination between all connected modules.

---

## ▶️ Running the Relay

### Prerequisites

* Python **3.10+**
* Installed dependency: `neuro_api`
* Installed dependencies for WebSocket server:

  ```bash
  pip install websockets pyyaml
  ```

### Launch Command

From the project root:

```bash
python -m src.dev.nakurity
```

You should see logs similar to:

```
[Intermediary] starting on ws://127.0.0.1:8765
[Nakurity Backend] starting relay backend...
```

Press **Ctrl+C** to stop gracefully — all queues will persist.

---

## 🔐 Authentication

All clients (Neuro-OS or integrations) must include the **auth_token** from `authentication.yaml` in their registration payload.

Example registration payload:

```json
{
  "type": "integration",
  "name": "spotify-adapter",
  "auth_token": "super-secret-token"
}
```

Invalid tokens will result in:

```json
{ "error": "invalid auth token" }
```

---

## 🧰 Development Notes

* All messages are JSON-encoded.
* Binary payloads (e.g. image or file uploads) are automatically saved by the Intermediary.
* Failed deliveries are **queued** and retried every 5 seconds.
* Watchers automatically receive connection/disconnection events for all integrations.

---

## 💡 Example Integration (Python)

```python
import asyncio
import json
import websockets

AUTH_TOKEN = "super-secret-token"

async def main():
    async with websockets.connect("ws://127.0.0.1:8765") as ws:
        await ws.send(json.dumps({
            "type": "integration",
            "name": "example-bot",
            "auth_token": AUTH_TOKEN
        }))

        while True:
            msg = await ws.recv()
            print("Received:", msg)

asyncio.run(main())
```

---

## 🧱 Future Extensions

* Support for multi-relay clusters (distributed relays)
* Enhanced metrics for integration uptime
* Configurable retry and persistence policies
* Web-based dashboard for live relay inspection

---

## 📜 License

This project is part of the **Neuro Integration Suite (Nakurity)**.
All rights reserved © 2025 Nakurity Development Team.

---

## 🧩 Summary

| Component            | Port                      | Purpose                                           |
| -------------------- | ------------------------- | ------------------------------------------------- |
| **Intermediary**     | 8765                      | Routes messages between Neuro-OS and Integrations |
| **Nakurity Backend** | 8000                      | Acts as a local Neuro backend for testing/relay   |
| **Relay Queue**      | `relay_message_queue.pkl` | Stores unsent messages                            |
| **Auth Token**       | `"super-secret-token"`    | Secures relay registration                        |

---
