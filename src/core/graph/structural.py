"""
Structural Graph Builder
========================

Transforms parsed document chunks into structural graph nodes and edges
(Layer 1). No LLM required -- built deterministically from parser output.

Graph structure:
    (:Document)-[:HAS_SECTION]->(:Section)
    (:Section)-[:HAS_SUBSECTION]->(:Section)
    (:Section)-[:HAS_TABLE]->(:Table)
    (:Section)-[:HAS_FIGURE]->(:Figure)
    (:Chunk)-[:NEXT_CHUNK]->(:Chunk)
    (:Section)-[:REFERENCES_SECTION]->(:Section)  [cross-references]

Graph Construction:
    build_structural_graph      Build nodes and edges from parsed document data

Cross-Reference Extraction:
    _extract_cross_references   Scan chunk text for section cross-references
"""

import logging
import re
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


# ============================================
# Graph Construction
# ============================================

def build_structural_graph(
    doc_hash: str,
    parsed_data: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Build structural nodes and edges from parsed document data.

    Args:
        doc_hash: The document's hash identifier.
        parsed_data: Output of the SDD parser (metadata, chunks, summary).

    Returns:
        {"nodes": [...], "edges": [...], "summary": {...}}
    """
    chunks = parsed_data.get("chunks", [])
    metadata = parsed_data.get("metadata", {})

    nodes: List[Dict[str, Any]] = []
    edges: List[Dict[str, Any]] = []

    # Document node
    doc_node_id = f"doc_{doc_hash}"
    nodes.append({
        "id": doc_node_id,
        "label": "Document",
        "properties": {
            "hash": doc_hash,
            "title": metadata.get("title", ""),
            "doc_number": metadata.get("doc_number"),
        },
    })

    # Track chunk node IDs for NEXT_CHUNK edges
    previous_chunk_id: Optional[str] = None

    label_map = {
        "section": "Section",
        "table": "Table",
        "figure": "Figure",
    }

    for idx, chunk in enumerate(chunks):
        chunk_type = chunk.get("type", "section")
        number = chunk.get("number", str(idx))
        title = chunk.get("title", "")
        content = chunk.get("content", "")
        parent_section = chunk.get("parent_section")
        page_start = chunk.get("page_start")
        page_end = chunk.get("page_end")
        image_path = chunk.get("image_path")

        node_label = label_map.get(chunk_type, "Section")
        chunk_node_id = f"{chunk_type}_{doc_hash}_{number}"

        nodes.append({
            "id": chunk_node_id,
            "label": node_label,
            "properties": {
                "doc_hash": doc_hash,
                "number": number,
                "title": title,
                "content": content,
                "type": chunk_type,
                "page_start": page_start,
                "page_end": page_end,
                "image_path": image_path,
            },
        })

        # Structural edges based on chunk type and parent
        if chunk_type == "section":
            if parent_section is None:
                edges.append({
                    "source_id": doc_node_id,
                    "target_id": chunk_node_id,
                    "type": "HAS_SECTION",
                })
            else:
                parent_node_id = f"section_{doc_hash}_{parent_section}"
                edges.append({
                    "source_id": parent_node_id,
                    "target_id": chunk_node_id,
                    "type": "HAS_SUBSECTION",
                })
        elif chunk_type == "table" and parent_section:
            parent_node_id = f"section_{doc_hash}_{parent_section}"
            edges.append({
                "source_id": parent_node_id,
                "target_id": chunk_node_id,
                "type": "HAS_TABLE",
            })
        elif chunk_type == "figure" and parent_section:
            parent_node_id = f"section_{doc_hash}_{parent_section}"
            edges.append({
                "source_id": parent_node_id,
                "target_id": chunk_node_id,
                "type": "HAS_FIGURE",
            })

        # NEXT_CHUNK chain (sequential reading order)
        if previous_chunk_id is not None:
            edges.append({
                "source_id": previous_chunk_id,
                "target_id": chunk_node_id,
                "type": "NEXT_CHUNK",
            })
        previous_chunk_id = chunk_node_id

    # Cross-section references ("See Section 3.4.6", etc.)
    xref_edges = _extract_cross_references(chunks, nodes, doc_hash)
    edges.extend(xref_edges)

    summary = {
        "total_nodes": len(nodes),
        "total_edges": len(edges),
        "document_nodes": 1,
        "section_nodes": sum(1 for n in nodes if n["label"] == "Section"),
        "table_nodes": sum(1 for n in nodes if n["label"] == "Table"),
        "figure_nodes": sum(1 for n in nodes if n["label"] == "Figure"),
        "cross_reference_edges": len(xref_edges),
    }

    logger.info(
        "Built structural graph: %d nodes, %d edges (%d cross-references)",
        len(nodes), len(edges), len(xref_edges),
    )

    return {"nodes": nodes, "edges": edges, "summary": summary}


# ============================================
# Cross-Reference Extraction
# ============================================

# Matches "Section 3.4.6", "section 3.1", "Sections 3.1 and 3.2", etc.
# Captures the dotted number (e.g., "3.4.6") after the word "Section(s)".
_SECTION_XREF_PATTERN = re.compile(
    r"[Ss]ections?\s+(\d+(?:\.\d+)*)"
    r"(?:\s*(?:,|and|or|&)\s*(\d+(?:\.\d+)*))*",
)

# Simpler fallback: just grab every "Section X.Y.Z" individually
_SECTION_REF_SIMPLE = re.compile(r"[Ss]ections?\s+(\d+(?:\.\d+)*)")


def _extract_cross_references(
    chunks: List[Dict[str, Any]],
    nodes: List[Dict[str, Any]],
    doc_hash: str,
) -> List[Dict[str, Any]]:
    """
    Scan each chunk's text content for cross-references to other sections
    and create REFERENCES_SECTION edges.

    Only creates edges to sections that actually exist in the document
    (validated against the node list). Skips self-references (a section
    referencing itself).

    Returns a list of edge dicts ready to add to the graph.
    """
    # Build set of valid section node IDs
    valid_section_ids: Set[str] = {
        n["id"] for n in nodes if n["label"] == "Section"
    }

    xref_edges: List[Dict[str, Any]] = []
    seen: Set[tuple] = set()  # Dedup (source, target) pairs

    for chunk in chunks:
        chunk_type = chunk.get("type", "section")
        number = chunk.get("number", "")
        content = chunk.get("content", "")

        if not content:
            continue

        source_id = f"{chunk_type}_{doc_hash}_{number}"

        # Find all referenced section numbers in this chunk's text
        referenced_numbers = _SECTION_REF_SIMPLE.findall(content)

        for ref_number in referenced_numbers:
            target_id = f"section_{doc_hash}_{ref_number}"

            # Skip self-references
            if target_id == source_id:
                continue

            # Only link to sections that exist
            if target_id not in valid_section_ids:
                continue

            # Dedup
            edge_key = (source_id, target_id)
            if edge_key in seen:
                continue
            seen.add(edge_key)

            xref_edges.append({
                "source_id": source_id,
                "target_id": target_id,
                "type": "REFERENCES_SECTION",
            })
            logger.debug(
                "Cross-reference: %s -> %s", number, ref_number,
            )

    if xref_edges:
        logger.info(
            "Found %d cross-section references", len(xref_edges),
        )

    return xref_edges
