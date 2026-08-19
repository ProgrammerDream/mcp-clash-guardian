from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / "config"
DEFAULT_CONFIG_PATH = CONFIG_DIR / "default.json"
LOCAL_CONFIG_PATH = CONFIG_DIR / "local.json"
RUNTIME_DIR = ROOT / "runtime"
STATUS_PATH = RUNTIME_DIR / "status.json"
LOG_PATH = RUNTIME_DIR / "automation.jsonl"

REQUIRED_LOCAL_KEYS = (
    "public_host",
    "profile_path",
    "python_exe",
    "watcher_task_name",
)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def load_config(require_local: bool = True) -> dict[str, Any]:
    if not DEFAULT_CONFIG_PATH.exists():
        raise RuntimeError(f"Default config missing: {DEFAULT_CONFIG_PATH}")
    config = _read_json(DEFAULT_CONFIG_PATH)
    if LOCAL_CONFIG_PATH.exists():
        config.update(_read_json(LOCAL_CONFIG_PATH))
    elif require_local:
        raise RuntimeError(
            f"Local config missing: {LOCAL_CONFIG_PATH}. "
            "Copy config/local.example.json to config/local.json and fill machine-local values."
        )

    if require_local:
        missing = [key for key in REQUIRED_LOCAL_KEYS if not str(config.get(key) or "").strip()]
        if missing:
            raise RuntimeError(f"Local config missing required keys: {', '.join(missing)}")

    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    return config


def save_local_config(values: dict[str, Any]) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    LOCAL_CONFIG_PATH.write_text(
        json.dumps(values, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
