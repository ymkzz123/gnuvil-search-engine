"""역색인을 조회하고 문서 길이를 반영한 TF-IDF로 순위를 계산한다."""

import math
from collections import Counter

from indexer import database, forms, words


def snippet(body, terms):
    lower = body.lower()
    hits = [pos for term in terms if (pos := lower.find(term)) >= 0]
    start = max(0, min(hits, default=0) - 35)
    return ("…" if start else "") + body[start:start + 160] + ("…" if start + 160 < len(body) else "")


def search(path, query, mode="AND", limit=10, offset=0):
    mode = mode.upper()
    terms = list(dict.fromkeys(words(query)))
    if not terms or len(query) > 256:
        raise ValueError("검색어는 한글·영문·숫자를 포함한 1~256자여야 합니다.")
    if mode not in {"AND", "OR"} or not 1 <= limit <= 100 or offset < 0:
        raise ValueError("mode는 AND/OR, limit은 1~100, offset은 0 이상이어야 합니다.")
    hits = Counter()
    scores = Counter()
    with database(path) as conn:
        conn.execute("BEGIN")
        total_docs = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        for term in terms:
            variants = forms(term)
            marks = ",".join("?" for _ in variants)
            rows = conn.execute(f"""
                SELECT doc_id, MAX(frequency) AS freq, length
                FROM postings JOIN documents ON doc_id=id
                WHERE term IN ({marks}) GROUP BY doc_id
            """, variants).fetchall()
            idf = 1 + math.log((1 + total_docs) / (1 + len(rows)))
            for row in rows:
                doc_id = row["doc_id"]
                hits[doc_id] += 1
                scores[doc_id] += row["freq"] / row["length"] * idf
        candidates = [i for i in scores if mode == "OR" or hits[i] == len(terms)]
        candidates.sort(key=lambda i: (-scores[i], i))
        results = []
        variants = {v for term in terms for v in forms(term)}
        for i in candidates[offset:offset + limit]:
            row = conn.execute("SELECT url, title, body FROM documents WHERE id=?", (i,)).fetchone()
            results.append({"id": i, "url": row["url"], "title": row["title"],
                            "snippet": snippet(row["body"], variants), "score": round(scores[i], 8)})
    return {"query": query, "mode": mode, "total": len(candidates), "limit": limit,
            "offset": offset, "results": results}
