"""
Backfill embeddings and PageRank for existing Neo4j data.

Finds structural nodes (Section, Table, Figure) that lack embeddings,
generates them using the sentence-transformers model, and stores them.
Also computes PageRank for each document.

Usage:
    python scripts/backfill_embeddings.py
"""

import sys
from pathlib import Path

# Add src to path so imports work
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from config import settings
from core.graph.neo4j_writer import (
    get_driver,
    store_embeddings,
    compute_and_store_pagerank,
    ensure_schema,
)
from core.graph.embeddings import embed_texts


def backfill():
    """Backfill embeddings and PageRank for all structural nodes."""
    driver = get_driver()

    # Ensure vector indexes exist
    ensure_schema()

    # Find all structural nodes missing embeddings
    with driver.session(database=settings.NEO4J_DATABASE) as session:
        result = session.run(
            "MATCH (n) "
            "WHERE (n:Section OR n:Table OR n:Figure) "
            "AND n.embedding IS NULL "
            "RETURN n.id AS id, n.title AS title, n.content AS content, "
            "       labels(n)[0] AS label "
            "ORDER BY n.id"
        )
        nodes = [dict(r) for r in result]

    print(f"Found {len(nodes)} nodes without embeddings")

    if nodes:
        # Generate embeddings
        texts = []
        for node in nodes:
            title = node.get("title") or ""
            content = node.get("content") or ""
            texts.append(f"{title} {content}".strip())

        print(f"Generating embeddings for {len(texts)} nodes...")
        vectors = embed_texts(texts)

        embedding_data = [
            {"id": node["id"], "embedding": vec}
            for node, vec in zip(nodes, vectors)
        ]

        stored = store_embeddings(embedding_data)
        print(f"Stored {stored} embeddings")

    # Compute PageRank for each document
    with driver.session(database=settings.NEO4J_DATABASE) as session:
        result = session.run(
            "MATCH (n) "
            "WHERE (n:Section OR n:Table OR n:Figure) "
            "AND n.doc_hash IS NOT NULL "
            "RETURN DISTINCT n.doc_hash AS doc_hash"
        )
        doc_hashes = [r["doc_hash"] for r in result]

    print(f"Computing PageRank for {len(doc_hashes)} documents...")
    for doc_hash in doc_hashes:
        compute_and_store_pagerank(doc_hash)
        print(f"  PageRank done: {doc_hash[:12]}...")

    print("Backfill complete!")


if __name__ == "__main__":
    backfill()
