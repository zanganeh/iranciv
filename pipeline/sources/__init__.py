"""Registry of data sources, discovered automatically.

To add a source, drop a module in this package containing a Source subclass
with an `info = SourceInfo(...)` class attribute. Nothing else to register.
"""

from __future__ import annotations

import importlib
import pkgutil

from ..core.source import Source


def _discover() -> list[Source]:
    found: dict[str, Source] = {}
    for m in pkgutil.iter_modules(__path__):
        mod = importlib.import_module(f"{__name__}.{m.name}")
        for obj in vars(mod).values():
            if (
                isinstance(obj, type)
                and issubclass(obj, Source)
                and obj.__module__ == mod.__name__
                and getattr(obj, "info", None) is not None
            ):
                inst = obj()
                if inst.id in found:
                    raise RuntimeError(f"duplicate source id {inst.id!r}")
                found[inst.id] = inst
    return sorted(found.values(), key=lambda s: s.id)


SOURCES = _discover()
BY_ID = {s.id: s for s in SOURCES}
