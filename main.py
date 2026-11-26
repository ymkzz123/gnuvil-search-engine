"""그누빌 검색엔진 명령줄 진입점."""

import argparse
import json
import sqlite3
from pathlib import Path

from api import collect, make_server
from indexer import index_documents, initialize
from search import search


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="data/search.sqlite3")
    commands = parser.add_subparsers(dest="command", required=True)
    crawler = commands.add_parser("crawl", help="웹페이지 수집 및 색인")
    crawler.add_argument("url")
    crawler.add_argument("--max-pages", type=int, default=10)
    crawler.add_argument("--max-depth", type=int, default=2)
    crawler.add_argument("--output", type=Path, help="수집한 문서를 저장할 JSON 파일")
    index = commands.add_parser("index", help="JSON 문서 색인")
    index.add_argument("file", type=Path)
    query = commands.add_parser("search", help="관련도순 검색")
    query.add_argument("query")
    query.add_argument("--mode", choices=["AND", "OR"], type=str.upper, default="AND")
    query.add_argument("--limit", type=int, default=10)
    query.add_argument("--offset", type=int, default=0)
    serve = commands.add_parser("serve", help="로컬 JSON API 실행")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--site", action="append", default=[], help="API 수집을 허용할 출처 (반복 가능)")
    args = parser.parse_args()
    try:
        initialize(args.db)
        if args.command == "serve":
            with make_server(("127.0.0.1", args.port), args.db, args.site) as server:
                print(f"JSON API http://127.0.0.1:{server.server_port}", flush=True)
                server.serve_forever()
            return
        if args.command == "crawl":
            docs, result = collect(args.db, args.url, args.max_pages, args.max_depth)
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(json.dumps(docs, ensure_ascii=False, indent=2), encoding="utf-8")
        elif args.command == "index":
            docs = json.loads(args.file.read_text(encoding="utf-8-sig"))
            result = {"indexed": len(index_documents(args.db, docs))}
        else:
            result = search(args.db, args.query, args.mode, args.limit, args.offset)
        print(json.dumps(result, indent=2, ensure_ascii=False))
    except KeyboardInterrupt:
        return
    except (ValueError, OSError, sqlite3.Error) as exc:
        parser.exit(1, f"{exc}\n")


if __name__ == "__main__":
    main()
