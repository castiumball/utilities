"""
Preprocessing Utilities
=======================

Converts raw PDF documents into normalized markdown with page-mapping
metadata for downstream parsing.

Public API:
    preprocess_pdf_to_markdown  Convert a PDF to markdown with a page map

Internal Helpers:
    _get_page_count             Count pages in a PDF via PyMuPDF
    _extract_markdown_with_page_map
                                Page-aware extraction with fallback
    _build_from_chunks          Assemble markdown and page map from chunks
    _normalize_chunk            Normalize a single pymupdf4llm chunk
    _fallback_page_map          Generate a stub page map when offsets unknown
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

import fitz  # PyMuPDF
import pymupdf4llm

# ============================================
# Public API
# ============================================

# TODO explain
PAGE_DELIMITER = "\n\n--\n\n"


def preprocess_pdf_to_markdown(doc_path: Path) -> Dict[str, Any]:
    """
    Convert a PDF to markdown and build a page map for downstream parsing.
    """
    doc_path = Path(doc_path)
    if not doc_path.exists():
        raise FileNotFoundError(f"Document not found: {doc_path}")

    markdown, page_map, page_count = _extract_markdown_with_page_map(doc_path)

    metadata = {
        "source": "pymupdf4llm",
        "page_count": page_count,
        "page_delimiter": PAGE_DELIMITER,
        "generated_at": datetime.utcnow().isoformat() + "Z",
    }

    return {
        "markdown": markdown,
        "page_map": page_map,
        "metadata": metadata,
    }


# ============================================
# Internal Helpers
# ============================================

def _get_page_count(doc_path: Path) -> int:
    with fitz.open(doc_path) as doc:
        return doc.page_count


def _extract_markdown_with_page_map(doc_path: Path) -> Tuple[str, List[Dict[str, Any]], int]:
    """
    Attempt page-aware extraction; fall back to single markdown string.

    Returns (markdown, page_map, page_count).
    """
    try:
        markdown_output = pymupdf4llm.to_markdown(
            str(doc_path), page_chunks=True, write_images=False
        )
    except TypeError:
        markdown_output = pymupdf4llm.to_markdown(str(doc_path))

    if isinstance(markdown_output, list):
        markdown, page_map = _build_from_chunks(markdown_output)
        return markdown, page_map, len(markdown_output)

    if isinstance(markdown_output, str):
        page_count = _get_page_count(doc_path)
        return markdown_output, _fallback_page_map(page_count), page_count


def _build_from_chunks(chunks: List[Any]) -> Tuple[str, List[Dict[str, Any]]]:
    parts: List[str] = []
    page_map: List[Dict[str, Any]] = []
    offset = 0

    for idx, chunk in enumerate(chunks):
        page_number, content = _normalize_chunk(chunk, idx)

        start = offset
        parts.append(content)
        offset += len(content)
        end = offset

        page_map.append({
            "page_number": page_number,
            "start": start,
            "end": end,
        })

        if idx < len(chunks) - 1:
            parts.append(PAGE_DELIMITER)
            offset += len(PAGE_DELIMITER)

    return "".join(parts), page_map


def _normalize_chunk(chunk: Any, fallback_index: int) -> Tuple[int, str]:
    if isinstance(chunk, dict):
        page_number = chunk.get("page_number") or chunk.get("page") or (fallback_index + 1)
        content = (
            chunk.get("markdown")
            or chunk.get("text")
            or chunk.get("content")
            or ""
        )
        return int(page_number), str(content)

    if isinstance(chunk, str):
        return fallback_index + 1, chunk

    return fallback_index + 1, str(chunk)


def _fallback_page_map(page_count: int) -> List[Dict[str, Any]]:
    return [
        {"page_number": page_num, "start": None, "end": None}
        for page_num in range(1, page_count + 1)
    ]
