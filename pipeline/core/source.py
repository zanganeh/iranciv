"""The contract every data source implements.

A source does two things:

* ``fetch``   – download raw files (cached; see core.fetch)
* ``process`` – turn raw files into a tidy JSON-able dict, saved as
                data/interim/<id>.json

Sources know nothing about hexes, cities or the UI. Combining sources into
what the map shows is the job of the builders in pipeline/build/.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import fetch
from .paths import RAW


@dataclass(frozen=True)
class SourceInfo:
    id: str
    title: str
    publisher: str
    homepage: str
    license: str
    description: str
    used_for: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


class Source:
    info: SourceInfo

    @property
    def id(self) -> str:
        return self.info.id

    @property
    def raw_dir(self) -> Path:
        return RAW / self.id

    @property
    def offline(self) -> bool:
        """True under --offline: use cached files only and skip discovery requests."""
        return fetch.OFFLINE

    def download(
        self, url: str, rel_path: str, *, force: bool = False, headers: dict | None = None, expect_binary: bool = False
    ) -> Path:
        return fetch.download(self.id, url, rel_path, force=force, headers=headers, expect_binary=expect_binary)

    def download_prefix(self, url: str, rel_path: str, prefix: str, *, header_start: str = "", force: bool = False) -> Path:
        """Header + the block of lines starting with `prefix` only (see fetch.download_prefix)."""
        return fetch.download_prefix(self.id, url, rel_path, prefix, header_start=header_start, force=force)

    def discard(self, rel_path: str) -> None:
        """Forget a bad cached file (and its manifest entry) so the next fetch retries it."""
        fetch.discard(self.id, rel_path)

    def fetch(self, force: bool = False) -> None:
        raise NotImplementedError

    def process(self) -> dict:
        """Return processed data.

        May include "vintage" (e.g. "ACS 2025 1-year") and "_files" (raw files actually
        used, relative to data/raw/<id>/; the Almanac cites only those).
        """
        raise NotImplementedError
