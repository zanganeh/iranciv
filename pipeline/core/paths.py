"""Well-known project directories."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
RAW = DATA / "raw"  # untouched downloads, one folder per source (git-ignored)
INTERIM = DATA / "interim"  # per-source cleaned JSON (git-ignored)
WEB = ROOT / "web"
WEB_DATA = WEB / "data"  # what the browser loads
