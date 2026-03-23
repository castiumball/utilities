"""
Documents Package

Document storage for the ingestion process.

This package provides:
    - Hash-based document deduplication
    - SQLite metadata tracking
    - Stage-based file organization (raw, preprocessed, parsed, extracted, graph) TODO

Example Usage:
    from documents import DocumentManager

    manager = DocumentManager()

    # Add a new document
    doc_hash, is_new = manager.add_document(file, "test.pdf")

    # List all documents
    documents = manager.list_documents()

    # Get document path for specific stage
    raw_path = manager.list_documents()

    # Get document path for a specific stage
    raw_path = manager.get_document_path(doc_hash, "raw")
"""

from .manager import compute_file_hash, DocumentManager

__all__ = [
    "DocumentManager",
    "computer_file_hash",
]
