"""같은 출처의 HTML 문서를 너비 우선으로 수집한다."""

import re
from collections import deque
from dataclasses import dataclass, field
from html.parser import HTMLParser
from http.client import HTTPException
from urllib.error import HTTPError
from urllib.parse import quote, urljoin, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from urllib.robotparser import RobotFileParser

AGENT = "GnuvilSearchBot"
MAX_BYTES = 2 * 1024 * 1024


def normalize_url(value):
    try:
        parts = urlsplit(value.strip())
        if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username is not None:
            return None
        host = parts.hostname.encode("idna").decode("ascii")
        if ":" in host:
            host = f"[{host}]"
        if parts.port is not None and parts.port != (443 if parts.scheme == "https" else 80):
            host += f":{parts.port}"
        path = quote(parts.path or "/", safe="/%:@!$&'()*+,;=-._~")
        query = quote(parts.query, safe="/?%:@!$&'()*+,;=-._~")
        return urlunsplit((parts.scheme, host, path, query, ""))
    except (ValueError, UnicodeError):
        return None


def origin(url):
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


class PageParser(HTMLParser):
    ignored = {"script", "style", "template", "noscript"}
    blocks = {"p", "div", "br", "li", "section", "article", "header", "footer",
              "h1", "h2", "h3", "h4", "h5", "h6", "td", "nav", "main"}

    def __init__(self):
        super().__init__()
        self.title = []
        self.body = []
        self.links = []
        self.base = None
        self.in_head = False
        self.in_title = False
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.ignored:
            self.skip += 1
        if self.skip:
            return
        attrs = dict(attrs)
        if tag == "head":
            self.in_head = True
        elif tag == "title":
            self.in_title = True
        elif tag == "base" and self.base is None:
            self.base = attrs.get("href")
        elif tag == "a" and attrs.get("href"):
            self.links.append(attrs["href"])
        elif tag in self.blocks:
            self.body.append(" ")

    def handle_endtag(self, tag):
        if tag in self.ignored:
            self.skip = max(0, self.skip - 1)
        elif not self.skip:
            if tag == "head":
                self.in_head = False
            elif tag == "title":
                self.in_title = False
            elif tag in self.blocks:
                self.body.append(" ")

    def handle_data(self, data):
        if self.skip:
            return
        if self.in_title:
            self.title.append(data)
        elif not self.in_head:
            self.body.append(data)


class ManualRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def read_bytes(response):
    payload = response.read(MAX_BYTES + 1)
    if len(payload) > MAX_BYTES:
        raise ValueError("응답 크기 제한(2 MiB)을 초과했습니다.")
    return payload


def read_html(response):
    payload = read_bytes(response)
    meta = re.search(br"""<meta\b[^>]*?charset\s*=\s*["']?([\w.-]+)""", payload[:4096], re.I)
    encoding = response.headers.get_content_charset() or (meta[1].decode("ascii") if meta else "utf-8-sig")
    try:
        return payload.decode(encoding, "replace")
    except (LookupError, UnicodeError):
        return payload.decode("utf-8-sig", "replace")


@dataclass
class Crawl:
    documents: list = field(default_factory=list)
    errors: list = field(default_factory=list)


def crawl(seed, max_pages=10, max_depth=2):
    seed = normalize_url(seed)
    if not seed or not 1 <= max_pages <= 100 or not 0 <= max_depth <= 10:
        raise ValueError("HTTP(S) URL, max_pages 1~100, max_depth 0~10이 필요합니다.")
    site = origin(seed)
    opener = build_opener(ManualRedirect())

    def fetch(url):
        return opener.open(Request(url, headers={"User-Agent": AGENT}), timeout=5)

    result = Crawl()
    rules = RobotFileParser()
    robots_url = site + "/robots.txt"
    try:
        with fetch(robots_url) as response:
            rules.parse(read_bytes(response).decode("utf-8-sig", "replace").splitlines())
    except HTTPError as exc:
        exc.close()
        if exc.code not in {404, 410}:
            result.errors.append({"url": robots_url, "reason": f"HTTP {exc.code}"})
            return result
        rules.allow_all = True
    except (HTTPException, OSError, ValueError) as exc:
        result.errors.append({"url": robots_url, "reason": str(exc)})
        return result

    queue = deque([(seed, 0)])
    seen = {seed}
    budget = max_pages * 10

    def enqueue(base, href, depth, redirect=False):
        try:
            url = normalize_url(urljoin(base, href))
        except ValueError:
            return
        if url and origin(url) == site and url not in seen and len(seen) < budget:
            seen.add(url)
            if redirect:
                queue.appendleft((url, depth))
            else:
                queue.append((url, depth))

    while queue and len(result.documents) < max_pages:
        url, depth = queue.popleft()
        if not rules.can_fetch(AGENT, url):
            result.errors.append({"url": url, "reason": "robots.txt 수집 제한"})
            continue
        try:
            with fetch(url) as response:
                if response.headers.get_content_type() not in {"text/html", "application/xhtml+xml"}:
                    continue
                page = PageParser()
                page.feed(read_html(response))
                page.close()
            title = " ".join("".join(page.title).split()) or url
            body = " ".join("".join(page.body).split())
            result.documents.append({"url": url, "title": title, "body": body})
            if depth < max_depth:
                base = urljoin(url, page.base or "")
                for href in page.links:
                    enqueue(base, href, depth + 1)
        except HTTPError as exc:
            exc.close()
            if exc.code in {301, 302, 303, 307, 308} and exc.headers.get("Location"):
                enqueue(url, exc.headers["Location"], depth, redirect=True)
            else:
                result.errors.append({"url": url, "reason": f"HTTP {exc.code}"})
        except (HTTPException, OSError, ValueError) as exc:
            result.errors.append({"url": url, "reason": str(exc)})
    return result
