# src/dev/nakurity/__main__.py
import asyncio
import signal

from .intermediary import Intermediary
from .server import NakurityBackend
from ..utils.loadconfig import load_config

"""
Entrypoint: runs intermediary (ws://127.0.0.1:8765) and the relay server (nakurity-backend) (ws://127.0.0.1:8000)
Neuro Integrations would connect to 127.0.0.1:8000 (nakurity-backend).
Neuro-OS and its integrations connect to 127.0.0.1:8765.

And Intermediary runs the Nakurity Client which connects to the neuro backend,
    and forwards all neuro integrations connected to the Nakurity Backend
"""

cfg = load_config()

HOST = {
    "intermediary": cfg.get("intermediary", {}).get("host", "127.0.0.1"),
    "nakurity-backend": cfg.get("backend", {}).get("host", "127.0.0.1")
}
PORT = {
    "intermediary": int(cfg.get("intermediary", {}).get("port", 8765)),
    "nakurity-backend": int(cfg.get("nakurity-backend", {}).get("port", 8000))
}

async def main():
    intermediary = Intermediary(host=HOST.get("intermediary"), port=PORT.get("intermediary"))
    nakurity_backend = NakurityBackend(intermediary)

    # start intermediary first and wait for it to be listening
    intermediary_task = await asyncio.create_task(intermediary.start())

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

    # graceful shutdown on SIGINT
    loop = asyncio.get_event_loop()
    stop = loop.create_future()
    loop.add_signal_handler(signal.SIGINT, lambda: stop.set_result(True))
    await stop

    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)

if __name__ == "__main__":
    asyncio.run(main())
