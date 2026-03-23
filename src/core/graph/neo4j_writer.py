"""
Neo4j Writer
============

Manages Neo4j connection and ingestion of both structural and semantic
graph layers using batched UNWIND + MERGE operations.

Design decisions:
    - MERGE (not CREATE) ensures idempotent ingestion -- re-running is safe
    - UNWIND batching reduces round-trips (default 500 items per batch)
    - Constraints enforce uniqueness on entity IDs
    - Indexes on doc_hash for document-scoped queries

Connection Management:
    get_driver              Get or create the Neo4j driver singleton
    close_driver            Close the Neo4j driver

Schema Setup:
    ensure_schema           Create constraints and indexes if missing

Structural Graph Ingestion (Layer 1):
    ingest_structural_graph Ingest structural nodes and edges into Neo4j

Semantic Graph Ingestion (Layer 2):
    ingest_semantic_graph   Ingest semantic entities and relationships
    delete_document_graph   Delete all nodes/relationships for a document
"""

import logging
from collections import defaultdict
from typing import Any, Dict, List, Optional

from neo4j import GraphDatabase, Driver

from config import settings
from core.errors import GraphError

logger = logging.getLogger(__name__)


# ============================================
# Connection Management
# ============================================

_driver: Optional[Driver] = None


def get_driver() -> Driver:
    """Get or create the Neo4j driver singleton."""
    global _driver
    if _driver is None:
        _driver = GraphDatabase.driver(
            settings.NEO4J_URI,
            auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD),
        )
        _driver.verify_connectivity()
        logger.info("Connected to Neo4j at %s", settings.NEO4J_URI)
    return _driver


def close_driver() -> None:
    """Close the Neo4j driver."""
    global _driver
    if _driver is not None:
        _driver.close()
        _driver = None
        logger.info("Closed Neo4j connection")


# ============================================
# Schema Setup
# ============================================

STRUCTURAL_LABELS = ["Document", "Section", "Table", "Figure"]
SEMANTIC_LABELS = [
    "System", "CSCI", "CSC", "CSU", "Interface",
    "DataStore", "Function", "Requirement",
    "ExternalSystem", "Message", "Protocol",
    "Person", "Organization", "Standard", "Language",
]
AUXILIARY_LABELS = ["Acronym"]


def ensure_schema() -> None:
    """
    Create Neo4j constraints and indexes if they don't already exist.

    Unique constraint on `id` for each label. Index on `doc_hash`
    for structural nodes to support document-scoped queries.
    """
    driver = get_driver()

    with driver.session(database=settings.NEO4J_DATABASE) as session:
        for label in STRUCTURAL_LABELS + SEMANTIC_LABELS + AUXILIARY_LABELS:
            constraint_name = f"unique_{label.lower()}_id"
            session.run(
                f"CREATE CONSTRAINT {constraint_name} IF NOT EXISTS "
                f"FOR (n:{label}) REQUIRE n.id IS UNIQUE"
            )

        for label in STRUCTURAL_LABELS:
            index_name = f"idx_{label.lower()}_doc_hash"
            session.run(
                f"CREATE INDEX {index_name} IF NOT EXISTS "
                f"FOR (n:{label}) ON (n.doc_hash)"
            )

        # Full-text index for RAG retrieval (BM25 search)
        session.run(
            "CREATE FULLTEXT INDEX structural_content_ft IF NOT EXISTS "
            "FOR (n:Section|Table|Figure) ON EACH [n.content, n.title]"
        )

        # Vector indexes for embedding-based retrieval (one per label)
        dim = settings.EMBEDDING_DIMENSION
        for label in ("Section", "Table", "Figure"):
            idx_name = f"{label.lower()}_embedding_idx"
            session.run(
                f"CREATE VECTOR INDEX {idx_name} IF NOT EXISTS "
                f"FOR (n:{label}) ON (n.embedding) "
                f"OPTIONS {{indexConfig: {{"
                f"`vector.dimensions`: {dim}, "
                f"`vector.similarity_function`: 'cosine'"
                f"}}}}"
            )

    logger.info("Neo4j schema setup complete")


# ============================================
# Structural Graph Ingestion (Layer 1)
# ============================================

def ingest_structural_graph(graph_data: Dict[str, Any]) -> Dict[str, int]:
    """
    Ingest structural graph nodes and edges into Neo4j.

    Uses UNWIND + MERGE for idempotent batched writes.
    """
    driver = get_driver()
    nodes = graph_data.get("nodes", [])
    edges = graph_data.get("edges", [])
    batch_size = settings.NEO4J_BATCH_SIZE

    nodes_written = 0
    edges_written = 0

    with driver.session(database=settings.NEO4J_DATABASE) as session:
        # Nodes by label
        nodes_by_label: Dict[str, List[Dict]] = defaultdict(list)
        for node in nodes:
            nodes_by_label[node["label"]].append(node)

        for label, label_nodes in nodes_by_label.items():
            for i in range(0, len(label_nodes), batch_size):
                batch = label_nodes[i:i + batch_size]
                params = [
                    {"id": n["id"], **{k: v for k, v in n.get("properties", {}).items() if v is not None}}
                    for n in batch
                ]
                session.run(
                    f"UNWIND $batch AS row "
                    f"MERGE (n:{label} {{id: row.id}}) "
                    f"SET n += row",
                    batch=params,
                )
                nodes_written += len(batch)

        # Edges by type
        edges_by_type: Dict[str, List[Dict]] = defaultdict(list)
        for edge in edges:
            edges_by_type[edge["type"]].append(edge)

        for edge_type, type_edges in edges_by_type.items():
            for i in range(0, len(type_edges), batch_size):
                batch = type_edges[i:i + batch_size]
                params = [
                    {"source_id": e["source_id"], "target_id": e["target_id"]}
                    for e in batch
                ]
                session.run(
                    f"UNWIND $batch AS row "
                    f"MATCH (a {{id: row.source_id}}) "
                    f"MATCH (b {{id: row.target_id}}) "
                    f"MERGE (a)-[:{edge_type}]->(b)",
                    batch=params,
                )
                edges_written += len(batch)

    logger.info(
        "Structural graph ingested: %d nodes, %d edges",
        nodes_written, edges_written,
    )
    return {"nodes_written": nodes_written, "edges_written": edges_written}


# ============================================
# Semantic Graph Ingestion (Layer 2)
# ============================================

def ingest_semantic_graph(
    entities: List[Dict[str, Any]],
    relationships: List[Dict[str, Any]],
    doc_hash: str,
) -> Dict[str, int]:
    """
    Ingest semantic entities and relationships into Neo4j.

    Links semantic entities to their source structural chunks
    via EXTRACTED_FROM edges.
    """
    driver = get_driver()
    batch_size = settings.NEO4J_BATCH_SIZE

    nodes_written = 0
    edges_written = 0

    with driver.session(database=settings.NEO4J_DATABASE) as session:
        # Entity nodes by label
        entities_by_label: Dict[str, List[Dict]] = defaultdict(list)
        for entity in entities:
            entities_by_label[entity["label"]].append(entity)

        for label, label_entities in entities_by_label.items():
            for i in range(0, len(label_entities), batch_size):
                batch = label_entities[i:i + batch_size]
                params = [
                    {
                        "id": e["id"],
                        "name": e["name"],
                        "description": e.get("description"),
                    }
                    for e in batch
                ]
                session.run(
                    f"UNWIND $batch AS row "
                    f"MERGE (n:{label} {{id: row.id}}) "
                    f"SET n.name = row.name, n.description = row.description",
                    batch=params,
                )
                nodes_written += len(batch)

        # Link entities to source chunks via EXTRACTED_FROM
        for entity in entities:
            for chunk_id in entity.get("chunk_ids", []):
                # chunk_id format: "3.1.1_5" — split to get chunk number
                parts = chunk_id.rsplit("_", 1)
                chunk_number = parts[0] if len(parts) > 1 else chunk_id
                # Try to match structural node (section, table, or figure)
                for prefix in ("section", "table", "figure"):
                    structural_id = f"{prefix}_{doc_hash}_{chunk_number}"
                    session.run(
                        "MATCH (e {id: $entity_id}) "
                        "MATCH (c {id: $chunk_id}) "
                        "MERGE (e)-[:EXTRACTED_FROM]->(c)",
                        entity_id=entity["id"],
                        chunk_id=structural_id,
                    )

        # Relationships by type
        rels_by_type: Dict[str, List[Dict]] = defaultdict(list)
        for rel in relationships:
            rels_by_type[rel["type"]].append(rel)

        for rel_type, type_rels in rels_by_type.items():
            for i in range(0, len(type_rels), batch_size):
                batch = type_rels[i:i + batch_size]
                params = [
                    {"source_id": r["source_id"], "target_id": r["target_id"]}
                    for r in batch
                ]
                session.run(
                    f"UNWIND $batch AS row "
                    f"MATCH (a {{id: row.source_id}}) "
                    f"MATCH (b {{id: row.target_id}}) "
                    f"MERGE (a)-[:{rel_type}]->(b)",
                    batch=params,
                )
                edges_written += len(batch)

    logger.info(
        "Semantic graph ingested: %d nodes, %d edges",
        nodes_written, edges_written,
    )
    return {"nodes_written": nodes_written, "edges_written": edges_written}


def delete_document_graph(doc_hash: str) -> int:
    """
    Delete all nodes and relationships for a document from Neo4j.
    Useful for re-ingestion.
    """
    driver = get_driver()
    with driver.session(database=settings.NEO4J_DATABASE) as session:
        result = session.run(
            "MATCH (n {doc_hash: $doc_hash}) "
            "DETACH DELETE n "
            "RETURN count(n) AS deleted",
            doc_hash=doc_hash,
        )
        record = result.single()
        deleted = record["deleted"] if record else 0

    logger.info("Deleted %d nodes for document %s", deleted, doc_hash)
    return deleted


# ============================================
# Acronym Ingestion
# ============================================

def ingest_acronyms(acronym_map: Dict[str, str]) -> Dict[str, int]:
    """
    Ingest acronym definitions as Acronym nodes and link them to
    matching semantic entities via ALIAS_OF edges.

    Each acronym gets an Acronym node with properties:
      - id: deterministic (e.g. "acronym_mdt")
      - short: the acronym (e.g. "MDT")
      - expansion: the full form (e.g. "Minutia Deviation Tool")

    ALIAS_OF edges are created when a semantic entity's name matches
    the acronym (case-insensitive exact match) or contains the acronym
    in parentheses (e.g. entity named "Minutia Deviation Tool (MDT)").
    """
    if not acronym_map:
        return {"nodes_written": 0, "edges_written": 0}

    driver = get_driver()
    nodes_written = 0
    edges_written = 0

    with driver.session(database=settings.NEO4J_DATABASE) as session:
        # Create Acronym nodes
        params = [
            {
                "id": f"acronym_{short.lower().replace(' ', '_')}",
                "short": short,
                "expansion": expansion,
            }
            for short, expansion in acronym_map.items()
        ]
        session.run(
            "UNWIND $batch AS row "
            "MERGE (a:Acronym {id: row.id}) "
            "SET a.short = row.short, a.expansion = row.expansion",
            batch=params,
        )
        nodes_written = len(params)

        # Link to semantic entities via ALIAS_OF.
        # Match entities whose name equals the acronym or contains it
        # in parentheses.
        semantic_filter = "[" + ", ".join(
            f'"{l}"' for l in SEMANTIC_LABELS
        ) + "]"
        result = session.run(
            "MATCH (a:Acronym) "
            "MATCH (e) WHERE any(lbl IN labels(e) WHERE lbl IN " + semantic_filter + ") "
            "AND e.name IS NOT NULL "
            "AND ("
            "  toUpper(e.name) = toUpper(a.short) OR "
            "  e.name CONTAINS ('(' + a.short + ')')"
            ") "
            "MERGE (a)-[:ALIAS_OF]->(e) "
            "RETURN count(*) AS linked"
        )
        record = result.single()
        edges_written = record["linked"] if record else 0

    logger.info(
        "Acronym ingestion: %d nodes, %d ALIAS_OF edges",
        nodes_written, edges_written,
    )
    return {"nodes_written": nodes_written, "edges_written": edges_written}


# ============================================
# Embedding Storage
# ============================================

def store_embeddings(embeddings: List[Dict[str, Any]]) -> int:
    """
    Store precomputed embeddings on structural nodes.

    Args:
        embeddings: List of {"id": str, "embedding": List[float]}

    Returns:
        Number of nodes updated.
    """
    if not embeddings:
        return 0

    driver = get_driver()
    batch_size = settings.NEO4J_BATCH_SIZE
    updated = 0

    with driver.session(database=settings.NEO4J_DATABASE) as session:
        for i in range(0, len(embeddings), batch_size):
            batch = embeddings[i:i + batch_size]
            session.run(
                "UNWIND $batch AS row "
                "MATCH (n {id: row.id}) "
                "SET n.embedding = row.embedding",
                batch=batch,
            )
            updated += len(batch)

    logger.info("Stored embeddings on %d nodes", updated)
    return updated


# ============================================
# PageRank Computation
# ============================================

def compute_and_store_pagerank(doc_hash: str, iterations: int = 20) -> None:
    """
    Compute PageRank over a document's structural + semantic graph
    using iterative Cypher (no GDS plugin required).

    Scores are normalized to [0, 1] and stored as a `pagerank` property
    on each structural node.
    """
    driver = get_driver()

    with driver.session(database=settings.NEO4J_DATABASE) as session:
        # Initialize all structural nodes for this document with rank 1.0
        result = session.run(
            "MATCH (n {doc_hash: $doc_hash}) "
            "WHERE n:Section OR n:Table OR n:Figure "
            "SET n.pagerank = 1.0 "
            "RETURN count(n) AS total",
            doc_hash=doc_hash,
        )
        record = result.single()
        total = record["total"] if record else 0

        if total == 0:
            logger.debug("No structural nodes found for PageRank: %s", doc_hash)
            return

        # Iterative PageRank updates
        for _ in range(iterations):
            session.run(
                "MATCH (n {doc_hash: $doc_hash}) "
                "WHERE (n:Section OR n:Table OR n:Figure) "
                "WITH n, size([(n)-[]->() | 1]) AS outDegree "
                "WHERE outDegree > 0 "
                "MATCH (n)-[]->(neighbor) "
                "WHERE (neighbor:Section OR neighbor:Table OR neighbor:Figure) "
                "AND neighbor.doc_hash = $doc_hash "
                "WITH neighbor, sum(n.pagerank / outDegree) AS incoming "
                "SET neighbor.pagerank = 0.15 + 0.85 * incoming",
                doc_hash=doc_hash,
            )

        # Normalize to [0, 1] within the document
        session.run(
            "MATCH (n {doc_hash: $doc_hash}) "
            "WHERE (n:Section OR n:Table OR n:Figure) AND n.pagerank IS NOT NULL "
            "WITH max(n.pagerank) AS maxRank "
            "WHERE maxRank > 0 "
            "MATCH (m {doc_hash: $doc_hash}) "
            "WHERE (m:Section OR m:Table OR m:Figure) AND m.pagerank IS NOT NULL "
            "SET m.pagerank = m.pagerank / maxRank",
            doc_hash=doc_hash,
        )

    logger.info("PageRank computed for %d nodes (doc: %s)", total, doc_hash)
