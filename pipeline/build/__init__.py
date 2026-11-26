"""Builders, discovered automatically and run in dependency order.

A builder is any module in this package that defines:

    REQUIRES: list[str]   # interim names it reads (sources, or "_name" interims made by other builders)
    PRODUCES: list[str]   # "_name" interims and/or web/data file names it writes
    def build() -> None

Optionally RUN_LAST = True (e.g. the catalog, which describes everything else).
Modules without a `build` function (e.g. yields.py) are helpers, not builders.
"""

from __future__ import annotations

import importlib
import pkgutil
from types import ModuleType


def _discover() -> dict[str, ModuleType]:
    mods = {}
    for m in pkgutil.iter_modules(__path__):
        mod = importlib.import_module(f"{__name__}.{m.name}")
        if callable(getattr(mod, "build", None)) and hasattr(mod, "PRODUCES"):
            mods[m.name] = mod
    return mods


def _order(mods: dict[str, ModuleType]) -> list[str]:
    producer = {out: name for name, mod in mods.items() for out in mod.PRODUCES}
    deps = {name: {producer[r] for r in getattr(mod, "REQUIRES", []) if r in producer and producer[r] != name} for name, mod in mods.items()}
    ordered, seen, active = [], set(), set()

    def visit(n):
        if n in seen:
            return
        if n in active:
            raise RuntimeError(f"builder dependency cycle at {n}")
        active.add(n)
        for d in sorted(deps[n]):
            visit(d)
        active.discard(n)
        seen.add(n)
        ordered.append(n)

    for n in sorted(mods, key=lambda n: (getattr(mods[n], "RUN_LAST", False), n)):
        visit(n)
    last = [n for n in ordered if getattr(mods[n], "RUN_LAST", False)]
    return [n for n in ordered if n not in last] + last


_MODS = _discover()
BUILDERS = {name: _MODS[name] for name in _order(_MODS)}


def producers_of(outputs) -> list[str]:
    """Builder names producing any of the given interim/web names."""
    return [n for n, m in BUILDERS.items() if set(m.PRODUCES) & set(outputs)]
