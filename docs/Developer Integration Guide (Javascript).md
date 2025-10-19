# 🧩 Developer Integration Guide

Developers can use the **Neuro Relay SDK pattern** to connect their integration module to the relay.
Each integration behaves like a small *microclient* that registers itself, listens for events, and replies with actions.

### 📁 Recommended Project Structure

```
my-integration/
├── src/
│   ├── config.yaml              # Relay + integration config
│   ├── relayClient.js           # Core WebSocket connector
│   ├── integrationLogic.js      # Main behavior / event handlers
│   └── index.js                 # Entrypoint
│
├── package.json
└── .env                         # Optional environment overrides
```

---

## 📦 `package.json`

```json
{
  "name": "my-integration",
  "version": "1.0.0",
  "type": "module",
  "main": "src/index.js",
  "dependencies": {
    "ws": "^8.18.0",
    "yaml": "^2.6.0"
  }
}
```

---

## ⚙️ `src/config.yaml`

```yaml
relay:
  host: "127.0.0.1"
  port: 8765
  auth_token: "super-secret-token"

integration:
  name: "discord-adapter"
  description: "Mirrors Neuro’s state and commands to Discord."
```

---

## 🧠 `src/relayClient.js`

Reusable Relay connector with automatic registration, event dispatch, and handler mapping.

```js
import WebSocket from "ws";
import fs from "fs";
import yaml from "yaml";

export class RelayClient {
  constructor(configPath = "src/config.yaml") {
    const config = yaml.parse(fs.readFileSync(configPath, "utf8"));
    this.host = config.relay.host;
    this.port = config.relay.port;
    this.authToken = config.relay.auth_token;
    this.name = config.integration.name;
    this.handlers = {};
  }

  async connect() {
    const uri = `ws://${this.host}:${this.port}`;
    this.ws = new WebSocket(uri);

    this.ws.on("open", () => {
      console.log(`[RelayClient] Connected to ${uri}`);
      this._register();
    });

    this.ws.on("message", (data) => {
      try {
        const msg = JSON.parse(data.toString());
        const event = msg.event;
        if (this.handlers[event]) this.handlers[event](msg);
        else console.log(`[RelayClient] Unhandled event: ${event}`);
      } catch (e) {
        console.error(`[RelayClient] Error parsing message:`, e);
      }
    });

    this.ws.on("close", () => {
      console.warn("[RelayClient] Disconnected. Retrying in 5s...");
      setTimeout(() => this.connect(), 5000);
    });
  }

  _register() {
    const payload = {
      type: "integration",
      name: this.name,
      auth_token: this.authToken,
    };
    this.ws.send(JSON.stringify(payload));
    console.log(`[RelayClient] Registered as '${this.name}'`);
  }

  on(eventName, handler) {
    this.handlers[eventName] = handler;
  }

  send(event, payload = {}) {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ event, payload }));
    } else {
      console.warn("[RelayClient] Tried to send but socket is not open.");
    }
  }
}
```

---

## 🎮 `src/integrationLogic.js`

Custom behavior layer — define how your integration reacts to relay events and Neuro instructions.

```js
export function setupIntegration(relay) {
  relay.on("choose_action", (msg) => {
    const context = msg.payload?.context || "none";
    console.log(`[Integration] Neuro requested action for: ${context}`);

    // Example: reply with a mock decision
    relay.send("action_result", {
      status: "ok",
      source: relay.name,
      decision: "play_pause_toggle"
    });
  });

  relay.on("context_update", (msg) => {
    console.log("[Integration] Context update:", msg.payload);
  });
}
```

---

## 🚀 `src/index.js`

Entrypoint to launch the integration.

```js
import { RelayClient } from "./relayClient.js";
import { setupIntegration } from "./integrationLogic.js";

async function main() {
  const relay = new RelayClient();
  setupIntegration(relay);
  await relay.connect();

  console.log("[Main] Integration started.");
}

main().catch(console.error);
```

---

## 🧩 Example Console Output

```
[RelayClient] Connected to ws://127.0.0.1:8765
[RelayClient] Registered as 'discord-adapter'
[Main] Integration started.
[Integration] Neuro requested action for: music_player
[RelayClient] Sent: action_result {status:"ok", decision:"play_pause_toggle"}
```

---

## 🧠 Integration Flow Recap

```text
[Neuro Backend]
   │
   ▼
[Nakurity Backend]
   │
   ▼
[Intermediary] ⇄ [discord-adapter]
```

* `discord-adapter` receives a `choose_action` event from the Intermediary.
* It processes the request, then sends an `action_result` event back.
* The Intermediary forwards this result to the Nakurity Backend → Neuro.

---

## ⚙️ Common Event Types

| Event             | Direction           | Description                       |
| ----------------- | ------------------- | --------------------------------- |
| `choose_action`   | Relay → Integration | Neuro requests a decision         |
| `context_update`  | Relay → Integration | Updated context or metadata       |
| `action_result`   | Integration → Relay | Integration responds to an action |
| `integration_log` | Integration → Relay | Custom logging or telemetry       |
| `custom_event`    | Bidirectional       | Experimental freeform events      |

---

## ✅ Dev Checklist

| Step | Description                 | Example                                         |
| ---- | --------------------------- | ----------------------------------------------- |
| 1️⃣  | Clone template              | `git clone my-integration`                      |
| 2️⃣  | Fill out `config.yaml`      | Add relay host, port, token                     |
| 3️⃣  | Define event handlers       | In `integrationLogic.js`                        |
| 4️⃣  | Run the integration         | `node src/index.js`                             |
| 5️⃣  | Check logs for registration | `[RelayClient] Registered as 'discord-adapter'` |

---

## 🧩 Developer Highlights

✅ **Auto reconnects** on disconnect
✅ **Event-driven** model using `relay.on()`
✅ **Fully compatible** with Python-based relay and Neuro backend
✅ **Plug-and-play** — no Neuro code modification needed
✅ **Cross-platform** — works on Windows, Linux, macOS

---