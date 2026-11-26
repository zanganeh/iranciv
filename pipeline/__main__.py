"""Command line:  uv run python -m pipeline <command> [names...]

  fetch   [source ...]   download raw files (cached; --force to re-download)
  process [source ...]   raw → data/interim/<source>.json
  build   [builder ...]  interim → web/data/*.json, in dependency order. Up-to-date
                         builders are skipped (--force rebuilds); --deps also runs
                         the upstream builders of the named ones
  check                  validate web/data/*.json and print a data-quality report
  all                    fetch + process + build everything, then check
  list                   show sources and builders
  serve                  serve web/ on http://127.0.0.1:8765
  package                build usaciv.zip: the site to upload, with the source code inside
                         as usaciv-source.zip

--offline: never touch the network. core.fetch.OFFLINE is set, so download() of a
cached file is a no-op and of a missing one (or any url_exists()) raises OfflineError,
and sources skip their discovery requests ("is there a newer year?") and use their
newest cached release instead. 'fetch' then only checks that every needed raw file
is cached, listing the sources that are incomplete, so 'all --offline' fails
before processing anything when a file is missing. (--force can't be combined.)
"""

from __future__ import annotations

import argparse
import ast
import functools
import http.server
import sys
import time
from pathlib import Path
from types import ModuleType

from .build import BUILDERS
from .core import fetch
from .core.paths import INTERIM, WEB, WEB_DATA
from .core.store import save_interim
from .sources import BY_ID, SOURCES


def _pick(names, registry, kind):
    if not names:
        return list(registry)
    unknown = [n for n in names if n not in registry]
    if unknown:
        sys.exit(f"unknown {kind}: {', '.join(unknown)}  (try: list)")
    return names


def cmd_fetch(names, force=False):
    if fetch.OFFLINE and force:
        sys.exit("--force re-downloads, which --offline forbids")
    missing = []
    for sid in _pick(names, BY_ID, "source"):
        print(f"[fetch] {sid}{' (--offline: checking the cache)' if fetch.OFFLINE else ''}")
        if not fetch.OFFLINE:
            BY_ID[sid].fetch(force=force)
            continue
        try:
            BY_ID[sid].fetch()
        except (fetch.OfflineError, FileNotFoundError) as exc:
            print(f"    ! {exc}")
            missing.append(sid)
    if missing:
        sys.exit(f"--offline: raw files missing for {', '.join(missing)} (run fetch without --offline)")


def cmd_process(names):
    for sid in _pick(names, BY_ID, "source"):
        t = time.time()
        print(f"[process] {sid}")
        data = BY_ID[sid].process()
        # save_interim moves "_files" (raw files used) into the payload for the catalog.
        save_interim(sid, data, vintage=data.pop("vintage", None))
        print(f"    done in {time.time() - t:.1f}s")


# ---------------------------------------------------------------- build planning


def output_path(name: str) -> Path:
    """Where a REQUIRES/PRODUCES name lives: "_x" and sources → data/interim, "x.json" → web/data."""
    if name.startswith("_") or not name.endswith(".json"):
        return INTERIM / f"{name}.json"
    return WEB_DATA / name


def select_builders(names, builders: dict[str, ModuleType], with_deps: bool = False) -> list[str]:
    """Builder names to run, in the registry's dependency order.

    No names → all of them. With `with_deps`, upstream producers of the named
    builders' REQUIRES are added transitively.
    """
    chosen = set(_pick(names, builders, "builder"))
    if with_deps:
        producer = {out: n for n, m in builders.items() for out in m.PRODUCES}
        todo = list(chosen)
        while todo:
            for req in getattr(builders[todo.pop()], "REQUIRES", []):
                up = producer.get(req)
                if up and up not in chosen:
                    chosen.add(up)
                    todo.append(up)
    return [n for n in builders if n in chosen]


def helper_files(module_file: Path, seen: set[Path] | None = None) -> set[Path]:
    """The module file plus sibling modules it imports relatively (e.g. build/yields.py), transitively."""
    module_file = Path(module_file)
    seen = set() if seen is None else seen
    if module_file in seen or not module_file.exists():
        return seen
    seen.add(module_file)
    for node in ast.walk(ast.parse(module_file.read_text())):
        if isinstance(node, ast.ImportFrom) and node.level == 1:
            names = [node.module] if node.module else [a.name for a in node.names]
            for n in names:
                helper_files(module_file.parent / f"{n.split('.')[0]}.py", seen)
    return seen


def is_stale(mod: ModuleType) -> bool:
    """True unless every PRODUCES output exists and is newer than all REQUIRES interims and the code."""
    outs = [output_path(p) for p in mod.PRODUCES]
    if not outs or not all(p.exists() for p in outs):
        return True
    inputs = [output_path(r) for r in getattr(mod, "REQUIRES", [])]
    if not all(p.exists() for p in inputs):
        return True  # let build() fail with its own clear message
    inputs += helper_files(Path(mod.__file__))
    return max((p.stat().st_mtime for p in inputs), default=0) >= min(p.stat().st_mtime for p in outs)


def cmd_build(names, force=False, with_deps=False, builders=None):
    builders = BUILDERS if builders is None else builders
    for name in select_builders(names, builders, with_deps):
        mod = builders[name]
        if not (force or getattr(mod, "RUN_LAST", False) or is_stale(mod)):
            print(f"[build] {name}: up to date (--force to rebuild)")
            continue
        t = time.time()
        print(f"[build] {name}")
        mod.build()
        print(f"    done in {time.time() - t:.1f}s")


def cmd_check() -> int:
    from . import check

    return check.main()


def cmd_list():
    print("sources:")
    for s in SOURCES:
        print(f"  {s.id:20} {s.info.title}")
    print("builders (in order):")
    for name, mod in BUILDERS.items():
        print(f"  {name:20} needs {', '.join(mod.REQUIRES) or '-'}")


def cmd_serve(port=8765):
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(WEB))
    with http.server.ThreadingHTTPServer(("127.0.0.1", port), handler) as srv:
        print(f"serving web/ at http://127.0.0.1:{port}")
        srv.serve_forever()


def main(argv=None):
    p = argparse.ArgumentParser(prog="pipeline", description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("command", choices=["fetch", "process", "build", "check", "all", "list", "serve", "package"])
    p.add_argument("names", nargs="*")
    p.add_argument("--force", action="store_true", help="fetch: re-download even if cached; build: rebuild even if up to date")
    p.add_argument("--deps", action="store_true", help="build: also run upstream builders of the named ones")
    p.add_argument("--offline", action="store_true", help="use cached downloads only; never touch the network")
    p.add_argument("--port", type=int, default=8765)
    a = p.parse_args(argv)
    fetch.OFFLINE = a.offline
    if a.command == "fetch":
        cmd_fetch(a.names, a.force)
    elif a.command == "process":
        cmd_process(a.names)
    elif a.command == "build":
        cmd_build(a.names, a.force, a.deps)
    elif a.command == "check":
        sys.exit(cmd_check())
    elif a.command == "all":
        cmd_fetch([], a.force)
        cmd_process([])
        cmd_build([], a.force)
        if cmd_check():  # WARNs are reported but don't fail the run; FAILs do
            sys.exit(1)
    elif a.command == "list":
        cmd_list()
    elif a.command == "serve":
        cmd_serve(a.port)
    elif a.command == "package":
        from .package import cmd_package

        cmd_package()


if __name__ == "__main__":
    main()
