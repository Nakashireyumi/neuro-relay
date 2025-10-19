from pathlib import Path
import yaml

# ---------------------------
# Load configuration from YAML
# ---------------------------
def load_config():
    # Get the directory of this script
    current_dir = Path(__file__).resolve().parent

    # Walk upward until we find "neuro-relay" folder (the project root)
    for parent in current_dir.parents:
        if parent.name == "neuro-relay":
            project_root = parent
            break
    else:
        raise RuntimeError("Could not locate 'windows-api' directory above this file.")

    # Build path to the YAML config file
    config_path = project_root / "src" / "resources" / "authentication.yaml"

    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found at: {config_path}")

    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

# cfg = load_config()

# HOST = cfg.get("host", "127.0.0.1")
# PORT = int(cfg.get("port", 8765))
# AUTH_TOKEN = cfg.get("auth_token", "replace-with-a-strong-secret")
# SCREENSHOT_DIR = Path(cfg.get("screenshot_dir", "./screenshots"))