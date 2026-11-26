"""로컬 프론트엔드와 CLI에서 공유하는 JSON API."""

import json
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock
from urllib.parse import parse_qs, urlsplit

from crawler import crawl, normalize_url, origin
from indexer import index_documents
from search import search


def collect(path, url, max_pages, max_depth):
    batch = crawl(url, max_pages, max_depth)
    documents = index_documents(path, batch.documents)
    return documents, {"indexed": len(documents), "errors": batch.errors}


def make_server(address, path, sites):
    allowed = set()
    for site in sites:
        url = normalize_url(site)
        if not url:
            raise ValueError("--site에 HTTP(S) URL이 필요합니다.")
        allowed.add(origin(url))
    crawl_lock = Lock()

    class Handler(BaseHTTPRequestHandler):
        def reply(self, status, data):
            payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            try:
                parts = urlsplit(self.path)
                if parts.path != "/search":
                    return self.reply(404, {"error": "경로를 찾을 수 없습니다."})
                args = parse_qs(parts.query, keep_blank_values=True, max_num_fields=10)
                result = search(path, args.get("q", [""])[0], args.get("mode", ["AND"])[0],
                                int(args.get("limit", [10])[0]), int(args.get("offset", [0])[0]))
            except ValueError as exc:
                return self.reply(400, {"error": str(exc)})
            except sqlite3.Error:
                return self.reply(503, {"error": "색인 데이터베이스를 사용할 수 없습니다."})
            self.reply(200, result)

        def do_POST(self):
            if self.path != "/crawl":
                return self.reply(404, {"error": "경로를 찾을 수 없습니다."})
            if self.headers.get_content_type() != "application/json":
                return self.reply(415, {"error": "application/json 요청이 필요합니다."})
            try:
                length = int(self.headers.get("Content-Length", 0))
                if not 0 < length <= 8192:
                    return self.reply(400, {"error": "JSON 본문은 1~8192바이트여야 합니다."})
                data = json.loads(self.rfile.read(length).decode("utf-8"))
                if not isinstance(data, dict) or not isinstance(data.get("url"), str):
                    raise ValueError("문자열 url이 필요합니다.")
                url = normalize_url(data["url"])
                if not url:
                    raise ValueError("올바른 HTTP(S) URL이 필요합니다.")
                if origin(url) not in allowed:
                    return self.reply(403, {"error": "--site로 등록한 출처만 수집할 수 있습니다."})
                pages, depth = data.get("max_pages", 10), data.get("max_depth", 2)
                if type(pages) is not int or type(depth) is not int:
                    raise ValueError("max_pages와 max_depth에 정수가 필요합니다.")
                if not crawl_lock.acquire(blocking=False):
                    return self.reply(409, {"error": "수집이 이미 진행 중입니다."})
                try:
                    _, result = collect(path, url, pages, depth)
                finally:
                    crawl_lock.release()
            except (ValueError, RecursionError) as exc:
                return self.reply(400, {"error": str(exc)})
            except sqlite3.Error:
                return self.reply(503, {"error": "색인 데이터베이스를 사용할 수 없습니다."})
            self.reply(200, result)

    return ThreadingHTTPServer(address, Handler)
