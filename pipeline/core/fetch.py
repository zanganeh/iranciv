"""Cached downloads with provenance.

Every file a source downloads lands in data/raw/<source_id>/ and is recorded in
that folder's manifest.json (url, sha256, size, retrieval time). The manifest
feeds the in-app "Almanac", so every number on screen can be traced back to
an exact file.

Downloads are retried (RETRIES attempts, exponential backoff) on network errors,
timeouts and HTTP 5xx; a failed attempt never leaves a partial file behind.
download_prefix() is the same for big sorted text files of which only one block
of lines is needed; discard() forgets a cached file that turned out to be bad.

OFFLINE (set by `python -m pipeline ... --offline`) means "use only what is
cached": download() of a missing file and url_exists() raise OfflineError.
Sources should consult it to skip discovery requests when cached data exists.
"""

from __future__ import annotations

import hashlib
import http.client
import itertools
import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .paths import RAW
from .store import atomic_write_text

USER_AGENT = "civ-usa-visualizer/0.1 (open data pipeline)"
OFFLINE = False  # set by the CLI's --offline flag
RETRIES = 3  # total attempts per download
BACKOFF = 2.0  # seconds before the 2nd attempt; doubles each time


class OfflineError(RuntimeError):
    """A network request was needed while running with --offline."""


class UnexpectedContentError(RuntimeError):
    """The server answered 200 but with an HTML page instead of the expected file."""


def _request(url: str, method: str = "GET", headers: dict | None = None) -> urllib.request.Request:
    return urllib.request.Request(url, method=method, headers={"User-Agent": USER_AGENT, **(headers or {})})


def _retryable(exc: BaseException) -> bool:
    """True for transient failures: HTTP 5xx, connection problems and timeouts (not 4xx)."""
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code >= 500
    return isinstance(exc, (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException))


def url_exists(url: str, timeout: int = 30) -> bool:
    """True if the server has `url`. Tries HEAD, then a 1-byte ranged GET (some servers, e.g. EIA, 503 on HEAD)."""
    if OFFLINE:
        raise OfflineError(f"--offline: cannot check {url}")
    for method, headers in (("HEAD", None), ("GET", {"Range": "bytes=0-0"})):
        try:
            with urllib.request.urlopen(_request(url, method, headers), timeout=timeout) as r:
                if r.status in (200, 206):
                    return True
        except urllib.error.HTTPError as exc:
            exc.close()
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            pass
    return False


def manifest_path(source_id: str) -> Path:
    return RAW / source_id / "manifest.json"


def read_manifest(source_id: str) -> dict:
    p = manifest_path(source_id)
    return json.loads(p.read_text()) if p.exists() else {}


def _fetch_to(url: str, tmp: Path, timeout: int, headers: dict | None, expect_binary: bool) -> tuple[str, int]:
    """One download attempt into `tmp`. Returns (sha256 hex, size).

    A body shorter than the declared Content-Length (connection cut mid-transfer)
    raises ConnectionError, so the attempt is retried instead of cached.
    """
    digest = hashlib.sha256()
    size = 0
    with urllib.request.urlopen(_request(url, headers=headers), timeout=timeout) as resp:
        ctype = (resp.headers.get("Content-Type") or "").lower()
        length = resp.headers.get("Content-Length")
        identity = not resp.headers.get("Content-Encoding")
        if expect_binary and ctype.startswith("text/html"):
            raise UnexpectedContentError(f"{url} returned an HTML page (Content-Type {ctype!r}), not a data file")
        with open(tmp, "wb") as out:
            while chunk := resp.read(1 << 20):
                out.write(chunk)
                digest.update(chunk)
                size += len(chunk)
    if length and identity and length.strip().isdigit() and size != int(length):
        raise ConnectionError(f"short read: got {size} of {length} bytes from {url}")
    return digest.hexdigest(), size


def _download_with(
    source_id: str, url: str, rel_path: str, attempt: Callable[[Path], tuple[str, int]], *,
    force: bool, note: str = "", extra: dict | None = None,
) -> Path:
    """Shared body of download() and download_prefix(): cache check, --offline, retries, atomic
    replace and the manifest entry. `attempt(tmp)` writes one try into `tmp` and returns
    (sha256 hex, size); `extra` (a dict, may be filled in by `attempt`) is merged into the entry."""
    dest = RAW / source_id / rel_path
    if dest.exists() and not force:
        return dest
    if OFFLINE:
        raise OfflineError(f"--offline: {source_id}/{rel_path} is not cached ({url})")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    print(f"    ↓ {url}{note}")
    for n in range(1, RETRIES + 1):
        try:
            sha, size = attempt(tmp)
            break
        except BaseException as exc:
            tmp.unlink(missing_ok=True)
            if n == RETRIES or not _retryable(exc):
                raise
            if isinstance(exc, urllib.error.HTTPError):
                exc.close()
            wait = BACKOFF * 2 ** (n - 1)
            print(f"      attempt {n} failed ({exc}); retrying in {wait:g}s")
            time.sleep(wait)
    tmp.replace(dest)

    manifest = read_manifest(source_id)
    manifest[rel_path] = {
        "url": url,
        "sha256": sha,
        "bytes": size,
        "retrieved": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        **(extra or {}),
    }
    atomic_write_text(manifest_path(source_id), json.dumps(manifest, indent=2, sort_keys=True))
    print(f"      {size / 1e6:.1f} MB")
    return dest


def download(
    source_id: str,
    url: str,
    rel_path: str,
    *,
    force: bool = False,
    timeout: int = 600,
    headers: dict | None = None,
    expect_binary: bool = False,
) -> Path:
    """Download `url` to data/raw/<source_id>/<rel_path> unless already cached.

    `headers` are extra request headers. With `expect_binary=True` a text/html
    response is an error (some publishers answer missing files with a 200 page).
    """
    return _download_with(
        source_id, url, rel_path, lambda tmp: _fetch_to(url, tmp, timeout, headers, expect_binary), force=force
    )


def _stream_prefix(url: str, tmp: Path, prefix: bytes, header_start: bytes, timeout: int) -> tuple[str, int, int]:
    """One attempt of download_prefix(). Returns (sha256 hex, bytes, matching rows).

    The block is known to be complete only when a non-matching line follows it. If the stream
    ends first, it must be the true end of the body: with a declared Content-Length (identity
    encoding) a shorter read raises ConnectionError so the attempt is retried, not cached.
    (http.client's readline() returns b"" on a dropped connection instead of raising.)
    """
    digest, size, rows, inside, past_block, received = hashlib.sha256(), 0, 0, False, False, 0
    with urllib.request.urlopen(_request(url), timeout=timeout) as resp, open(tmp, "wb") as out:
        length = resp.headers.get("Content-Length")
        identity = not resp.headers.get("Content-Encoding")
        header = resp.readline()
        received += len(header)
        if not header.startswith(header_start):
            raise UnexpectedContentError(f"{url}: unexpected first line {header[:80]!r} (wanted {header_start!r}…)")
        for line in itertools.chain([header], iter(resp.readline, b"")):  # lazily: stop early
            if line is not header:
                received += len(line)
            is_match = line.startswith(prefix)
            if inside and not is_match:
                past_block = True
                break  # past the block: the rest of the file isn't needed
            inside = inside or is_match
            rows += is_match
            out.write(line)
            digest.update(line)
            size += len(line)
    if not past_block:  # reached EOF: make sure it was the real end of the body
        if length and identity and length.strip().isdigit() and received != int(length):
            raise ConnectionError(f"short read: stream ended after {received} of {length} bytes from {url}")
        # (a chunked body cut short raises IncompleteRead inside http.client: retried as well)
    if not rows:
        raise UnexpectedContentError(f"{url}: no rows starting with {prefix!r}")
    return digest.hexdigest(), size, rows


def download_prefix(
    source_id: str,
    url: str,
    rel_path: str,
    prefix: str,
    *,
    header_start: str = "",
    force: bool = False,
    timeout: int = 600,
) -> Path:
    """Like download(), but stop reading the response as soon as the contiguous block of lines
    that start with `prefix` has ended: the file keeps everything up to the end of that block.

    For big line-sorted files where one block is needed (e.g. the county rows of an ACS
    5-year table). The first line must start with `header_start` (publishers sometimes
    answer with an HTML page and a 2xx status). The manifest entry gets a "partial" note,
    and its sha256/bytes describe the saved prefix, not the whole remote file.
    """
    extra: dict = {}

    def attempt(tmp: Path) -> tuple[str, int]:
        sha, size, rows = _stream_prefix(url, tmp, prefix.encode(), header_start.encode(), timeout)
        extra["partial"] = f"header + rows up to the end of the {prefix} block ({rows} rows)"
        return sha, size

    return _download_with(
        source_id, url, rel_path, attempt, force=force, note=f" (rows {prefix}* only)", extra=extra
    )


def discard(source_id: str, rel_path: str) -> None:
    """Delete a bad cached download and its manifest entry, so the next fetch retries it."""
    (RAW / source_id / rel_path).unlink(missing_ok=True)
    manifest = read_manifest(source_id)
    if manifest.pop(rel_path, None) is not None:
        atomic_write_text(manifest_path(source_id), json.dumps(manifest, indent=2, sort_keys=True))
