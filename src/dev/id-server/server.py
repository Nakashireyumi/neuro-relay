
from ..utils.loadconfig import load_config

cfg = load_config().get("nakurity-id", {})
HOST = cfg.get("host", "127.0.0.1")
PORT = cfg.get("port", "3032")

from pathlib import Path
from fastapi import FastAPI

class IDServer:
    def __init__(self):
        self.app = FastAPI(title="Nakurity ID Server")
        self.path = (Path(__file__).parent / "routes").resolve()
        self.routes = []
        self.auth_tokens = {}  # {module_id: token}
        self._load_routes()

    def _load_routes(self):
        import importlib.util
        for file in self.path.glob("*.py"):
            if file.name.startswith("__"):
                continue
            mod_name = f"id_server.routes.{file.stem}"
            spec = importlib.util.spec_from_file_location(mod_name, file)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            self.routes.append(mod)
            if hasattr(mod, "register_routes"):
                mod.register_routes(self)

    async def run(self, host="127.0.0.1", port=8081):
        import uvicorn
        config = uvicorn.Config(self.app, host=host, port=port, log_level="info")
        server = uvicorn.Server(config)
        await server.serve()

from dataclasses import dataclass, field
from typing import Callable, List, Optional

# Used for helping routes identify themselves
@dataclass
class RouteID:
    route_path: str = "/"
    method: List[str] = field(default_factory=lambda: ["get", "post"])
    special_functions: Optional[Callable] = None
    special_class: Optional[type] = None


import secrets
import asyncio
from typing import Dict, Any, Optional
from dataclasses import dataclass, field

@dataclass
class IdentityRecord:
    module_name: str
    token: str
    identity: Dict[str, Any]
    source: str  # "intermediary", "backend", or "integration"

# -------------------------------
# Shared Nakurity Link Stub
# -------------------------------

class NakurityLink:
    """Handles cross-registration with Nakurity Backend."""
    def __init__(self):
        self.backend_identity = None

    async def identify_backend(self, payload):
        self.backend_identity = payload
        print(f"[IDServer] Nakurity Backend identified: {payload}")

    async def forward_integration_identity(self, payload):
        print(f"[IDServer] Forwarding integration identity to Nakurity: {payload}")

nakurity_link = NakurityLink()

class NakurityID:
    """
    Handles authentication and identity resolution between:
      - Nakurity Backend
      - Intermediary
      - Integrations

    Lives on the ID Server side, but can be imported by others
    to query/resolve identity state.
    """
    def __init__(self):
        self.auth_tokens: Dict[str, str] = {}           # {module_name: token}
        self.identities: Dict[str, IdentityRecord] = {} # {module_name: record}
        self.backend_identity: Optional[Dict[str, Any]] = None
        self._lock = asyncio.Lock()

    # -------------------------------
    # TOKEN HANDLING
    # -------------------------------

    def generate_token(self, module_name: str) -> str:
        token = secrets.token_hex(16)
        self.auth_tokens[module_name] = token
        print(f"[NakurityID] Issued new token for {module_name}: {token}")
        return token

    def verify_token(self, module_name: str, token: str) -> bool:
        stored = self.auth_tokens.get(module_name)
        valid = stored == token
        print(f"[NakurityID] Verify {module_name}: {'valid' if valid else 'invalid'}")
        return valid

    # -------------------------------
    # IDENTITY MANAGEMENT
    # -------------------------------

    async def register_identity(self, module_name: str, token: str, identity: Dict[str, Any], source: str):
        """Store or update identity once verified."""
        if not self.verify_token(module_name, token):
            raise ValueError(f"Invalid token for {module_name}")

        async with self._lock:
            record = IdentityRecord(module_name=module_name, token=token, identity=identity, source=source)
            self.identities[module_name] = record
            print(f"[NakurityID] Registered identity from {source}: {module_name}")

    async def identify_backend(self, identity: Dict[str, Any]):
        """Called when Nakurity Backend identifies itself."""
        async with self._lock:
            self.backend_identity = identity
            print(f"[NakurityID] Nakurity Backend identified: {identity}")

    def resolve(self, module_name: str) -> Optional[IdentityRecord]:
        """Resolve module identity by name."""
        return self.identities.get(module_name)

    def list_identities(self) -> Dict[str, Any]:
        """Return summary of all registered identities."""
        return {
            k: {
                "source": v.source,
                "identity": v.identity
            }
            for k, v in self.identities.items()
        }

    # -------------------------------
    # CROSS-LINK OPERATIONS
    # -------------------------------

    async def forward_identity_to_backend(self, nakurity_link, identity: Dict[str, Any]):
        """Forward verified integration identity to Nakurity Backend."""
        print(f"[NakurityID] Forwarding verified identity to Nakurity Backend...")
        await nakurity_link.forward_integration_identity(identity)

    async def refresh_backend_link(self, nakurity_link):
        """Re-register backend if identity is known."""
        if not self.backend_identity:
            print("[NakurityID] No backend identity to refresh.")
            return
        await nakurity_link.identify_backend(self.backend_identity)
        print("[NakurityID] Backend link refreshed.")

# Instantiate singleton for shared import use
nakurity_id = NakurityID()