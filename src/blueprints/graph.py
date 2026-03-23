"""
Graph Blueprint
===============

API endpoints for entity extraction and Neo4j graph ingestion.
All routes prefixed with /api/graph.

Setup:
    init_graph_document_manager     Inject the DocumentManager dependency

Endpoints:
    POST   /api/graph/extract       Extract entities from a parsed document
    POST   /api/graph/ingest        Build and ingest graph for a document
    POST   /api/graph/pipeline      Run full pipeline (extract + ingest)
    POST   /api/graph/batch         Process all documents at a given status
    GET    /api/graph/status/<hash> Check extraction/graph status
    DELETE /api/graph/<hash>        Delete a document's graph from Neo4j
"""

import logging
from typing import Tuple

from flask import Blueprint, Response, jsonify, request

from core.documents.manager import DocumentManager
from core.errors import ResourceNotFoundError, ValidationError
from core.graph.pipeline import (
    run_extraction,
    run_ingestion,
    run_full_pipeline,
    run_batch_pipeline,
)
from core.graph.neo4j_writer import delete_document_graph

logger = logging.getLogger(__name__)

# ============================================
# Blueprint Setup
# ============================================

graph_blueprint = Blueprint("graph", __name__, url_prefix="/api/graph")

_document_manager = None


def init_graph_document_manager(manager: DocumentManager) -> None:
    """Initialize the document manager for the graph blueprint."""
    global _document_manager
    _document_manager = manager


def _get_manager() -> DocumentManager:
    if _document_manager is None:
        raise RuntimeError("Document manager not initialized for graph blueprint")
    return _document_manager


# ============================================
# Route Handlers
# ============================================

@graph_blueprint.route("/extract", methods=["POST"])
def extract_entities() -> Tuple[Response, int]:
    """Extract entities from a parsed document."""
    manager = _get_manager()
    data = request.get_json()
    if not data or not data.get("hash"):
        raise ValidationError("No document hash provided")

    doc_hash = data["hash"]
    result = run_extraction(doc_hash, manager)

    return jsonify({
        "success": True,
        "hash": doc_hash,
        "entities_found": len(result.get("entities", [])),
        "relationships_found": len(result.get("relationships", [])),
        "chunks_processed": result.get("chunks_processed", 0),
        "chunks_with_errors": result.get("chunks_with_errors", 0),
    }), 200


@graph_blueprint.route("/ingest", methods=["POST"])
def ingest_graph() -> Tuple[Response, int]:
    """Build and ingest graph into Neo4j."""
    manager = _get_manager()
    data = request.get_json()
    if not data or not data.get("hash"):
        raise ValidationError("No document hash provided")

    doc_hash = data["hash"]
    result = run_ingestion(doc_hash, manager)

    return jsonify({
        "success": True,
        "hash": doc_hash,
        "result": result,
    }), 200


@graph_blueprint.route("/pipeline", methods=["POST"])
def full_pipeline() -> Tuple[Response, int]:
    """Run full extraction + ingestion pipeline."""
    manager = _get_manager()
    data = request.get_json()
    if not data or not data.get("hash"):
        raise ValidationError("No document hash provided")

    doc_hash = data["hash"]
    result = run_full_pipeline(doc_hash, manager)

    return jsonify({
        "success": True,
        "hash": doc_hash,
        "result": result,
    }), 200


@graph_blueprint.route("/batch", methods=["POST"])
def batch_pipeline() -> Tuple[Response, int]:
    """Process all documents at a given status."""
    manager = _get_manager()
    data = request.get_json() or {}
    from_status = data.get("from_status", "parsed")

    if from_status not in ("parsed", "entity_extracted"):
        raise ValidationError(
            f"from_status must be 'parsed' or 'entity_extracted', "
            f"got '{from_status}'"
        )

    result = run_batch_pipeline(manager, from_status=from_status)

    return jsonify({
        "success": True,
        "result": result,
    }), 200


@graph_blueprint.route("/status/<doc_hash>", methods=["GET"])
def graph_status(doc_hash: str) -> Tuple[Response, int]:
    """Check extraction and graph status for a document."""
    manager = _get_manager()
    document = manager.get_document(doc_hash)
    if document is None:
        raise ResourceNotFoundError("Document not found")

    has_extracted = manager.document_exists_at_status(doc_hash, "entity_extracted")
    has_staged = manager.document_exists_at_status(doc_hash, "graph_staged")
    has_graph = manager.document_exists_at_status(doc_hash, "graph_ready")

    return jsonify({
        "hash": doc_hash,
        "status": document.get("status"),
        "entity_extracted": has_extracted,
        "entity_extracted_at": document.get("entity_extracted_at"),
        "graph_staged": has_staged,
        "graph_staged_at": document.get("graph_staged_at"),
        "graph_ready": has_graph,
        "graph_ready_at": document.get("graph_ready_at"),
    }), 200


@graph_blueprint.route("/<doc_hash>", methods=["DELETE"])
def delete_graph(doc_hash: str) -> Tuple[Response, int]:
    """Delete a document's graph from Neo4j."""
    manager = _get_manager()
    document = manager.get_document(doc_hash)
    if document is None:
        raise ResourceNotFoundError("Document not found")

    deleted = delete_document_graph(doc_hash)

    return jsonify({
        "message": "Graph deleted",
        "hash": doc_hash,
        "nodes_deleted": deleted,
    }), 200
