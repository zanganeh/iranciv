"""Helpers shared by the resource sources (EIA, MSHA): a tiny xlsx reader and checked downloads.

Not a source itself (no Source subclass), so the registry ignores it.
"""

from __future__ import annotations

import http.client
import re
import urllib.error
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Iterator

from ..core import fetch

_M = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_PKG_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"


# --- downloads ----------------------------------------------------------------


def download_checked(source, url: str, rel_path: str, *, force: bool = False, magic: bytes | None = None,
                     binary: bool = False, skip_errors: bool = False) -> Path | None:
    """GET `url` via source.download (which already retries 5xx and network errors).

    Returns None when the file is not there: a 403/404/410, an HTML page instead of a file, or a
    body that does not start with `magic` (EIA redirects missing files to an HTML page, which would
    otherwise be cached as a "spreadsheet"); `binary` also rejects a text/html response up front.
    With `skip_errors`, a failure that outlasted the retries also returns None, so callers can
    move on to their next candidate URL.
    """
    try:
        path = source.download(url, rel_path, force=force, expect_binary=binary)
    except fetch.UnexpectedContentError:
        return None
    except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException) as e:
        if isinstance(e, urllib.error.HTTPError) and e.code in (403, 404, 410):
            return None
        if not skip_errors:
            raise
        print(f"      {e} – skipping {url}")
        return None
    if magic is not None:
        with open(path, "rb") as f:
            if f.read(len(magic)) != magic:
                print(f"      not a {magic!r} file, discarding {rel_path}")
                fetch.discard(source.id, rel_path)
                return None
    return path


# --- xlsx -----------------------------------------------------------------------


def _col(ref: str) -> str:
    return re.match(r"[A-Z]+", ref).group()


def _sheet_path(z: zipfile.ZipFile, sheet: str | None) -> str:
    """Archive path of the named worksheet (first sheet if None)."""
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    sheets = wb.find(_M + "sheets")
    el = sheets[0] if sheet is None else next((s for s in sheets if s.get("name") == sheet), None)
    if el is None:
        raise KeyError(f"sheet {sheet!r} not found")
    rid = el.get(_R + "id")
    rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    target = next(r.get("Target") for r in rels.iter(_PKG_REL + "Relationship") if r.get("Id") == rid)
    return target.lstrip("/") if target.startswith("/xl/") else "xl/" + target


def xlsx_rows(path: Path, sheet: str | None = None) -> Iterator[dict[str, str]]:
    """Yield each row as {column letter: text}. Streams the sheet, so big files are fine.

    Empty cells are simply absent (xlsx omits them), which is why columns are keyed by letter.
    """
    with zipfile.ZipFile(path) as z:
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            for si in ET.fromstring(z.read("xl/sharedStrings.xml")).iter(_M + "si"):
                shared.append("".join(t.text or "" for t in si.iter(_M + "t")))
        with z.open(_sheet_path(z, sheet)) as f:
            for _, el in ET.iterparse(f):
                if el.tag != _M + "row":
                    continue
                row = {}
                for c in el.iter(_M + "c"):
                    v = c.find(_M + "v")
                    t = c.get("t")
                    if t == "s" and v is not None:
                        val = shared[int(v.text)]
                    elif t == "inlineStr":
                        val = "".join(x.text or "" for x in c.iter(_M + "t"))
                    else:
                        val = v.text if v is not None else ""
                    row[_col(c.get("r"))] = (val or "").strip()
                yield row
                el.clear()


def xlsx_records(path: Path, header_cell: str, sheet: str | None = None) -> Iterator[dict[str, str]]:
    """Yield rows as {header: text}. The header is the first row containing `header_cell`
    (title rows above it vary between releases, so no fixed row number)."""
    header = None
    for row in xlsx_rows(path, sheet):
        if header is None and header_cell in row.values():
            header = row
        elif header is not None:
            yield {name: row.get(col, "") for col, name in header.items()}


def num(text: str) -> float | None:
    try:
        return float(text)
    except (TypeError, ValueError):
        return None
