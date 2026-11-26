"""Builder-level checks (check(web_dir) in pipeline/build modules) and categorical lenses in pipeline.check."""

import copy
import json
import tempfile
import unittest
from pathlib import Path

from pipeline import check

from test_check import dataset


def write(web, interim, tmp):
    wd, idir = Path(tmp) / "web", Path(tmp) / "interim"
    wd.mkdir()
    idir.mkdir()
    for name, doc in web.items():
        if doc is not None:
            (wd / name).write_text(json.dumps(doc))
    for name, doc in interim.items():
        (idir / f"{name}.json").write_text(json.dumps(doc))
    return wd, idir


class BuilderChecksTest(unittest.TestCase):
    def run_with(self, checks, web=None, interim=None):
        if web is None:
            web, interim = dataset()
        with tempfile.TemporaryDirectory() as tmp:
            wd, idir = write(web, interim, tmp)
            return check.run(wd, idir, quiet=True, builder_checks_=checks)

    def test_lines_are_added_with_the_builder_name(self):
        seen = []

        def good(web_dir):
            seen.append(Path(web_dir).name)
            return [("PASS", "file ok"), ("note", "lower-case level"), ("WARN", "something odd")]

        rep = self.run_with([("demo", good)])
        self.assertEqual(seen, ["web"])
        self.assertIn(("PASS", "demo: file ok"), rep.lines)
        self.assertIn(("NOTE", "demo: lower-case level"), rep.lines)
        self.assertIn(("WARN", "demo: something odd"), rep.lines)
        self.assertFalse(rep.failed)

    def test_fail_line_fails_the_run(self):
        rep = self.run_with([("demo", lambda wd: [("FAIL", "broken")])])
        self.assertTrue(rep.failed)
        self.assertIn(("FAIL", "demo: broken"), rep.lines)

    def test_raising_or_malformed_check_is_a_fail_and_others_still_run(self):
        def boom(wd):
            raise ValueError("bad file")

        rep = self.run_with([("a", boom), ("b", lambda wd: ["not a pair"]), ("c", lambda wd: [("MAYBE", "x")]),
                             ("d", lambda wd: [("PASS", "still runs")]), ("e", lambda wd: None)])
        fails = [m for lv, m in rep.lines if lv == "FAIL"]
        self.assertTrue(any(m.startswith("a: check() raised ValueError") for m in fails))
        self.assertTrue(any(m.startswith("b:") for m in fails))
        self.assertTrue(any(m.startswith("c: unknown level") for m in fails))
        self.assertIn(("PASS", "d: still runs"), rep.lines)

    def test_runs_even_when_world_is_unusable(self):
        web, interim = dataset()
        web["world.json"] = None
        rep = self.run_with([("demo", lambda wd: [("NOTE", "ran")])], web, interim)
        self.assertIn(("NOTE", "demo: ran"), rep.lines)

    def test_discovery_finds_check_functions_in_build_modules(self):
        found = check.builder_checks()
        self.assertTrue(all(callable(fn) and isinstance(name, str) for name, fn in found))
        import pipeline.build as b

        for name, _ in found:
            self.assertTrue(callable(getattr(__import__(f"{b.__name__}.{name}", fromlist=["check"]), "check")))

    def test_real_builder_checks_do_not_fail_on_a_dataset_without_their_files(self):
        # A builder's check() must report its missing output as a NOTE, never a FAIL or WARN (see check.py).
        rep = self.run_with(None)
        names = {n for n, _ in check.builder_checks()}
        bad = [(lv, m) for lv, m in rep.lines if lv in ("FAIL", "WARN") and m.split(":")[0] in names]
        self.assertEqual(bad, [])


class CategoricalLensTest(unittest.TestCase):
    def lens(self):
        return {"id": "faith_tradition", "group": "people", "name": "Largest tradition", "icon": "l-faith", "emoji": "*",
                "unit": "", "format": "category", "scale": "linear", "domain": [0, 2],
                "ramp": ["#aa0000", "#00aa00", "#0000aa"],
                "categories": [{"id": "a", "name": "A", "color": "#aa0000"}, {"id": "b", "name": "B", "color": "#00aa00"},
                               {"id": "c", "name": "C", "color": "#0000aa"}],
                "note": "Counts congregations' adherents, not residents' beliefs.",
                "higher_is": "neutral", "source": "acs_5yr", "description": "d", "values": [0, 1, None, 2, None, None]}

    def run_lens(self, lens):
        web, interim = dataset()
        web["lenses.json"]["lenses"].append(lens)
        with tempfile.TemporaryDirectory() as tmp:
            wd, idir = write(web, interim, tmp)
            return check.run(wd, idir, quiet=True, builder_checks_=[])

    def fails(self, rep):
        return [m for lv, m in rep.lines if lv == "FAIL"]

    def test_valid_categorical_lens_passes(self):
        self.assertEqual(self.fails(self.run_lens(self.lens())), [])

    def test_categorical_violations(self):
        cases = {
            "values": lambda l: l.update(values=[0, 1.5, None, 3, None, None]),
            "ramp": lambda l: l.update(ramp=["#aa0000", "#0000aa", "#00aa00"]),
            "domain": lambda l: l.update(domain=[0, 3]),
            "categories": lambda l: l.update(categories=[{"id": "a", "name": "A"}]),
            "note": lambda l: l.update(note=5),
        }
        for what, change in cases.items():
            lens = copy.deepcopy(self.lens())
            change(lens)
            with self.subTest(what=what):
                self.assertTrue(self.fails(self.run_lens(lens)), what)

    def test_categories_only_on_categorical_lenses(self):
        lens = self.lens()
        lens.update(format="float")
        self.assertTrue(any("only for format" in m for m in self.fails(self.run_lens(lens))))


if __name__ == "__main__":
    unittest.main()
