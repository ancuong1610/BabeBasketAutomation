"""Load config.toml and secrets from the environment or a local .env file."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


def load_dotenv(path: Path = ROOT / ".env") -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def load(path: Path = ROOT / "config.toml") -> dict:
    load_dotenv()
    with path.open("rb") as fh:
        cfg = tomllib.load(fh)
    channel = os.environ.get("DISCORD_CHANNEL_ID")
    if channel:
        cfg.setdefault("discord", {})["channel_id"] = channel
    return cfg


def secret(name: str) -> str:
    load_dotenv()
    value = os.environ.get(name, "")
    if not value:
        raise SystemExit(f"Missing {name}. Put it in .env (see .env.example).")
    return value
