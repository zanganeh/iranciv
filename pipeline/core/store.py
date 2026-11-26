"""Reading/writing interim and web output files.

All writes are atomic (temp file + os.replace), so an interrupted run never
leaves a truncated JSON file for the next step or the browser to choke on.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .paths import INTERIM, WEB_DATA


def atomic_write_text(path: Path, text: str) -> None:
    """Write `text` to `path` via a sibling temp file and os.replace."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def save_interim(name: str, data: dict, *, vintage: str | None = None) -> None:
    """Save data/interim/<name>.json. A "_files" key (raw files used) is stored as payload["files"]."""
    data = dict(data)
    files = data.pop("_files", None)
    payload = {
        "name": name,
        "processed": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "vintage": vintage,
        "data": data,
    }
    if files is not None:
        payload["files"] = sorted(str(f) for f in files)
    atomic_write_text(INTERIM / f"{name}.json", json.dumps(payload, separators=(",", ":")))


def load_interim_payload(name: str) -> dict:
    path = INTERIM / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError(f"interim '{name}' missing – run: uv run python -m pipeline process {name}")
    return json.loads(path.read_text())


def load_interim(name: str) -> Any:
    return load_interim_payload(name)["data"]


def write_web(name: str, data: Any) -> None:
    text = json.dumps(data, separators=(",", ":"), allow_nan=False)
    atomic_write_text(WEB_DATA / name, text)
    print(f"    → web/data/{name} ({len(text) / 1e3:.0f} kB)")
