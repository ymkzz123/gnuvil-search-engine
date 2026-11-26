"""문서 원본과 역색인을 같은 SQLite 트랜잭션으로 저장한다."""

import re
import sqlite3
import unicodedata
from collections import Counter
from contextlib import contextmanager
from pathlib import Path

from crawler import normalize_url

WORDS = re.compile(r"[a-z0-9]+|[가-힣]+")
PARTICLES = ("에서는", "에서", "으로", "에게", "에는", "은", "는", "을", "를", "이", "가")


def words(text):
    return WORDS.findall(unicodedata.normalize("NFKC", text).casefold())


def forms(word):
    for ending in PARTICLES:
        if word.endswith(ending) and len(word) - len(ending) >= 2:
            stem = word[:-len(ending)]
            final = (ord(stem[-1]) - ord("가")) % 28
            if ending in {"은", "을", "이"} and not final:
                continue
            if ending in {"는", "를", "가"} and final:
                continue
            return (word, stem)
    return (word,)


SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY,
    url TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    length INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS postings (
    term TEXT NOT NULL,
    doc_id INTEGER NOT NULL REFERENCES documents(id),
    frequency INTEGER NOT NULL,
    PRIMARY KEY (term, doc_id)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS postings_by_doc ON postings(doc_id);
"""


@contextmanager
def database(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        yield conn
    finally:
        conn.close()


def initialize(path):
    with database(path) as conn:
        conn.executescript(SCHEMA)


def index_documents(path, documents):
    if not isinstance(documents, list):
        raise ValueError("문서 JSON은 배열이어야 합니다.")
    saved = []
    with database(path) as conn, conn:
        for doc in documents:
            if not isinstance(doc, dict) or not all(isinstance(doc.get(k), str) for k in ("url", "title", "body")):
                raise ValueError("각 문서에 문자열 url, title, body가 필요합니다.")
            url = normalize_url(doc["url"])
            if not url:
                raise ValueError("올바른 HTTP(S) 문서 URL이 필요합니다.")
            title, body = doc["title"], doc["body"]
            tokens = words(title + " " + body)
            counts = Counter(term for token in tokens for term in forms(token))
            conn.execute("""
                INSERT INTO documents(url, title, body, length) VALUES (?, ?, ?, ?)
                ON CONFLICT(url) DO UPDATE SET
                    title=excluded.title, body=excluded.body, length=excluded.length
            """, (url, title, body, len(tokens)))
            doc_id = conn.execute("SELECT id FROM documents WHERE url=?", (url,)).fetchone()[0]
            conn.execute("DELETE FROM postings WHERE doc_id=?", (doc_id,))
            conn.executemany("INSERT INTO postings VALUES (?, ?, ?)",
                             ((term, doc_id, freq) for term, freq in counts.items()))
            saved.append({"id": doc_id, "url": url, "title": title, "body": body})
    return saved
