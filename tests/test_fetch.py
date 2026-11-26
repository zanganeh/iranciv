"""core.fetch against a local HTTP server: retries, atomic files, HTML guard, url_exists, offline."""

import hashlib
import http.client
import http.server
import json
import os
import tempfile
import threading
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from pipeline.core import fetch

BODY = b"col_a,col_b\n1,2\n" * 100


class Handler(http.server.BaseHTTPRequestHandler):
    hits: dict = {}
    seen_headers: dict = {}

    def log_message(self, *args):
        pass

    def _send(self, code, body=b"", ctype="application/octet-stream"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _route(self):
        path = self.path
        n = Handler.hits[path] = Handler.hits.get(path, 0) + 1
        Handler.seen_headers[path] = dict(self.headers)
        if path == "/ok":
            return self._send(200, BODY)
        if path == "/flaky":  # fails twice, then works
            return self._send(503) if n <= 2 else self._send(200, BODY)
        if path == "/down":
            return self._send(500)
        if path == "/missing":
            return self._send(404)
        if path == "/html":
            return self._send(200, b"<html>not found</html>", "text/html; charset=utf-8")
        if path in ("/short", "/short-once"):  # promise more bytes than sent, then hang up
            if path == "/short" or n == 1:
                self.send_response(200)
                self.send_header("Content-Length", str(len(BODY)))
                self.end_headers()
                self.wfile.write(BODY[:10])
                self.close_connection = True
                return
            return self._send(200, BODY)
        if path == "/table":  # sorted, pipe-delimited like an ACS table
            body = b"GEO_ID|X\n0400000US01|1\n0500000US01001|2\n0500000US01003|3\n0600000US0100|4\n" + b"9" * 10000
            return self._send(200, body, "text/plain")
        if path in ("/cut-table", "/cut-table-once"):  # declares 500 bytes, hangs up inside the block
            if path == "/cut-table" or n == 1:
                self.send_response(200)
                self.send_header("Content-Length", "500")
                self.end_headers()
                self.wfile.write(b"GEO_ID|X\n0400000US01|1\n0500000US01001|2\n0500000US01003|3\n")
                self.close_connection = True
                return
            return self._send(200, b"GEO_ID|X\n0500000US01001|2\n0500000US01003|3\n0500000US01005|4\n", "text/plain")
        if path == "/table-last":  # the block runs to the (complete) end of the file
            return self._send(200, b"GEO_ID|X\n0400000US01|1\n0500000US01001|2\n", "text/plain")
        if path == "/flaky-table":
            return self._send(503) if n == 1 else self._send(200, b"GEO_ID|X\n0500000US01001|2\n", "text/plain")
        if path == "/nohead":  # like EIA: HEAD is refused, a ranged GET works
            if self.command == "HEAD":
                return self._send(503)
            return self._send(206 if "Range" in self.headers else 200, BODY[:1])
        self._send(404)

    do_GET = _route
    do_HEAD = _route


def setUpModule():
    os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"


class FetchTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        Handler.hits.clear()
        self.tmp = tempfile.TemporaryDirectory()
        self.raw = Path(self.tmp.name)
        patches = [mock.patch.object(fetch, "RAW", self.raw), mock.patch.object(fetch, "BACKOFF", 0.0),
                   mock.patch.object(fetch, "OFFLINE", False)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)

    def _dl(self, path, rel="f.csv", **kw):
        try:
            with mock.patch("builtins.print"):
                return fetch.download("src", self.base + path, rel, timeout=5, **kw)
        except urllib.error.HTTPError as exc:
            exc.close()
            raise

    def _leftovers(self):
        return sorted(p.name for p in (self.raw / "src").glob("*") if p.name.endswith((".part", ".tmp")) or p.name.startswith("."))

    def test_download_records_manifest(self):
        dest = self._dl("/ok")
        self.assertEqual(dest.read_bytes(), BODY)
        m = fetch.read_manifest("src")["f.csv"]
        self.assertEqual(m["sha256"], hashlib.sha256(BODY).hexdigest())
        self.assertEqual(m["bytes"], len(BODY))
        self.assertEqual(m["url"], self.base + "/ok")
        self.assertEqual(self._leftovers(), [])

    def test_cached_file_is_not_refetched(self):
        self._dl("/ok")
        self._dl("/ok")
        self.assertEqual(Handler.hits["/ok"], 1)
        self._dl("/ok", force=True)
        self.assertEqual(Handler.hits["/ok"], 2)

    def test_retries_transient_5xx(self):
        dest = self._dl("/flaky")
        self.assertEqual(dest.read_bytes(), BODY)
        self.assertEqual(Handler.hits["/flaky"], 3)

    def test_gives_up_after_retries_and_cleans_up(self):
        with self.assertRaises(urllib.error.HTTPError):
            self._dl("/down")
        self.assertEqual(Handler.hits["/down"], fetch.RETRIES)
        self.assertFalse((self.raw / "src" / "f.csv").exists())
        self.assertEqual(self._leftovers(), [])
        self.assertEqual(fetch.read_manifest("src"), {})

    def test_4xx_is_not_retried(self):
        with self.assertRaises(urllib.error.HTTPError):
            self._dl("/missing")
        self.assertEqual(Handler.hits["/missing"], 1)

    def test_failed_redownload_keeps_cached_file_and_manifest(self):
        self._dl("/ok")
        before = fetch.read_manifest("src")
        with self.assertRaises(urllib.error.HTTPError):
            self._dl("/down", force=True)
        self.assertEqual((self.raw / "src" / "f.csv").read_bytes(), BODY)
        self.assertEqual(fetch.read_manifest("src"), before)
        self.assertEqual(self._leftovers(), [])

    def test_truncated_body_is_retried_not_cached(self):
        with self.assertRaises(ConnectionError):
            self._dl("/short", "f.bin")
        self.assertEqual(Handler.hits["/short"], fetch.RETRIES)
        self.assertFalse((self.raw / "src" / "f.bin").exists())
        self.assertEqual(self._leftovers(), [])
        self.assertEqual(fetch.read_manifest("src"), {})

    def test_truncated_body_then_full(self):
        self.assertEqual(self._dl("/short-once", "f.bin").read_bytes(), BODY)
        self.assertEqual(Handler.hits["/short-once"], 2)
        self.assertEqual(fetch.read_manifest("src")["f.bin"]["bytes"], len(BODY))

    def test_html_guard(self):
        with self.assertRaises(fetch.UnexpectedContentError):
            self._dl("/html", "x.zip", expect_binary=True)
        self.assertFalse((self.raw / "src" / "x.zip").exists())
        self.assertEqual(Handler.hits["/html"], 1)
        self.assertTrue(self._dl("/html", "page.html").exists())  # fine when not expecting binary

    def test_extra_headers_are_sent(self):
        self._dl("/ok", headers={"X-Test": "yes"})
        self.assertEqual(Handler.seen_headers["/ok"].get("X-Test"), "yes")

    def test_manifest_write_is_atomic_json(self):
        self._dl("/ok", "a.csv")
        self._dl("/ok", "b.csv")
        self.assertEqual(sorted(json.loads((self.raw / "src" / "manifest.json").read_text())), ["a.csv", "b.csv"])

    def test_url_exists(self):
        self.assertTrue(fetch.url_exists(self.base + "/ok", timeout=5))
        self.assertTrue(fetch.url_exists(self.base + "/nohead", timeout=5))
        self.assertEqual(Handler.seen_headers["/nohead"].get("Range"), "bytes=0-0")
        self.assertFalse(fetch.url_exists(self.base + "/missing", timeout=5))

    def test_offline(self):
        self._dl("/ok")
        with mock.patch.object(fetch, "OFFLINE", True):
            self.assertTrue(self._dl("/ok").exists())  # cached: fine
            with self.assertRaises(fetch.OfflineError):
                self._dl("/ok", "other.csv")
            with self.assertRaises(fetch.OfflineError):
                fetch.url_exists(self.base + "/ok")
        self.assertEqual(Handler.hits["/ok"], 1)

    def _prefix(self, path, rel="t.dat", **kw):
        with mock.patch("builtins.print"):
            return fetch.download_prefix("src", self.base + path, rel, "0500000US", header_start="GEO_ID|", timeout=5, **kw)

    def test_download_prefix_keeps_header_and_block(self):
        dest = self._prefix("/table")
        want = b"GEO_ID|X\n0400000US01|1\n0500000US01001|2\n0500000US01003|3\n"  # up to the block's end
        self.assertEqual(dest.read_bytes(), want)
        m = fetch.read_manifest("src")["t.dat"]
        self.assertEqual(m["sha256"], hashlib.sha256(want).hexdigest())
        self.assertEqual(m["bytes"], len(want))
        self.assertIn("2 rows", m["partial"])
        self._prefix("/table")  # cached
        self.assertEqual(Handler.hits["/table"], 1)
        self.assertEqual(self._leftovers(), [])

    def test_download_prefix_rejects_wrong_header_and_retries(self):
        with self.assertRaises(fetch.UnexpectedContentError):
            self._prefix("/html", "h.dat")
        self.assertEqual(Handler.hits["/html"], 1)
        self.assertFalse((self.raw / "src" / "h.dat").exists())
        self.assertTrue(self._prefix("/flaky-table", "f.dat").exists())
        self.assertEqual(Handler.hits["/flaky-table"], 2)
        with mock.patch.object(fetch, "OFFLINE", True), self.assertRaises(fetch.OfflineError):
            self._prefix("/table", "other.dat")

    def test_download_prefix_dropped_connection_is_retried_not_cached(self):
        # readline() returns b"" on a dropped connection; the short body must not be cached.
        with self.assertRaises(ConnectionError):
            self._prefix("/cut-table", "c.dat")
        self.assertEqual(Handler.hits["/cut-table"], fetch.RETRIES)
        self.assertFalse((self.raw / "src" / "c.dat").exists())
        self.assertEqual(fetch.read_manifest("src"), {})
        self.assertEqual(self._leftovers(), [])
        dest = self._prefix("/cut-table-once", "d.dat")  # a retry that gets the whole block
        self.assertIn(b"0500000US01005", dest.read_bytes())
        self.assertIn("3 rows", fetch.read_manifest("src")["d.dat"]["partial"])

    def test_download_prefix_block_at_end_of_file(self):
        self.assertEqual(self._prefix("/table-last").read_bytes(), b"GEO_ID|X\n0400000US01|1\n0500000US01001|2\n")

    def test_discard(self):
        self._dl("/ok", "a.csv")
        self._dl("/ok", "b.csv")
        fetch.discard("src", "a.csv")
        self.assertFalse((self.raw / "src" / "a.csv").exists())
        self.assertEqual(sorted(fetch.read_manifest("src")), ["b.csv"])
        fetch.discard("src", "never.csv")  # no-op
        self._dl("/ok", "a.csv")  # re-downloaded
        self.assertEqual(Handler.hits["/ok"], 3)

    def test_retryable_classification(self):
        for code, expect in ((503, True), (500, True), (404, False)):
            exc = urllib.error.HTTPError("u", code, "x", {}, None)
            self.addCleanup(exc.close)
            self.assertIs(fetch._retryable(exc), expect)
        self.assertTrue(fetch._retryable(urllib.error.URLError("refused")))
        self.assertTrue(fetch._retryable(TimeoutError()))
        self.assertTrue(fetch._retryable(http.client.IncompleteRead(b"x", 10)))
        self.assertTrue(fetch._retryable(ConnectionError("short read")))
        self.assertFalse(fetch._retryable(fetch.UnexpectedContentError("html")))
        self.assertFalse(fetch._retryable(KeyboardInterrupt()))


if __name__ == "__main__":
    unittest.main()
