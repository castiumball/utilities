"""
Pipeline Orchestration
======================

Coordinates the full document-to-graph flow through four stages:
    parsed -> entity_extracted -> graph_staged -> graph_ready

Single-Document Pipeline Steps:
    run_extraction          Extract entities from a parsed document and save results
    run_ingestion           Build structural + semantic graphs and ingest into Neo4j
    run_full_pipeline       Run extraction then ingestion, skipping completed stages

Batch Processing:
    run_batch_pipeline      Process all documents at a given status through the pipeline

Internal Helpers:
    _find_entity_label      Resolve an entity name to its label across chunks
    _merge_entity_entries   Merge duplicate entity entries sharing the same ID
"""

import json
import logging
from typing import Any, Dict, List

from config import settings
from core.documents.manager import DocumentManager
from core.errors import ResourceNotFoundError, ValidationError
from .acronyms import load_acronyms
from .extraction import run_extraction_sync
from .resolution import (
    generate_entity_id,
    fuzzy_deduplicate,
    apply_merge_map,
)
from .validation import validate_extraction
from .structural import build_structural_graph
from .neo4j_writer import (
    ensure_schema,
    ingest_structural_graph,
    ingest_semantic_graph,
    store_embeddings,
    compute_and_store_pagerank,
)
from .embeddings import embed_texts

logger = logging.getLogger(__name__)


# ============================================
# Single-Document Pipeline Steps
# ============================================


def run_extraction(doc_hash: str, manager: DocumentManager) -> Dict[str, Any]:
    """
    Extract entities from a parsed document and save to data/entity_extracted/.

    Flow:
        1. Load parsed data from data/parsed/{hash}.json
        2. Load acronym CSV (if configured)
        3. Run async extraction on all chunks via vLLM
        4. Run fuzzy deduplication on collected entities
        5. Save results to data/entity_extracted/{hash}.json
        6. Update document status to 'entity_extracted'
    """
    document = manager.get_document(doc_hash)
    if document is None:
        raise ResourceNotFoundError(f"Document not found: {doc_hash}")

    parsed_path = manager.get_document_path(doc_hash, "parsed")
    if not parsed_path.exists():
        raise ValidationError(
            f"Document {doc_hash} has not been parsed yet. "
            f"Current status: {document.get('status')}"
        )

    with open(parsed_path, "r", encoding="utf-8") as f:
        parsed_data = json.load(f)

    chunks = parsed_data.get("chunks", [])
    if not chunks:
        logger.warning("No chunks found in parsed data for %s", doc_hash)

    acronym_map = load_acronyms(settings.ACRONYM_CSV_PATH)

    logger.info(
        "Starting entity extraction for %s (%d chunks)", doc_hash, len(chunks)
    )
    extraction_results = run_extraction_sync(chunks, acronym_map)

    # Collect all entities and relationships across chunks.
    # all_entities_by_name accumulates name -> label mappings so that
    # relationships in later chunks can resolve entities extracted earlier.
    all_entities: List[Dict] = []
    all_relationships: List[Dict] = []
    all_entities_by_name: Dict[str, str] = {}

    for result in extraction_results:
        chunk_id = f"{result['chunk_number']}_{result['chunk_index']}"
        for entity in result.get("entities", []):
            entity_id = generate_entity_id(entity["label"], entity["name"])
            all_entities.append({
                "id": entity_id,
                "name": entity["name"],
                "label": entity["label"],
                "description": entity.get("description"),
                "chunk_ids": [chunk_id],
            })
            all_entities_by_name[entity["name"]] = entity["label"]
        for rel in result.get("relationships", []):
            source_label = _find_entity_label(
                rel["source"], result.get("entities", []),
                all_entities_by_name,
            )
            target_label = _find_entity_label(
                rel["target"], result.get("entities", []),
                all_entities_by_name,
            )
            source_id = generate_entity_id(source_label, rel["source"])
            target_id = generate_entity_id(target_label, rel["target"])
            all_relationships.append({
                "source_id": source_id,
                "target_id": target_id,
                "type": rel["type"],
            })

    # Merge duplicate entity entries (same ID from different chunks)
    merged_entities = _merge_entity_entries(all_entities)

    # Fuzzy deduplication
    deduplicated_entities, merge_map = fuzzy_deduplicate(
        merged_entities,
        threshold=settings.FUZZY_MATCH_THRESHOLD,
    )
    resolved_relationships = apply_merge_map(all_relationships, merge_map)

    # Post-processing validation
    if settings.VALIDATION_ENABLED:
        deduplicated_entities, resolved_relationships, validation_report = (
            validate_extraction(deduplicated_entities, resolved_relationships)
        )
    else:
        validation_report = {"skipped": True}

    output = {
        "doc_hash": doc_hash,
        "metadata": parsed_data.get("metadata", {}),
        "chunks_processed": len(chunks),
        "chunks_with_entities": sum(
            1 for r in extraction_results if r.get("entities")
        ),
        "chunks_with_errors": sum(
            1 for r in extraction_results if r.get("error")
        ),
        "entities": deduplicated_entities,
        "relationships": resolved_relationships,
        "validation": validation_report,
        "extraction_details": extraction_results,
    }

    output_path = manager.get_document_path(doc_hash, "entity_extracted")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    manager.update_status(doc_hash, "entity_extracted")

    logger.info(
        "Extraction complete for %s: %d entities, %d relationships",
        doc_hash, len(deduplicated_entities), len(resolved_relationships),
    )
    return output


def _generate_embeddings(structural_graph: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Generate embeddings for structural nodes (Section, Table, Figure).

    Builds embedding input as "{title} {content}" for each node,
    then batch-encodes using the sentence-transformers model.
    """
    nodes = structural_graph.get("nodes", [])
    embeddable = [
        n for n in nodes
        if n.get("label") in ("Section", "Table", "Figure")
    ]

    if not embeddable:
        return []

    texts = []
    ids = []
    for node in embeddable:
        props = node.get("properties", {})
        title = props.get("title", "")
        content = props.get("content", "")
        texts.append(f"{title} {content}".strip())
        ids.append(node["id"])

    vectors = embed_texts(texts)

    return [
        {"id": node_id, "embedding": vec}
        for node_id, vec in zip(ids, vectors)
    ]


def run_ingestion(doc_hash: str, manager: DocumentManager) -> Dict[str, Any]:
    """
    Build and ingest both structural and semantic graphs into Neo4j.

    Flow:
        1. Load parsed data (for structural graph)
        2. Load entity_extracted data (for semantic graph)
        3. Build structural graph from chunks
        4. Ensure Neo4j schema (constraints/indexes)
        5. Ingest structural graph (Layer 1)
        6. Ingest semantic graph (Layer 2)
        7. Save summary to data/graph_ready/{hash}.json
        8. Update document status to 'graph_ready'
    """
    document = manager.get_document(doc_hash)
    if document is None:
        raise ResourceNotFoundError(f"Document not found: {doc_hash}")

    parsed_path = manager.get_document_path(doc_hash, "parsed")
    if not parsed_path.exists():
        raise ValidationError(f"Parsed data not found for {doc_hash}")

    with open(parsed_path, "r", encoding="utf-8") as f:
        parsed_data = json.load(f)

    extracted_path = manager.get_document_path(doc_hash, "entity_extracted")
    if not extracted_path.exists():
        raise ValidationError(
            f"Entity extraction not complete for {doc_hash}. "
            f"Run extraction first."
        )

    with open(extracted_path, "r", encoding="utf-8") as f:
        extracted_data = json.load(f)

    # Build structural graph (Layer 1)
    structural_graph = build_structural_graph(doc_hash, parsed_data)

    # Ensure Neo4j schema
    ensure_schema()

    # Ingest Layer 1
    structural_stats = ingest_structural_graph(structural_graph)

    # Generate and store embeddings for structural nodes
    embedding_data = _generate_embeddings(structural_graph)
    embeddings_stored = store_embeddings(embedding_data)

    # Ingest Layer 2
    semantic_stats = ingest_semantic_graph(
        entities=extracted_data.get("entities", []),
        relationships=extracted_data.get("relationships", []),
        doc_hash=doc_hash,
    )

    # Compute PageRank over the full graph (structural + semantic edges)
    compute_and_store_pagerank(doc_hash)

    summary = {
        "doc_hash": doc_hash,
        "structural": {
            **structural_graph.get("summary", {}),
            **structural_stats,
            "embeddings_stored": embeddings_stored,
        },
        "semantic": {
            "entities": len(extracted_data.get("entities", [])),
            "relationships": len(extracted_data.get("relationships", [])),
            **semantic_stats,
        },
    }

    # Write to graph_staged first (testing checkpoint)
    staged_path = manager.get_document_path(doc_hash, "graph_staged")
    staged_path.parent.mkdir(parents=True, exist_ok=True)
    with open(staged_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    manager.update_status(doc_hash, "graph_staged")
    logger.info("Graph ingestion staged for %s", doc_hash)

    # Auto-promote to graph_ready (copy staged output)
    ready_path = manager.get_document_path(doc_hash, "graph_ready")
    ready_path.parent.mkdir(parents=True, exist_ok=True)
    with open(ready_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    manager.update_status(doc_hash, "graph_ready")

    logger.info("Graph ingestion complete for %s", doc_hash)
    return summary


def run_full_pipeline(
    doc_hash: str, manager: DocumentManager
) -> Dict[str, Any]:
    """
    Run extraction + ingestion for a single document.
    Skips stages that are already complete.
    """
    document = manager.get_document(doc_hash)
    if document is None:
        raise ResourceNotFoundError(f"Document not found: {doc_hash}")

    status = document.get("status", "")

    extraction_result = None
    ingestion_result = None

    if status == "parsed":
        extraction_result = run_extraction(doc_hash, manager)
        ingestion_result = run_ingestion(doc_hash, manager)
    elif status == "entity_extracted":
        ingestion_result = run_ingestion(doc_hash, manager)
    elif status in ("graph_staged", "graph_ready"):
        logger.info("Document %s already %s; skipping", doc_hash, status)
    else:
        raise ValidationError(
            f"Document {doc_hash} must be parsed first (status: {status})"
        )

    return {
        "doc_hash": doc_hash,
        "extraction": extraction_result,
        "ingestion": ingestion_result,
    }


# ============================================
# Batch Processing
# ============================================


def run_batch_pipeline(
    manager: DocumentManager,
    from_status: str = "parsed",
) -> Dict[str, Any]:
    """
    Process all documents at a given status through the full pipeline.

    For initial bulk processing of already-parsed documents.
    """
    documents = manager.list_documents(status=from_status)
    logger.info(
        "Batch pipeline: %d documents at status '%s'",
        len(documents), from_status,
    )

    successes = []
    failures = []

    for doc in documents:
        doc_hash = doc["hash"]
        try:
            if from_status == "parsed":
                run_full_pipeline(doc_hash, manager)
            elif from_status == "entity_extracted":
                run_ingestion(doc_hash, manager)
            successes.append(doc_hash)
        except Exception as exc:
            logger.error("Pipeline failed for %s: %s", doc_hash, exc)
            failures.append({"hash": doc_hash, "error": str(exc)})

    return {
        "total": len(documents),
        "successes": len(successes),
        "failures": len(failures),
        "failed_documents": failures,
    }


# ============================================
# Internal Helpers
# ============================================


def _find_entity_label(
    name: str,
    chunk_entities: List[Dict],
    all_entities_by_name: Dict[str, str] = None,
) -> str:
    """
    Find the label for an entity by name.

    Search order:
    1. Current chunk's entity list (most specific context)
    2. Accumulated entity registry from all processed chunks
    3. Falls back to 'System' only as last resort

    The cross-chunk fallback prevents phantom references like
    system_author_s when an entity was extracted with a different
    label (e.g., Person) in another chunk.
    """
    for e in chunk_entities:
        if e.get("name") == name:
            return e.get("label", "System")

    if all_entities_by_name and name in all_entities_by_name:
        return all_entities_by_name[name]

    return "System"


def _merge_entity_entries(entities: List[Dict]) -> List[Dict]:
    """
    Merge entity entries with the same ID (same entity from different chunks).
    Combines chunk_ids and keeps the longest description.
    """
    by_id: Dict[str, Dict] = {}
    for entity in entities:
        eid = entity["id"]
        if eid in by_id:
            existing = by_id[eid]
            existing["chunk_ids"] = list(
                set(existing.get("chunk_ids", []))
                | set(entity.get("chunk_ids", []))
            )
            new_desc = entity.get("description") or ""
            old_desc = existing.get("description") or ""
            if len(new_desc) > len(old_desc):
                existing["description"] = new_desc
        else:
            by_id[eid] = entity

    return list(by_id.values())
