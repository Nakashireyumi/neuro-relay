# src/dev/nakurity/test_harness.py
import asyncio
import json
import websockets

AUTH_TOKEN = "super-secret-token"

import sys
import linecache
import inspect
import time
from pathlib import Path
from datetime import datetime

# === CONFIGURATION ===
PROJECT_ROOT = Path(__file__).parents[2].resolve()
LOG_PATH = PROJECT_ROOT / "trace_debug.log"

USE_COLOR = True
SHOW_FILE_PATH = False
SHOW_TIMESTAMP = True
MAX_VALUE_LEN = 60
MAX_LOCALS = 4
MAX_STACK_DEPTH = 12

start_time = time.perf_counter()

# === COLOR UTILITIES ===
def color(txt, fg=None, style=None):
    if not USE_COLOR:
        return txt
    codes = {
        "reset": "\033[0m", "bold": "\033[1m",
        "gray": "\033[90m", "red": "\033[91m",
        "green": "\033[92m", "yellow": "\033[93m",
        "blue": "\033[94m", "magenta": "\033[95m",
        "cyan": "\033[96m",
    }
    return f"{codes.get(style, '')}{codes.get(fg, '')}{txt}{codes['reset']}"

# === FORMATTING HELPERS ===
def short(v):
    try:
        s = repr(v)
        return s if len(s) <= MAX_VALUE_LEN else s[:MAX_VALUE_LEN - 3] + "..."
    except (AttributeError, ValueError, TypeError):
        # Handle cases where repr() fails (like _ModuleLock objects)
        return f"<{type(v).__name__} object>"

def now():
    return f"{(time.perf_counter() - start_time):6.3f}s"

def fmt_path(rel, lineno):
    if SHOW_FILE_PATH:
        return f"{rel}:{lineno}"
    return f"{rel.name}:{lineno}"

def fmt_locals(locals_dict):
    items = [
        f"{color(k, 'blue')}={color(short(v), 'gray')}"
        for k, v in locals_dict.items()
        if not k.startswith("__") and not inspect.isfunction(v)
    ]
    return ", ".join(items[:MAX_LOCALS])

def write_log(line):
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")

# === MAIN TRACER ===
TRACE_INCLUDE = ["src/dev/nakurity", "src/dev/tests"]
TRACE_EVENTS = {"call", "return", "exception", "line"}  # include line events for verbose debugging
TRACE_EXCLUDE_FUNCS = {"write_log", "trace"}

def trace(frame, event, arg):
    try:
        filename = Path(frame.f_code.co_filename).resolve()
    except Exception:
        return  # during shutdown, modules may be gone
    
    # Skip import machinery and module loading to avoid interference
    if "importlib" in str(filename) or "<frozen" in str(filename):
        return

    try:
        filename.relative_to(PROJECT_ROOT)
    except ValueError:
        return  # Skip non-project files

    rel = filename.relative_to(PROJECT_ROOT)
    # include-path filtering
    rel_posix = rel.as_posix()
    # if TRACE_INCLUDE and not any(p in rel_posix for p in TRACE_INCLUDE):
    #     return

    func = frame.f_code.co_name
    # if func in TRACE_EXCLUDE_FUNCS:
    #     return

    # if event not in TRACE_EVENTS:
    #     return

    depth = len(inspect.stack(0)) - 1
    indent = "│  " * (depth % MAX_STACK_DEPTH)
    ts = f"[{now()}]" if SHOW_TIMESTAMP else ""

    def log(msg):
        try:
            print(msg)
            write_log(msg)
        except Exception:
            pass

    # === CALL ===
    if event == "call":
        args, _, _, values = inspect.getargvalues(frame)
        arg_str = ", ".join(f"{a}={short(values[a])}" for a in args if a in values)
        header = f"\n{indent}{color('╭▶', 'cyan', 'bold')} {color(func, 'green', 'bold')}() {color(fmt_path(rel, frame.f_lineno), 'gray')} {ts}"
        log(header)
        if arg_str:
            log(f"{indent}{color('│ args:', 'yellow')} {arg_str}")

    # === LINE ===
    elif event == "line":
        line = linecache.getline(str(filename), frame.f_lineno).strip()
        log(f"{indent}{color('│ →', 'cyan')} {color(line, 'reset')}")
        local_vars = fmt_locals(frame.f_locals)
        if local_vars:
            log(f"{indent}{color('│ • locals:', 'gray')} {local_vars}")

    # === RETURN ===
    elif event == "return":
        msg = f"{indent}{color('╰↩', 'green', 'bold')} {color('return', 'gray')} {short(arg)} {ts}"
        log(msg)

    # === EXCEPTION ===
    elif event == "exception":
        exc_type, exc_value, _ = arg
        msg = f"{indent}{color('💥', 'red', 'bold')} {exc_type.__name__}: {exc_value}  {color(fmt_path(rel, frame.f_lineno), 'gray')}"
        log(msg)

    return trace

sys.settrace(trace)  # Re-enabled with improved error handling

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
        # Listen for integration messages with timeout
        message_count = 0
        max_messages = 3
        try:
            async for msg in ws:
                try:
                    data = json.loads(msg)
                    print(f"[Integration] received: {data}")
                    message_count += 1
                    
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
                    
                    # Exit after receiving a few messages
                    if message_count >= max_messages:
                        print(f"[Integration] received {message_count} messages, exiting")
                        break
                        
                except json.JSONDecodeError:
                    print(f"[Integration] received non-JSON: {msg}")
        except (websockets.exceptions.ConnectionClosed, websockets.exceptions.ConnectionClosedOK):
            print("[Integration] connection closed")

async def fake_watcher():
    uri = "ws://127.0.0.1:8765"
    async with websockets.connect(uri) as ws:
        # Use Neuro OS special auth token for enhanced privileges
        await ws.send(json.dumps({
            "type": "neuro-os",
            "name": "neuroos",
            "auth_token": "super-secret-token"  # Neuro OS special token
        }))
        
        # Test direct message to Neuro backend (enhanced privilege)
        await asyncio.sleep(1)
        await ws.send(json.dumps({
            "direct_to_neuro": True,
            "payload": {
                "op": "neuroos_status",
                "message": "Neuro OS monitoring relay activity",
                "timestamp": "2025-01-01T00:00:00Z"
            }
        }))
        print("[Neuro OS] sent direct message to Neuro backend")
        
        # Regular integration command (may fail if integration disconnected)
        await asyncio.sleep(1)
        await ws.send(json.dumps({
            "target": "spotify", 
            "cmd": {"action": "ping"}
        }))
        print("[Watcher] sent ping")
        
        # Listen for a few messages then exit
        message_count = 0
        max_messages = 5
        try:
            async for msg in ws:
                print("[Watcher] recv:", msg)
                message_count += 1
                if message_count >= max_messages:
                    print(f"[Watcher] received {message_count} messages, exiting")
                    break
        except (websockets.exceptions.ConnectionClosed, websockets.exceptions.ConnectionClosedOK):
            print("[Watcher] connection closed")

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
        
        # Wait for response from relay
        try:
            response = await asyncio.wait_for(ws.recv(), timeout=10.0)
            data = json.loads(response)
            print(f"[Neuro] received response: {data}")
        except asyncio.TimeoutError:
            print("[Neuro] timeout waiting for response")
        except (websockets.exceptions.ConnectionClosed, websockets.exceptions.ConnectionClosedOK):
            print("[Neuro] connection closed before response")
        except json.JSONDecodeError:
            print(f"[Neuro] received non-JSON response: {response}")

async def run_all():
    print("[Test Harness] Starting relay integration tests...")
    await asyncio.sleep(2)  # wait for servers to start
    
    try:
        await asyncio.wait_for(
            asyncio.gather(
                fake_integration(),
                fake_watcher(),
                fake_neuro_request()
            ),
            timeout=30.0  # 30 second timeout for all tests
        )
        print("[Test Harness] All tests completed successfully")
    except asyncio.TimeoutError:
        print("[Test Harness] Tests timed out after 30 seconds")
    except Exception as e:
        print(f"[Test Harness] Tests failed with error: {e}")
    finally:
        print("[Test Harness] Test run finished")

if __name__ == "__main__":
    import sys
    try:
        asyncio.run(run_all())
        print("[Test Harness] Exiting successfully")
        sys.exit(0)
    except KeyboardInterrupt:
        print("[Test Harness] Interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"[Test Harness] Fatal error: {e}")
        sys.exit(1)
