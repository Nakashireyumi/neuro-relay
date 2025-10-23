# src/dev/nakurity/__main__.py
import asyncio
import signal

from .intermediary import Intermediary
from .server import NakurityBackend
from .client import connect_outbound
from .intercept_proxy import InterceptProxy, config_from_yaml
from ..utils.loadconfig import load_config
from .linker import NakurityLink

"""
Entrypoint: runs intermediary (ws://127.0.0.1:8765) and the relay server (nakurity-backend) (ws://127.0.0.1:8000)
Neuro Integrations would connect to 127.0.0.1:8000 (nakurity-backend).
Neuro-OS and its integrations connect to 127.0.0.1:8765.

And Intermediary runs the Nakurity Client which connects to the neuro backend,
    and forwards all neuro integrations connected to the Nakurity Backend
"""

cfg = load_config()

import os
print("PID:", os.getpid())

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
    s = repr(v)
    return s if len(s) <= MAX_VALUE_LEN else s[:MAX_VALUE_LEN - 3] + "..."

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

# Apply trace configuration from config.yaml
from ..utils.config_loader import get_config_loader, apply_trace_config
config_loader = get_config_loader()
apply_trace_config(trace)
if config_loader.is_trace_enabled():
    print(color(f"🧠 SmartTrace enabled", "magenta", "bold"))
else:
    print(color(f"🧠 SmartTrace disabled by configuration", "yellow", "bold"))

HOST = {
    "intermediary": cfg.get("intermediary", {}).get("host", "127.0.0.1"),
    "nakurity-backend": cfg.get("nakurity-backend", {}).get("host", "127.0.0.1"),
    "nakurity-client": cfg.get("nakurity-client", {}).get("host", "127.0.0.1"),
}
PORT = {
    "intermediary": int(cfg.get("intermediary", {}).get("port", 8765)),
    "nakurity-backend": int(cfg.get("nakurity-backend", {}).get("port", 8001)),
    "nakurity-client": int(cfg.get("nakurity-client", {}).get("port", 8000)),
}

async def main():
    intermediary = Intermediary(host=HOST.get("intermediary"), port=PORT.get("intermediary"))
    nakurity_backend = NakurityBackend(intermediary)

    # start intermediary first and wait for it to be listening
    intermediary_task = await intermediary.start()

    # Set timeout for intermediary, incase it takes too long to start.
    # if that happens, something is wrong
    try:
        await intermediary.wait_until_ready(timeout=5.0)
    except asyncio.TimeoutError:
        print("[Error] intermediary failed to start within timeout")
        return
    
    # now start the backend (it will be able to connect to the intermediary or be discoverable)
    nakurity_task = asyncio.create_task(nakurity_backend.run_server(
        host=HOST.get("nakurity-backend"),
        port=PORT.get("nakurity-backend")
    ))

    tasks = [intermediary_task, nakurity_task] 

    # Optional: start intercept proxy if enabled in config
    ip_cfg = cfg.get("intercept-proxy", {}) or {}
    if ip_cfg.get("enabled", False):
        proxy = InterceptProxy(config_from_yaml())
        proxy_task = asyncio.create_task(proxy.start())
        tasks.append(proxy_task)
        print("[Main] InterceptProxy enabled and starting")

    # start nakurity client (outbound) as a background task, give it a forwarding callback
    # We pass the backend's intermediary forwarder so inbound messages from the real Neuro
    # are forwarded into the relay pipeline.
    outbound_uri = f"ws://{HOST.get('nakurity-client')}:{PORT.get('nakurity-client')}"
    # create a background task which will attach a NakurityClient to the loop
    client = None
    nk_link = None
    reconnect_in_progress = False
    
    async def setup_client_and_link(new_client):
        nonlocal nk_link, client
        client = new_client
        if client:
            # store client instance for later use
            nakurity_backend.outbound_client = client
            print("[Main] outbound client connected and stored")
            # set up NakurityLink tied to the connected client and attach to intermediary
            nk_link = NakurityLink(client)
            intermediary.nakurity_outbound_client = nk_link
            tasks.append(asyncio.create_task(nk_link.start()))
            print("[Main] NakurityLink attached to intermediary")
            # Set up reconnection callback
            client._reconnect_callback = reconnect_client
    
    async def reconnect_client():
        nonlocal reconnect_in_progress, client, nk_link
        if reconnect_in_progress:
            return
        reconnect_in_progress = True
        
        print("[Main] Connection lost, attempting to reconnect...")
        # Clean up old client
        if client and hasattr(client, '_reader_task') and client._reader_task:
            client._reader_task.cancel()
        if nk_link:
            await nk_link.stop()
            intermediary.nakurity_outbound_client = None
        
        # Attempt reconnection
        new_client = await connect_outbound(outbound_uri, nakurity_backend._handle_intermediary_forward)
        if new_client:
            print("[Main] Reconnection successful")
            await setup_client_and_link(new_client)
        else:
            print("[Main] Reconnection failed, relay will continue without Neuro backend")
        
        reconnect_in_progress = False
    
    async def start_outbound():
        # pass backend forwarder so remote Neuro actions flow into our pipeline
        new_client = await connect_outbound(outbound_uri, nakurity_backend._handle_intermediary_forward)
        if new_client is None:
            print("[Main] outbound client failed to connect initially, will retry on demand")
        else:
            await setup_client_and_link(new_client)

    outbound_task = asyncio.create_task(start_outbound())
    tasks.append(outbound_task)

    # graceful shutdown (cross-platform)
    loop = asyncio.get_event_loop()
    stop = loop.create_future()

    def _stop(*_):
        if not stop.done():
            stop.set_result(True)

    try:
        loop.add_signal_handler(signal.SIGINT, _stop)
    except NotImplementedError:
        # Windows fallback: use add_reader on stdin
        import threading
        def wait_for_ctrl_c():
            import time
            try:
                while True:
                    time.sleep(0.2)
            except KeyboardInterrupt:
                _stop()
        threading.Thread(target=wait_for_ctrl_c, daemon=True).start()

    await stop

    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)

if __name__ == "__main__":
    asyncio.run(main())
