import json
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import contextmanager
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from api import make_server
from crawler import crawl
from indexer import index_documents, initialize
from search import search

SITE = Path(__file__).resolve().parents[1] / "examples" / "site"


@contextmanager
def running(server):
    with server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}"
        finally:
            server.shutdown()
            thread.join()


class EngineTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.db = Path(temp.name) / "test.db"
        initialize(self.db)

    def test_crawl_and_search_api(self):
        handler = partial(SimpleHTTPRequestHandler, directory=str(SITE))
        with running(ThreadingHTTPServer(("127.0.0.1", 0), handler)) as site:
            with running(make_server(("127.0.0.1", 0), self.db, [site])) as api:
                def post(url):
                    request = Request(api + "/crawl", data=json.dumps({"url": url}).encode(),
                                      headers={"Content-Type": "application/json"})
                    with urlopen(request) as response:
                        return json.load(response)

                for _ in range(2):
                    self.assertEqual(post(site + "/index.html"), {"indexed": 6, "errors": []})
                cases = [
                    ("검색엔진", "AND", {"engine", "ranking"}),
                    ("역색인", "AND", {"engine"}),
                    ("검색엔진 역색인", "AND", {"engine"}),
                    ("검색엔진 메모리", "AND", set()),
                    ("검색엔진 메모리", "OR", {"engine", "ranking", "memory", "cache"}),
                    ("bfs", "AND", {"graph"}),
                    ("ＴＦ－ＩＤＦ", "AND", {"ranking"}),
                    ("메모리", "AND", {"memory", "cache"}),
                    ("양자컴퓨터", "OR", set()),
                ]
                for q, mode, expected in cases:
                    with self.subTest(q=q, mode=mode):
                        with urlopen(api + "/search?" + urlencode({"q": q, "mode": mode})) as response:
                            result = json.load(response)
                        self.assertEqual(result["total"], len(expected))
                        self.assertEqual({Path(r["url"]).stem for r in result["results"]}, expected)
                with self.assertRaises(HTTPError) as denied:
                    post("https://example.org/")
                self.assertEqual(denied.exception.code, 403)
                denied.exception.close()
                with self.assertRaises(HTTPError) as invalid:
                    urlopen(api + "/search?q=bfs&limit=0")
                self.assertEqual(invalid.exception.code, 400)
                invalid.exception.close()
            first = search(self.db, "메모리", limit=1)
            self.assertEqual(first["results"][0]["title"], "가상 메모리")
            self.assertEqual(search(self.db, "메모리", offset=1)["results"][0]["title"], "CPU 캐시")
            self.assertEqual(len(crawl(site + "/index.html", max_depth=1).documents), 5)
            self.assertEqual(len(crawl(site + "/index.html", max_pages=2).documents), 2)

    def test_atomic_reindex_and_stable_ids(self):
        doc = {"url": "https://example.org/a", "title": "자료구조", "body": "스택"}
        initial = index_documents(self.db, [doc])[0]
        changed = dict(doc, body="트리")
        with self.assertRaises(ValueError):
            index_documents(self.db, [changed, {}])
        self.assertEqual(search(self.db, "스택")["total"], 1)
        self.assertEqual(index_documents(self.db, [changed])[0]["id"], initial["id"])
        self.assertEqual(search(self.db, "스택")["total"], 0)
        self.assertEqual(search(self.db, "트리")["total"], 1)

    def test_cli_export_import(self):
        folder = self.db.parent
        snapshot = folder / "docs.json"

        def run(db, *args):
            result = subprocess.run(
                [sys.executable, "-X", "utf8", str(SITE.parents[1] / "main.py"), "--db", str(db), *args],
                check=True, capture_output=True, encoding="utf-8")
            return json.loads(result.stdout)

        handler = partial(SimpleHTTPRequestHandler, directory=str(SITE))
        with running(ThreadingHTTPServer(("127.0.0.1", 0), handler)) as site:
            result = run(self.db, "crawl", site + "/index.html", "--output", str(snapshot))
        self.assertEqual(result["indexed"], 6)
        exported = json.loads(snapshot.read_text(encoding="utf-8"))
        self.assertEqual(set(exported[0]), {"id", "url", "title", "body"})
        fresh = folder / "new.db"
        self.assertEqual(run(fresh, "index", str(snapshot)), {"indexed": 6})
        self.assertEqual(run(fresh, "search", "역색인")["results"][0]["title"], "검색엔진 구조")
