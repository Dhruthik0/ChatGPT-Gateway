import os
import secrets
import tomllib
from dataclasses import dataclass
from pathlib import Path

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "ai-bridge"
CONFIG_FILE = CONFIG_DIR / "config.toml"
DEFAULT_PROFILE = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share"))) / "ai-bridge/chrome-profile"

_TEMPLATE = """\
# ai-bridge config. Mode 0600 -- it holds the API token.

[server]
host = "127.0.0.1"          # never change this; the service refuses non-loopback
port = 8756
token = "{token}"

[browser]
cdp_url = "http://127.0.0.1:9333"
profile_dir = "{profile}"

[limits]
max_input_bytes = 32768
default_timeout = 120
max_queue = 10

[logging]
# true writes prompts AND responses to the journal. Off unless you are debugging.
log_bodies = false
"""


@dataclass
class Config:
    host: str
    port: int
    token: str
    cdp_url: str
    profile_dir: Path
    max_input_bytes: int
    default_timeout: int
    max_queue: int
    log_bodies: bool


def load() -> Config:
    if not CONFIG_FILE.exists():
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(_TEMPLATE.format(token=secrets.token_urlsafe(32), profile=DEFAULT_PROFILE))
        CONFIG_FILE.chmod(0o600)
    raw = tomllib.loads(CONFIG_FILE.read_text())
    s, b, l, g = raw["server"], raw["browser"], raw["limits"], raw["logging"]
    if s["host"] not in ("127.0.0.1", "localhost", "::1"):
        raise SystemExit(f"refusing to bind non-loopback host {s['host']!r}; edit {CONFIG_FILE}")
    return Config(
        host=s["host"], port=s["port"], token=s["token"],
        cdp_url=b["cdp_url"], profile_dir=Path(b["profile_dir"]).expanduser(),
        max_input_bytes=l["max_input_bytes"], default_timeout=l["default_timeout"], max_queue=l["max_queue"],
        log_bodies=g["log_bodies"],
    )
