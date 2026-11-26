"""core.store: atomic writes and the "_files" convention."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pipeline.core import store


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        for p in (mock.patch.object(store, "INTERIM", root / "interim"), mock.patch.object(store, "WEB_DATA", root / "web")):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)
        self.root = root

    def test_save_interim_moves_files(self):
        data = {"rows": [1, 2], "_files": ["2024/b.dat", "2024/a.dat"]}
        store.save_interim("src", data, vintage="v1")
        payload = store.load_interim_payload("src")
        self.assertEqual(payload["files"], ["2024/a.dat", "2024/b.dat"])
        self.assertEqual(payload["data"], {"rows": [1, 2]})
        self.assertEqual(payload["vintage"], "v1")
        self.assertIn("_files", data, "caller's dict must not be mutated")

    def test_save_interim_without_files(self):
        store.save_interim("src", {"a": 1})
        self.assertNotIn("files", store.load_interim_payload("src"))
        self.assertEqual(store.load_interim("src"), {"a": 1})

    def test_missing_interim_message(self):
        with self.assertRaisesRegex(FileNotFoundError, "pipeline process nope"):
            store.load_interim("nope")

    def test_write_web_and_no_temp_left(self):
        with mock.patch("builtins.print"):
            store.write_web("x.json", {"a": [1, None]})
        self.assertEqual(json.loads((self.root / "web" / "x.json").read_text()), {"a": [1, None]})
        self.assertEqual([p.name for p in (self.root / "web").iterdir()], ["x.json"])

    def test_write_web_rejects_nan(self):
        with self.assertRaises(ValueError):
            store.write_web("x.json", {"a": float("nan")})
        self.assertFalse((self.root / "web" / "x.json").exists())

    def test_interrupted_write_keeps_old_file(self):
        target = self.root / "keep.json"
        store.atomic_write_text(target, '{"old": true}')
        with mock.patch.object(os, "replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                store.atomic_write_text(target, '{"new": true}')
        self.assertEqual(target.read_text(), '{"old": true}')
        self.assertEqual([p.name for p in self.root.iterdir()], ["keep.json"])


if __name__ == "__main__":
    unittest.main()
