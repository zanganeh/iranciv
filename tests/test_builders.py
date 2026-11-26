"""Builder discovery, dependency ordering, selection and staleness skipping."""

import os
import tempfile
import time
import unittest
from pathlib import Path
from types import ModuleType
from unittest import mock

import pipeline.__main__ as cli
from pipeline.build import BUILDERS, _order


def fake(name, requires, produces, run_last=False, file=None, log=None):
    m = ModuleType(name)
    m.REQUIRES, m.PRODUCES = requires, produces
    if run_last:
        m.RUN_LAST = True
    m.__file__ = str(file) if file else __file__

    def build():
        if log is not None:
            log.append(name)
        for out in produces:
            p = cli.output_path(out)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("{}")

    m.build = build
    return m


class RealRegistryTest(unittest.TestCase):
    def test_discovered_builders(self):
        self.assertIn("world", BUILDERS)
        self.assertIn("catalog", BUILDERS)
        self.assertNotIn("yields", BUILDERS, "helpers without build() are not builders")
        for name, mod in BUILDERS.items():
            self.assertTrue(callable(mod.build), name)
            self.assertIsInstance(mod.PRODUCES, list, name)

    def test_real_order_respects_requires(self):
        names = list(BUILDERS)
        producer = {out: n for n, m in BUILDERS.items() for out in m.PRODUCES}
        for n, m in BUILDERS.items():
            for req in m.REQUIRES:
                if req in producer and producer[req] != n:
                    self.assertLess(names.index(producer[req]), names.index(n), f"{producer[req]} before {n}")
        last = [n for n, m in BUILDERS.items() if getattr(m, "RUN_LAST", False)]
        self.assertEqual(names[len(names) - len(last):], last)


class PlanningTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        for p in (mock.patch.object(cli, "INTERIM", root / "interim"), mock.patch.object(cli, "WEB_DATA", root / "web")):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)
        self.root = root
        self.log = []
        code = root / "code"
        code.mkdir()
        (code / "helper.py").write_text("X = 1\n")
        (code / "a.py").write_text("from .helper import X\n")
        (code / "b.py").write_text("import json\n")
        (code / "c.py").write_text("from . import helper\n")
        mods = {
            "c": fake("c", ["_b", "src2"], ["c.json"], file=code / "c.py", log=self.log),
            "cat": fake("cat", [], ["sources.json"], run_last=True, file=code / "b.py", log=self.log),
            "a": fake("a", ["src1"], ["_a", "a.json"], file=code / "a.py", log=self.log),
            "b": fake("b", ["_a"], ["_b"], file=code / "b.py", log=self.log),
        }
        self.builders = {n: mods[n] for n in _order(mods)}
        self.code = code
        for src in ("src1", "src2"):
            self._touch(cli.output_path(src), -100)

    def _touch(self, path, age=0.0):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}")
        t = time.time() + age
        os.utime(path, (t, t))

    def _build(self, names=(), **kw):
        self.log.clear()
        with mock.patch("builtins.print"):
            cli.cmd_build(list(names), builders=self.builders, **kw)
        return list(self.log)

    def test_order(self):
        self.assertEqual(list(self.builders), ["a", "b", "c", "cat"])

    def test_cycle_detected(self):
        with self.assertRaises(RuntimeError):
            _order({"x": fake("x", ["_y"], ["_x"]), "y": fake("y", ["_x"], ["_y"])})

    def test_select(self):
        self.assertEqual(cli.select_builders([], self.builders), ["a", "b", "c", "cat"])
        self.assertEqual(cli.select_builders(["c", "a"], self.builders), ["a", "c"])
        self.assertEqual(cli.select_builders(["c"], self.builders, with_deps=True), ["a", "b", "c"])
        with self.assertRaises(SystemExit), mock.patch("sys.stderr"):
            cli.select_builders(["nope"], self.builders)

    def test_output_paths(self):
        self.assertEqual(cli.output_path("_world"), self.root / "interim" / "_world.json")
        self.assertEqual(cli.output_path("acs_5yr"), self.root / "interim" / "acs_5yr.json")
        self.assertEqual(cli.output_path("world.json"), self.root / "web" / "world.json")

    def test_helper_files(self):
        self.assertEqual(cli.helper_files(self.code / "a.py"), {self.code / "a.py", self.code / "helper.py"})
        self.assertEqual(cli.helper_files(self.code / "c.py"), {self.code / "c.py", self.code / "helper.py"})
        self.assertEqual(cli.helper_files(self.code / "b.py"), {self.code / "b.py"})

    def test_staleness(self):
        for f in self.code.iterdir():
            os.utime(f, (time.time() - 200, time.time() - 200))
        self.assertEqual(self._build(), ["a", "b", "c", "cat"])
        self.assertEqual(self._build(), ["cat"], "up to date; RUN_LAST always runs")
        self.assertEqual(self._build(force=True), ["a", "b", "c", "cat"])

        self._touch(cli.output_path("src2"), +50)  # newer input for c only
        self.assertEqual(self._build(), ["c", "cat"])

        os.utime(self.code / "helper.py", (time.time() + 100, time.time() + 100))  # helper changed → a and c
        self.assertEqual(self._build(), ["a", "b", "c", "cat"], "a's new _a makes b stale, b's _b makes c stale")

        cli.output_path("c.json").unlink()  # missing output
        self.assertEqual(self._build(["c"]), ["c"])

    def test_missing_input_is_stale(self):
        cli.output_path("src1").unlink()
        self.assertTrue(cli.is_stale(self.builders["a"]))



class OfflineCliTest(unittest.TestCase):
    def test_offline_fetch_checks_the_cache(self):
        """--offline: 'fetch' (and the fetch step of 'all') calls each source with fetch.OFFLINE set, so
        it only confirms the cache; a source with a missing file stops 'all' before processing."""
        calls = []

        def ok(force=False):
            calls.append(("ok", cli.fetch.OFFLINE, force))

        def missing(force=False):
            calls.append(("missing", cli.fetch.OFFLINE, force))
            raise cli.fetch.OfflineError("--offline: m/x.csv is not cached")

        good = mock.Mock(fetch=ok)
        with mock.patch.dict(cli.BY_ID, {"x": good}, clear=True), mock.patch("builtins.print"), \
                mock.patch.object(cli.fetch, "OFFLINE", False):
            cli.main(["fetch", "--offline"])
            self.assertEqual(calls, [("ok", True, False)])
            with mock.patch.object(cli, "cmd_process") as proc, mock.patch.object(cli, "cmd_build") as build, \
                    mock.patch.object(cli, "cmd_check", return_value=0):
                cli.main(["all", "--offline"])
            proc.assert_called_once()
            build.assert_called_once()
            with self.assertRaises(SystemExit):
                cli.main(["fetch", "--offline", "--force"])
            cli.main(["fetch"])
            self.assertEqual(calls[-1], ("ok", False, False))

        calls.clear()
        with mock.patch.dict(cli.BY_ID, {"m": mock.Mock(fetch=missing), "x": good}, clear=True), \
                mock.patch("builtins.print"), mock.patch.object(cli.fetch, "OFFLINE", False), \
                mock.patch.object(cli, "cmd_process") as proc:
            with self.assertRaises(SystemExit) as cm:
                cli.main(["all", "--offline"])
            self.assertIn("m", str(cm.exception.code))
            proc.assert_not_called()
            self.assertEqual([c[0] for c in calls], ["missing", "ok"])  # every source is checked


if __name__ == "__main__":
    unittest.main()
