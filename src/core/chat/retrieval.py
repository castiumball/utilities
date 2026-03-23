"""
RAG retrieval module for Polaris chat.

Retrieves relevant document context from the Neo4j knowledge graph
using BM25 full-text search, LLM query expansion, and entity-based
graph traversal.
"""

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from config import settings
from core.graph.neo4j_writer import get_driver

logger = logging.getLogger(__name__)

# ============================================
# Utilities
# ============================================

_LUCENE_SPECIAL = re.compile(r'([+\-&|!(){}[\]^"~*?:\\/])')

# Matches section-number patterns like "3.1.3.1", "section 3.1", "§3.2"
_SECTION_REF = re.compile(
    r'(?:section|sec\.?|§)\s*(\d+(?:\.\d+)*)'   # "section 3.1.3.1"
    r'|'
    r'\b(\d+\.\d+(?:\.\d+)*)\b',                 # bare "3.1.3.1" (needs at least one dot)
    re.IGNORECASE,
)

# Matches figure/table references like "Figure 6", "Table 3", "fig 2"
_FIGURE_TABLE_REF = re.compile(
    r'(?:figure|fig\.?|table|tbl\.?)\s+(\d+)',
    re.IGNORECASE,
)


def _escape_lucene(query: str) -> str:
    """Escape Lucene special characters in a query string."""
    return _LUCENE_SPECIAL.sub(r"\\\1", query)


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: word count * 1.3."""
    return int(len(text.split()) * 1.3)


# ============================================
# Entity Name Lookup (acronym / alias resolution)
# ============================================

# Labels to search for entity name matches
_ENTITY_LABELS = [
    "System", "CSCI", "CSC", "CSU", "Interface",
    "DataStore", "Function", "Requirement",
    "ExternalSystem", "Message", "Protocol",
]


def _lookup_entity_names(query: str) -> List[str]:
    """
    Search Neo4j for acronym expansions and entity names matching the query.

    Strategy (in priority order):
      1. Acronym nodes — look up terms in the curated Acronym table.
         Returns the expansion (e.g. "MDT" → "Minutia Deviation Tool").
      2. Entity name match — find semantic entities whose name exactly
         matches a query term (case-insensitive).

    Returns a list of name strings not already present in the query.
    """
    _STOPWORDS = {
        "what", "is", "the", "a", "an", "how", "does", "do", "can",
        "where", "when", "which", "who", "why", "are", "was", "were",
        "will", "would", "could", "should", "about", "from", "with",
        "this", "that", "for", "and", "but", "not", "its", "it",
        "of", "in", "on", "to", "by", "or", "be", "has", "have",
        "tell", "me", "describe", "explain", "show",
    }
    words = re.findall(r'\b[A-Za-z0-9]{2,}\b', query)
    terms = [w for w in words if w.lower() not in _STOPWORDS]

    if not terms:
        return []

    try:
        driver = get_driver()
    except Exception:
        return []

    expanded = []
    query_upper = query.upper()

    def _add(name: str):
        """Add a name if not already in query or results."""
        if name and name.upper() not in query_upper and name not in expanded:
            expanded.append(name)

    try:
        with driver.session(database=settings.NEO4J_DATABASE) as session:
            # Strategy 1: Acronym node lookup (curated, authoritative)
            result = session.run(
                "MATCH (a:Acronym) "
                "WHERE any(term IN $terms WHERE toUpper(a.short) = toUpper(term)) "
                "RETURN a.short AS short, a.expansion AS expansion "
                "LIMIT 5",
                terms=terms,
            )
            for record in result:
                expansion = record["expansion"]
                if expansion:
                    # Strip any parenthesized acronyms from the expansion
                    # for a cleaner search term
                    clean = re.sub(r'\s*\([^)]*\)\s*', ' ', expansion).strip()
                    _add(clean)
                    # Also add the raw expansion if different
                    if expansion != clean:
                        _add(expansion)

            # Strategy 2: Exact entity name match (case-insensitive)
            labels_list = "[" + ", ".join(
                f'"{l}"' for l in _ENTITY_LABELS
            ) + "]"
            result = session.run(
                f"MATCH (e) WHERE any(lbl IN labels(e) WHERE lbl IN {labels_list}) "
                "WITH e "
                "WHERE e.name IS NOT NULL AND any(term IN $terms WHERE "
                "  toUpper(e.name) = toUpper(term)"
                ") "
                "RETURN DISTINCT e.name AS name, e.description AS desc "
                "LIMIT 5",
                terms=terms,
            )
            for record in result:
                name = record["name"]
                _add(name)

    except Exception:
        logger.warning("Entity name lookup failed", exc_info=True)

    return expanded


# ============================================
# LLM Query Expansion
# ============================================

def expand_query(
    llm, user_query: str, known_terms: Optional[List[str]] = None,
) -> List[str]:
    """
    Use the LLM to generate multiple search terms from a user question.

    If known_terms are provided (e.g. from entity/acronym lookup), they
    are included in the prompt so the LLM can generate grounded expansions
    instead of guessing.

    Returns a list of search strings to run against BM25.
    """
    from langchain_core.messages import HumanMessage, SystemMessage

    # Build context block if we have resolved terms
    context_block = ""
    if known_terms:
        context_block = (
            "\n\nThe following terms have been resolved from the knowledge base "
            "and are known to be correct. Use them in your queries instead of "
            "guessing acronym expansions:\n"
            + "\n".join(f"  - {t}" for t in known_terms)
            + "\n"
        )

    expansion_prompt = SystemMessage(content=(
        "You are a search query generator for a technical documentation search engine. "
        "Given a user question, generate 2-4 search queries that rephrase or reformat "
        "the user's actual terms to improve search recall.\n\n"
        "Guidelines:\n"
        "- Keep the user's original key terms — rephrase, don't reinvent.\n"
        "- Generate useful alternate forms: abbreviations, shorthand, or different "
        "word order (e.g. 'section 3.1' → 'sec 3.1', '3.1').\n"
        "- If resolved terms are provided below, use those exact terms — do NOT "
        "guess alternative expansions for acronyms.\n"
        "- Do NOT add generic technical concepts, related topics, or terms the "
        "user did not mention or imply.\n"
        "- Fewer, precise queries are better than many speculative ones.\n\n"
        "Return ONLY a JSON array of strings, nothing else.\n"
        "Example: user asks 'What is section 3.1?' → "
        '[\"section 3.1\", \"sec 3.1\", \"3.1\"]'
        + context_block
    ))

    try:
        response = llm.invoke([
            expansion_prompt,
            HumanMessage(content=f"Generate search queries for: {user_query}")
        ])

        # Parse JSON array from response
        text = response.content.strip()
        # Handle cases where LLM wraps in markdown code blocks
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()

        queries = json.loads(text)
        if isinstance(queries, list) and all(isinstance(q, str) for q in queries):
            logger.info("Query expansion: %s -> %s", user_query[:50], queries)
            return queries
    except Exception:
        logger.warning("Query expansion failed, using original query", exc_info=True)

    # Fallback: just use the original query
    return [user_query]


# ============================================
# Exact Section Number Lookup
# ============================================

def _extract_section_numbers(query: str) -> List[str]:
    """
    Extract section number references from a user query.

    Recognizes patterns like:
      "section 3.1.3.1", "sec 2.1", "§3.2", "3.1.3.1"
    """
    numbers = []
    for match in _SECTION_REF.finditer(query):
        # Group 1 = prefixed ("section 3.1"), Group 2 = bare ("3.1.3.1")
        num = match.group(1) or match.group(2)
        if num and num not in numbers:
            numbers.append(num)
    return numbers


def _exact_section_lookup(section_numbers: List[str]) -> List[Dict[str, Any]]:
    """
    Look up sections by exact number match in Neo4j.

    Returns chunk dicts with a high synthetic score (10.0) so they
    always rank above BM25 results.
    """
    if not section_numbers:
        return []

    driver = get_driver()

    cypher = (
        "UNWIND $numbers AS num "
        "MATCH (n:Section {number: num}) "
        "RETURN n.id AS id, "
        "       n.content AS content, "
        "       n.title AS title, "
        "       n.number AS number, "
        "       n.page_start AS page_start, "
        "       n.page_end AS page_end, "
        "       n.doc_hash AS doc_hash, "
        "       'Section' AS label"
    )

    with driver.session(database=settings.NEO4J_DATABASE) as session:
        result = session.run(cypher, numbers=section_numbers)
        chunks = []
        for record in result:
            chunk = dict(record)
            chunk["score"] = 10.0  # Exact match — always ranks first
            chunk["exact_match"] = True
            chunks.append(chunk)

        if chunks:
            logger.info(
                "Exact section match: %s -> %d results",
                section_numbers, len(chunks),
            )
        return chunks


def _extract_figure_table_numbers(query: str) -> List[Tuple[str, str]]:
    """
    Extract figure/table references from a user query.

    Returns list of (label, number) tuples, e.g. [("Figure", "6"), ("Table", "3")].
    """
    refs = []
    for match in _FIGURE_TABLE_REF.finditer(query):
        keyword = match.group(0).split()[0].lower()
        num = match.group(1)
        label = "Figure" if keyword.startswith("fig") else "Table"
        ref = (label, num)
        if ref not in refs:
            refs.append(ref)
    return refs


def _exact_figure_table_lookup(
    refs: List[Tuple[str, str]],
) -> List[Dict[str, Any]]:
    """
    Look up Figure/Table nodes by exact number match.

    Handles both string and integer number properties in Neo4j
    by matching with toInteger() conversion.
    """
    if not refs:
        return []

    driver = get_driver()

    # Build separate lists for figures and tables
    figure_nums = [int(num) for label, num in refs if label == "Figure"]
    table_nums = [int(num) for label, num in refs if label == "Table"]

    chunks = []

    with driver.session(database=settings.NEO4J_DATABASE) as session:
        if figure_nums:
            cypher = (
                "UNWIND $numbers AS num "
                "MATCH (n:Figure) "
                "WHERE n.number = num OR n.number = toString(num) "
                "RETURN n.id AS id, "
                "       n.content AS content, "
                "       n.title AS title, "
                "       n.number AS number, "
                "       n.page_start AS page_start, "
                "       n.page_end AS page_end, "
                "       n.doc_hash AS doc_hash, "
                "       n.image_path AS image_path, "
                "       'Figure' AS label"
            )
            result = session.run(cypher, numbers=figure_nums)
            for record in result:
                chunk = dict(record)
                chunk["score"] = 10.0
                chunk["exact_match"] = True
                chunks.append(chunk)

        if table_nums:
            cypher = (
                "UNWIND $numbers AS num "
                "MATCH (n:Table) "
                "WHERE n.number = num OR n.number = toString(num) "
                "RETURN n.id AS id, "
                "       n.content AS content, "
                "       n.title AS title, "
                "       n.number AS number, "
                "       n.page_start AS page_start, "
                "       n.page_end AS page_end, "
                "       n.doc_hash AS doc_hash, "
                "       n.image_path AS image_path, "
                "       'Table' AS label"
            )
            result = session.run(cypher, numbers=table_nums)
            for record in result:
                chunk = dict(record)
                chunk["score"] = 10.0
                chunk["exact_match"] = True
                chunks.append(chunk)

    if chunks:
        logger.info(
            "Exact figure/table match: %s -> %d results",
            refs, len(chunks),
        )
    return chunks


# ============================================
# BM25 Full-Text Search
# ============================================

def _bm25_search(query: str, limit: int, min_score: float) -> List[Dict[str, Any]]:
    """
    Search structural nodes (Section, Table, Figure) using Neo4j
    full-text index with BM25 scoring.

    Returns a list of chunk dicts sorted by relevance score.
    """
    driver = get_driver()
    escaped = _escape_lucene(query)

    if not escaped.strip():
        return []

    cypher = (
        "CALL db.index.fulltext.queryNodes('structural_content_ft', $search_term) "
        "YIELD node, score "
        "WHERE score >= $min_score "
        "RETURN node.id AS id, "
        "       node.content AS content, "
        "       node.title AS title, "
        "       node.number AS number, "
        "       node.page_start AS page_start, "
        "       node.page_end AS page_end, "
        "       node.doc_hash AS doc_hash, "
        "       node.image_path AS image_path, "
        "       labels(node)[0] AS label, "
        "       node.pagerank AS pagerank, "
        "       score "
        "ORDER BY score DESC "
        "LIMIT $limit"
    )

    with driver.session(database=settings.NEO4J_DATABASE) as session:
        result = session.run(cypher, search_term=escaped, min_score=min_score, limit=limit)
        return [dict(record) for record in result]


# ============================================
# Vector Search
# ============================================

_VECTOR_INDEX_NAMES = {
    "Section": "section_embedding_idx",
    "Table": "table_embedding_idx",
    "Figure": "figure_embedding_idx",
}


def _vector_search(
    query: str, limit: int, min_score: float
) -> List[Dict[str, Any]]:
    """
    Search structural nodes using embedding similarity via Neo4j vector indexes.

    Queries all three label-specific vector indexes (Section, Table, Figure)
    and merges results by score. Returns gracefully empty if indexes don't
    exist or nodes lack embeddings.
    """
    try:
        from core.graph.embeddings import embed_query
    except ImportError:
        logger.debug("sentence-transformers not installed; skipping vector search")
        return []

    driver = get_driver()

    try:
        query_embedding = embed_query(query)
    except Exception as exc:
        logger.warning("Failed to generate query embedding: %s", exc)
        return []

    all_results = []

    for label, idx_name in _VECTOR_INDEX_NAMES.items():
        cypher = (
            f"CALL db.index.vector.queryNodes('{idx_name}', $limit, $embedding) "
            "YIELD node, score "
            "WHERE score >= $min_score "
            "RETURN node.id AS id, "
            "       node.content AS content, "
            "       node.title AS title, "
            "       node.number AS number, "
            "       node.page_start AS page_start, "
            "       node.page_end AS page_end, "
            "       node.doc_hash AS doc_hash, "
            "       node.image_path AS image_path, "
            f"      '{label}' AS label, "
            "       node.pagerank AS pagerank, "
            "       score "
            "ORDER BY score DESC "
            "LIMIT $limit"
        )

        try:
            with driver.session(database=settings.NEO4J_DATABASE) as session:
                result = session.run(
                    cypher, embedding=query_embedding,
                    min_score=min_score, limit=limit,
                )
                all_results.extend(dict(record) for record in result)
        except Exception as exc:
            # Index may not exist yet (pre-backfill) — graceful degradation
            logger.debug("Vector search on %s failed: %s", idx_name, exc)

    # Sort by score descending, cap at limit
    all_results.sort(key=lambda c: c.get("score", 0), reverse=True)
    return all_results[:limit]


# ============================================
# Reciprocal Rank Fusion
# ============================================

_PAGERANK_WEIGHT = 0.1  # Small boost — tiebreaker, not dominant


def _reciprocal_rank_fusion(
    result_lists: List[List[Dict[str, Any]]],
    k: int = 60,
) -> List[Dict[str, Any]]:
    """
    Fuse multiple ranked result lists using Reciprocal Rank Fusion (RRF).

    RRF score for each chunk = sum over lists of 1/(k + rank).
    PageRank is added as a small tiebreaker boost.

    Returns chunks sorted by fused score, with 'score' set to the RRF score
    and 'source_scores' preserving per-signal scores.
    """
    # Collect all unique chunks by id, tracking their ranks per list
    chunks_by_id: Dict[str, Dict[str, Any]] = {}
    rrf_scores: Dict[str, float] = {}

    for list_idx, results in enumerate(result_lists):
        for rank, chunk in enumerate(results, start=1):
            cid = chunk["id"]
            rrf_scores.setdefault(cid, 0.0)
            rrf_scores[cid] += 1.0 / (k + rank)

            # Keep the chunk data (first occurrence wins for metadata)
            if cid not in chunks_by_id:
                chunks_by_id[cid] = dict(chunk)
                chunks_by_id[cid]["source_scores"] = {}
            # Track per-signal scores
            signal_name = f"signal_{list_idx}"
            chunks_by_id[cid]["source_scores"][signal_name] = chunk.get("score", 0)

    # Add PageRank boost
    for cid, chunk in chunks_by_id.items():
        pagerank = chunk.get("pagerank") or 0.0
        rrf_scores[cid] += _PAGERANK_WEIGHT * pagerank

    # Set fused score and sort
    fused = []
    for cid, chunk in chunks_by_id.items():
        chunk["rrf_score"] = rrf_scores[cid]
        chunk["score"] = rrf_scores[cid]
        fused.append(chunk)

    fused.sort(key=lambda c: c["rrf_score"], reverse=True)
    return fused


def _multi_query_search(
    queries: List[str], limit: int, min_score: float
) -> List[Dict[str, Any]]:
    """
    Run multiple BM25 searches and merge results.
    Higher-scoring duplicates take precedence.
    """
    seen = {}  # id -> chunk dict (keep highest score)

    for query in queries:
        results = _bm25_search(query, limit=limit, min_score=min_score)
        for chunk in results:
            cid = chunk["id"]
            if cid not in seen or chunk["score"] > seen[cid]["score"]:
                seen[cid] = chunk

    merged = list(seen.values())
    merged.sort(key=lambda c: c.get("score", 0), reverse=True)
    return merged


# ============================================
# Structural Graph Expansion (parent + siblings)
# ============================================

def _expand_structural(
    exact_section_ids: List[str],
) -> List[Dict[str, Any]]:
    """
    Given exact-matched section IDs, follow the structural graph to
    pull in parent and sibling sections via HAS_SUBSECTION edges.

    This ensures that asking about section 1.2.3 also retrieves
    1.2 (parent) and 1.2.1, 1.2.2, 1.2.4 (siblings).
    """
    if not exact_section_ids:
        return []

    driver = get_driver()

    # Find parent and all of parent's children (siblings of matched section)
    cypher = (
        "UNWIND $section_ids AS sid "
        "MATCH (parent:Section)-[:HAS_SUBSECTION]->(matched:Section {id: sid}) "
        "OPTIONAL MATCH (parent)-[:HAS_SUBSECTION]->(sibling:Section) "
        "WITH parent, sibling, sid "
        "WHERE parent.id <> sid AND (sibling IS NULL OR sibling.id <> sid) "
        "WITH collect(DISTINCT parent) + collect(DISTINCT sibling) AS nodes "
        "UNWIND nodes AS n "
        "RETURN DISTINCT n.id AS id, "
        "       n.content AS content, "
        "       n.title AS title, "
        "       n.number AS number, "
        "       n.page_start AS page_start, "
        "       n.page_end AS page_end, "
        "       n.doc_hash AS doc_hash, "
        "       'Section' AS label"
    )

    with driver.session(database=settings.NEO4J_DATABASE) as session:
        result = session.run(cypher, section_ids=exact_section_ids)
        expanded = []
        for record in result:
            chunk = dict(record)
            chunk["score"] = 5.0  # Below exact (10) but above BM25
            expanded.append(chunk)

        if expanded:
            logger.info(
                "Structural expansion: %d parent/sibling sections",
                len(expanded),
            )
        return expanded


# ============================================
# Entity-Based Graph Expansion
# ============================================

def _expand_via_entities(
    chunk_ids: List[str], hop_depth: int
) -> List[Dict[str, Any]]:
    """
    Follow EXTRACTED_FROM edges from matched chunks to entities,
    then traverse entity relationships to find related chunks.

    Returns additional chunk dicts with a synthetic score for ranking.
    """
    if not chunk_ids or hop_depth < 1:
        return []

    driver = get_driver()

    cypher = (
        "UNWIND $chunk_ids AS cid "
        "MATCH (entity)-[:EXTRACTED_FROM]->(chunk {id: cid}) "
        "WITH DISTINCT entity "
        "MATCH (entity)-[r]->(related) "
        "WHERE type(r) IN ['CONTAINS', 'INTERFACES_WITH', 'DEPENDS_ON', "
        "                   'IMPLEMENTS', 'READS_FROM', 'WRITES_TO', 'CALLS'] "
        "MATCH (related)-[:EXTRACTED_FROM]->(source_chunk) "
        "WHERE NOT source_chunk.id IN $chunk_ids "
        "RETURN DISTINCT source_chunk.id AS id, "
        "       source_chunk.content AS content, "
        "       source_chunk.title AS title, "
        "       source_chunk.number AS number, "
        "       source_chunk.page_start AS page_start, "
        "       source_chunk.page_end AS page_end, "
        "       source_chunk.doc_hash AS doc_hash, "
        "       source_chunk.image_path AS image_path, "
        "       labels(source_chunk)[0] AS label, "
        "       entity.name AS via_entity, "
        "       type(r) AS via_relationship"
    )

    with driver.session(database=settings.NEO4J_DATABASE) as session:
        result = session.run(cypher, chunk_ids=chunk_ids)
        expanded = []
        for record in result:
            chunk = dict(record)
            chunk["score"] = 0.3
            expanded.append(chunk)
        return expanded


# ============================================
# Quality Filtering & Score Adjustment
# ============================================

# Minimum content length to include a chunk (filters noise from
# near-empty nodes like sparse figure captions).
_MIN_CONTENT_LENGTH = 20

# Score multipliers by node type — Section content is usually what
# the user is looking for; Table/Figure are supporting material.
_LABEL_WEIGHT = {
    "Section": 1.0,
    "Table":   0.85,
    "Figure":  0.7,
}


def _apply_quality_filters(chunks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Filter out low-quality chunks and apply node-type score weighting.

    - Drops chunks whose content is too short (< 20 chars), unless they
      are exact matches (user specifically asked for this section/figure).
    - Figures with an image but no text content are only kept if the user
      explicitly requested them (exact match). Otherwise they waste
      selection slots that could go to text-rich chunks.
    - Applies a score multiplier based on node type so Sections rank
      above Tables/Figures at equal BM25 scores.
    """
    filtered = []
    for chunk in chunks:
        content = (chunk.get("content") or "").strip()
        label = chunk.get("label", "Section")

        # Drop near-empty chunks — only keep exact matches (user
        # specifically asked for this section/figure/table)
        if len(content) < _MIN_CONTENT_LENGTH:
            if chunk.get("exact_match"):
                pass  # Always keep what the user explicitly asked for
            else:
                logger.debug(
                    "Filtered sparse chunk %s (%s, %d chars)",
                    chunk.get("id", "?"), label, len(content),
                )
                continue

        # Apply node-type weight to score (skip for exact matches at 10.0)
        score = chunk.get("score", 0)
        if score < 10.0:
            weight = _LABEL_WEIGHT.get(label, 1.0)
            chunk["score"] = score * weight

        filtered.append(chunk)

    return filtered


# ============================================
# LLM Reranking
# ============================================

# How many top candidates (by score) to send to the reranker.
# Must be large enough to include entity-expanded chunks (score 0.3)
# that BM25 missed but are semantically important.
_RERANK_CANDIDATE_COUNT = 20

# Max content chars per chunk in the rerank prompt (truncate long chunks)
_RERANK_PREVIEW_CHARS = 300


def _llm_rerank(
    query: str, chunks: List[Dict[str, Any]], llm,
) -> List[Dict[str, Any]]:
    """
    Use the LLM to re-score chunks by semantic relevance to the query.

    Sends the top BM25 candidates to the LLM in a single prompt,
    asking it to rate each chunk's relevance on a 0-10 scale.
    Returns chunks re-sorted by LLM relevance score.

    Chunks that weren't sent to the reranker (lower BM25 ranks) are
    appended at the end with their original scores preserved.
    """
    from langchain_core.messages import HumanMessage, SystemMessage

    if not chunks or not llm:
        return chunks

    # Take top N by current score for reranking
    candidates = chunks[:_RERANK_CANDIDATE_COUNT]
    remainder = chunks[_RERANK_CANDIDATE_COUNT:]

    # Build the prompt with numbered chunk previews
    chunk_texts = []
    for i, chunk in enumerate(candidates):
        label = chunk.get("label", "Section")
        number = chunk.get("number", "")
        title = chunk.get("title", "")
        content = (chunk.get("content") or "").strip()
        preview = content[:_RERANK_PREVIEW_CHARS]
        if len(content) > _RERANK_PREVIEW_CHARS:
            preview += "..."
        chunk_texts.append(
            f"[{i}] {label} {number} - {title}\n{preview}"
        )

    documents_block = "\n\n".join(chunk_texts)

    rerank_prompt = (
        f"User query: \"{query}\"\n\n"
        f"Rate how relevant each document below is to the user's query. "
        f"Return ONLY a JSON array of objects with 'index' and 'score' (0-10), "
        f"where 10 means perfectly relevant. Example: "
        f'[{{"index": 0, "score": 8}}, {{"index": 1, "score": 2}}]\n\n'
        f"Documents:\n{documents_block}"
    )

    try:
        response = llm.invoke([
            SystemMessage(content=(
                "You are a relevance scoring assistant. Given a query and "
                "documents, rate each document's relevance from 0 to 10. "
                "Return ONLY valid JSON — no explanation."
            )),
            HumanMessage(content=rerank_prompt),
        ])

        text = response.content.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()

        scores = json.loads(text)
        if not isinstance(scores, list):
            raise ValueError("Expected JSON array")

        # Apply LLM scores to candidates
        score_map = {}
        for entry in scores:
            if isinstance(entry, dict) and "index" in entry and "score" in entry:
                idx = int(entry["index"])
                if 0 <= idx < len(candidates):
                    score_map[idx] = float(entry["score"])

        for i, chunk in enumerate(candidates):
            if i in score_map:
                chunk["pre_rerank_score"] = chunk.get("score", 0)
                chunk["score"] = score_map[i]

        # Re-sort candidates by LLM score
        candidates.sort(key=lambda c: c.get("score", 0), reverse=True)

        logger.info(
            "LLM reranking: rescored %d/%d candidates",
            len(score_map), len(candidates),
        )

        return candidates + remainder

    except Exception:
        logger.warning("LLM reranking failed, using BM25 order", exc_info=True)
        return chunks


# ============================================
# Context Formatting
# ============================================

def _format_context(chunks: List[Dict[str, Any]]) -> str:
    """
    Format retrieved chunks into a clear reference block for the LLM.

    For Figure chunks with an image_path, includes a markdown image tag
    so the LLM can reference the figure visually in its response.
    """
    parts = [
        "The following reference material was retrieved from the STARS documentation:\n"
    ]

    for i, chunk in enumerate(chunks, 1):
        label = chunk.get("label", "Section")
        number = chunk.get("number", "")
        title = chunk.get("title", "")

        page_info = ""
        if chunk.get("page_start"):
            page_info = f" (p. {chunk['page_start']}"
            if chunk.get("page_end") and chunk["page_end"] != chunk["page_start"]:
                page_info = f" (pp. {chunk['page_start']}-{chunk['page_end']}"
            page_info += ")"

        header = f"[{label} {number}] {title}{page_info}".strip()
        content = (chunk.get("content") or "").strip()

        # For figures with an extracted image, include a markdown image tag.
        # Uses a relative URL (../api/) so it resolves correctly whether
        # accessed directly (/chat/) or via reverse proxy (/polaris_v1/chat/).
        image_md = ""
        if label == "Figure" and chunk.get("image_path") and chunk.get("doc_hash"):
            img_url = f"../api/documents/{chunk['doc_hash']}/figures/{chunk['image_path']}"
            alt_text = f"Figure {number}: {title}" if title else f"Figure {number}"
            image_md = f"\n![{alt_text}]({img_url})\n"

        parts.append(f"--- Source {i}: {header} ---\n{content}{image_md}\n")

    parts.append(
        "---\n"
        "Use the above information to answer the user's question accurately. "
        "Do NOT mention 'reference material', 'context', 'sources', or that "
        "information was 'retrieved' — present your knowledge naturally. "
        "If you lack information, say 'I don't have detailed information on that.' "
        "CRITICAL: If ANY source above contains a markdown image tag (![...](...))), "
        "you MUST copy it exactly into your response. Do NOT say 'I cannot display images' — "
        "the interface renders images from markdown. Always include the full image tag."
    )
    return "\n".join(parts)


# ============================================
# Chunk Selection (token budget)
# ============================================

def _select_chunks(
    all_chunks: List[Dict[str, Any]],
    max_chunks: int,
    token_budget: int,
) -> Tuple[List[Dict[str, Any]], int]:
    """
    Select chunks within the token budget. Returns (selected, tokens_used).
    """
    max_chunk_tokens = token_budget // 2
    selected = []
    tokens_used = 0

    for chunk in all_chunks:
        if len(selected) >= max_chunks:
            break

        content = (chunk.get("content") or "").strip()
        chunk_tokens = _estimate_tokens(content)

        if chunk_tokens > max_chunk_tokens:
            words = content.split()
            max_words = int(max_chunk_tokens / 1.3)
            content = " ".join(words[:max_words]) + " [...]"
            chunk["content"] = content
            chunk_tokens = max_chunk_tokens

        if tokens_used + chunk_tokens > token_budget:
            if not selected:
                selected.append(chunk)
            break

        tokens_used += chunk_tokens
        selected.append(chunk)

    return selected, tokens_used


# ============================================
# Retrieval Gate (skip retrieval for non-doc queries)
# ============================================

# Short conversational messages that don't need documentation lookup.
_SKIP_EXACT = frozenset([
    "hello", "hi", "hey", "thanks", "thank you", "ok", "okay",
    "yes", "no", "sure", "bye", "goodbye", "good morning",
    "good afternoon", "good evening", "good night", "please",
    "help", "cool", "great", "got it", "understood", "np",
    "ty", "thx", "yep", "nope", "lol", "haha",
])

# Longer conversational phrases that should also skip retrieval.
_SKIP_PHRASES = (
    "hello, how are you", "how are you", "how are you doing",
    "how's it going", "what's up", "nice to meet you",
    "good to see you", "can you help me",
)

# If the message starts with these, it's likely general chitchat.
_SKIP_PREFIXES = (
    "tell me a joke", "tell me about the tv",
    "tell me about the movie", "what is the weather",
    "who won the", "what year did", "how old is",
    "tell me a fun fact", "give me trivia",
    "what is the capital of", "who is the president",
    "how do you say",
)

# Simple math pattern: "what is 2 + 2", "calculate 5 * 3", etc.
_MATH_PATTERN = re.compile(
    r"^(?:what is|what\'s|calculate|compute|solve)\s+\d+\s*[+\-*/x×÷]\s*\d+",
    re.IGNORECASE,
)


def _should_skip_retrieval(query: str) -> bool:
    """
    Fast heuristic to skip retrieval for obvious non-documentation queries.

    Returns True if the query is clearly conversational or off-topic,
    meaning retrieval would just inject irrelevant noise.
    """
    cleaned = query.strip().rstrip("!?.").lower()

    # Exact match on common conversational phrases
    if cleaned in _SKIP_EXACT:
        return True

    # Longer conversational phrases
    if any(cleaned.startswith(p) for p in _SKIP_PHRASES):
        return True

    # Very short messages (1-2 words) without technical markers
    words = cleaned.split()
    if len(words) <= 2:
        # But don't skip if it contains a section/figure/table reference
        # or looks like a technical term (e.g., "MDT", "CSCI")
        has_ref = (_SECTION_REF.search(query) or _FIGURE_TABLE_REF.search(query))
        has_uppercase_acronym = any(w.isupper() and len(w) >= 2 for w in words)
        if not has_ref and not has_uppercase_acronym:
            return True

    # Simple math expressions
    if _MATH_PATTERN.search(cleaned):
        return True

    # Off-topic prefixes
    if any(cleaned.startswith(p) for p in _SKIP_PREFIXES):
        return True

    return False


# ============================================
# Main Retrieval Function (with query expansion)
# ============================================

def retrieve_context(
    query: str,
    llm=None,
    status_callback=None,
    reasoning_callback=None,
) -> Optional[str]:
    """
    Retrieve relevant document context for a user query.

    Args:
        query: The user's question.
        llm: Optional LangChain LLM for query expansion. If None, uses
             raw query only (no expansion).
        status_callback: Optional callable(str) to send status updates
                         to the frontend via SSE.
        reasoning_callback: Optional callable(label, detail) to send
                            structured reasoning steps to the frontend.

    Returns:
        Formatted context string, or None if no relevant results found.
    """
    if not query or not query.strip():
        return None

    # Gate: skip retrieval for conversational / off-topic messages
    if _should_skip_retrieval(query):
        logger.debug("Skipping retrieval for non-doc query: %s", query[:60])
        return None

    def _status(msg: str):
        if status_callback:
            status_callback(msg)

    def _reasoning(label: str, detail: str, description: str = ""):
        if reasoning_callback:
            reasoning_callback(label, detail, description)

    max_chunks = settings.RAG_MAX_CHUNKS
    token_budget = settings.RAG_TOKEN_BUDGET

    # Step 0: Entity name lookup — resolve acronyms/aliases from the
    # knowledge graph before LLM expansion. This gives us grounded
    # expansions (e.g. "MDT" → "Minutia Deviation Tool") that the LLM
    # couldn't know without access to the corpus.
    entity_names = _lookup_entity_names(query)
    if entity_names:
        _reasoning(
            "Entity lookup",
            f'Found: {json.dumps(entity_names)}',
            "Resolved names/acronyms from the knowledge graph",
        )

    # Step 1: Query expansion (if LLM available)
    # Pass entity names so the LLM uses grounded terms instead of guessing
    if llm:
        _status("Analyzing question...")
        search_queries = expand_query(llm, query, known_terms=entity_names or None)
        _reasoning(
            "Query expansion",
            f'"{query[:80]}" \u2192 {json.dumps(search_queries)}',
            "LLM generates alternative search terms from your question",
        )
    else:
        search_queries = [query]
        _reasoning("Query", f'"{query[:80]}"', "Original search query")

    # Merge entity-resolved names into search queries (deduplicated)
    if entity_names:
        existing_upper = {q.upper() for q in search_queries}
        for name in entity_names:
            if name.upper() not in existing_upper:
                search_queries.append(name)
                existing_upper.add(name.upper())

    # Step 2: Exact lookups (bypass BM25 tokenization issues)
    section_numbers = _extract_section_numbers(query)
    exact_chunks = _exact_section_lookup(section_numbers)

    figure_table_refs = _extract_figure_table_numbers(query)
    exact_chunks += _exact_figure_table_lookup(figure_table_refs)

    if exact_chunks:
        exact_labels = []
        for c in exact_chunks:
            label = c.get("label", "")
            title = c.get("title", "")
            exact_labels.append(f"{label} {title}".strip() if title else label)
        _reasoning(
            "Exact match",
            f"Matched {', '.join(exact_labels[:5])}"
            + (f" (+{len(exact_labels) - 5} more)" if len(exact_labels) > 5 else ""),
            "Direct lookup by section number or figure/table reference",
        )

    # Step 2b: Structural expansion — pull in parent/sibling sections
    exact_section_ids = [c["id"] for c in exact_chunks if c.get("label") == "Section"]
    structural_chunks = _expand_structural(exact_section_ids)

    if structural_chunks:
        _reasoning(
            "Structural expansion",
            f"+{len(structural_chunks)} parent/sibling sections",
            "Pulls in parent and sibling sections for surrounding context",
        )

    # Step 3: Multi-query BM25 search
    _status("Searching knowledge base...")
    bm25_results = _multi_query_search(
        search_queries,
        limit=settings.RAG_BM25_RESULT_LIMIT,
        min_score=settings.RAG_MIN_SCORE,
    )

    if bm25_results:
        top = bm25_results[0]
        top_title = top.get("title", "untitled")
        top_score = top.get("score", 0)
        _reasoning(
            "BM25 search",
            f"{len(search_queries)} queries \u2192 {len(bm25_results)} results "
            f'(top: "{top_title}" score {top_score:.1f})',
            "Full-text search across all document chunks",
        )

    # Step 3b: Vector similarity search
    vector_results = _vector_search(
        query,
        limit=settings.RAG_VECTOR_RESULT_LIMIT,
        min_score=settings.RAG_VECTOR_MIN_SCORE,
    )

    if vector_results:
        vtop = vector_results[0]
        _reasoning(
            "Vector search",
            f"{len(vector_results)} results "
            f'(top: "{vtop.get("title", "untitled")}" score {vtop.get("score", 0):.3f})',
            "Embedding similarity search for semantic matches",
        )

    # Step 3c: Fuse BM25 + vector results with RRF (+ PageRank boost)
    if bm25_results or vector_results:
        fused_results = _reciprocal_rank_fusion(
            [bm25_results, vector_results],
            k=settings.RAG_RRF_K,
        )
        if fused_results:
            _reasoning(
                "RRF fusion",
                f"{len(fused_results)} unique chunks from "
                f"{len(bm25_results)} BM25 + {len(vector_results)} vector results",
                "Reciprocal Rank Fusion combines rankings with PageRank boost",
            )
    else:
        fused_results = []

    if not fused_results and not exact_chunks:
        logger.debug("No search results for queries: %s", search_queries)
        _reasoning("Search", "No results found", "Neither BM25 nor vector search returned results")
        _status("")
        return None

    # Step 4: Entity-based expansion
    all_bm25_and_exact = exact_chunks + structural_chunks + fused_results
    _status(f"Found {len(all_bm25_and_exact)} results, expanding context...")
    chunk_ids_for_expansion = [r["id"] for r in all_bm25_and_exact]
    expanded = _expand_via_entities(chunk_ids_for_expansion, settings.RAG_ENTITY_HOP_DEPTH)

    # Step 5: Merge and deduplicate
    seen_ids = {c["id"] for c in all_bm25_and_exact}
    all_chunks = list(all_bm25_and_exact)

    new_from_entities = 0
    for chunk in expanded:
        if chunk["id"] not in seen_ids:
            seen_ids.add(chunk["id"])
            all_chunks.append(chunk)
            new_from_entities += 1

    if new_from_entities:
        _reasoning(
            "Entity expansion",
            f"+{new_from_entities} related chunks via entity graph",
            "Graph traversal to find related content through entity relationships",
        )

    # Step 5b: Quality filtering and node-type score weighting
    pre_filter_count = len(all_chunks)
    all_chunks = _apply_quality_filters(all_chunks)

    all_chunks.sort(key=lambda c: c.get("score", 0), reverse=True)

    # Step 6: LLM reranking — re-score top candidates by semantic relevance
    if llm and len(all_chunks) > 1:
        _status("Reranking results...")
        all_chunks = _llm_rerank(query, all_chunks, llm)

        if all_chunks and "pre_rerank_score" in all_chunks[0]:
            top = all_chunks[0]
            _reasoning(
                "Reranking",
                f'Top: "{top.get("title", "")}" '
                f'(LLM: {top.get("score", 0):.0f}, fusion: {top.get("pre_rerank_score", 0):.3f})',
                "LLM re-scored top candidates by semantic relevance to your question",
            )

    # Step 7: Select within token budget
    selected, tokens_used = _select_chunks(all_chunks, max_chunks, token_budget)

    if not selected:
        _status("")
        return None

    # Step 8: Sufficiency check — ask LLM if results seem relevant
    # Skip if reranking already ran (the reranker handles relevance).
    # Otherwise, if the top score is low, try a refined search.
    reranked = selected and "pre_rerank_score" in selected[0]
    top_score_val = selected[0].get("score", 0) if selected else 0
    if llm and selected and not reranked and top_score_val < 1.5:
        _status("Refining search...")
        _reasoning(
            "Low confidence",
            f"Top score {top_score_val:.3f} < 1.5 \u2014 refining search",
            "Best result scored low, asking LLM for better search terms",
        )
        # Build a brief summary of what we found
        found_titles = [c.get("title", "") for c in selected[:3] if c.get("title")]
        refine_prompt = (
            f"The user asked: \"{query}\"\n"
            f"The search found these sections: {found_titles}\n"
            f"These may not be relevant. Suggest 2-3 alternative search terms "
            f"that might find better results. Return ONLY a JSON array of strings."
        )
        try:
            from langchain_core.messages import HumanMessage, SystemMessage
            response = llm.invoke([
                SystemMessage(content="You are a search refinement assistant. Return only a JSON array of search terms."),
                HumanMessage(content=refine_prompt),
            ])
            text = response.content.strip()
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
                text = text.strip()
            refined_queries = json.loads(text)
            if isinstance(refined_queries, list):
                logger.info("Refined search queries: %s", refined_queries)
                _reasoning(
                    "Refined queries",
                    json.dumps(refined_queries),
                    "LLM-suggested alternative search terms",
                )
                refined_results = _multi_query_search(
                    refined_queries,
                    limit=settings.RAG_BM25_RESULT_LIMIT,
                    min_score=settings.RAG_MIN_SCORE,
                )
                # Merge refined results with original
                new_refined = 0
                for chunk in refined_results:
                    if chunk["id"] not in seen_ids:
                        seen_ids.add(chunk["id"])
                        all_chunks.append(chunk)
                        new_refined += 1

                if new_refined:
                    _reasoning(
                        "Refinement results",
                        f"+{new_refined} new results from refined search",
                        "Additional results found using refined search terms",
                    )

                all_chunks.sort(key=lambda c: c.get("score", 0), reverse=True)
                selected, tokens_used = _select_chunks(
                    all_chunks, max_chunks, token_budget
                )
        except Exception:
            logger.warning("Search refinement failed", exc_info=True)

    num_sources = len(selected)
    _status(f"Retrieved {num_sources} source{'s' if num_sources != 1 else ''}")

    # Build a detail string showing each selected chunk's title and score
    source_lines = []
    for i, c in enumerate(selected, 1):
        label = c.get("label", "")
        number = c.get("number", "")
        title = c.get("title", "untitled")
        score = c.get("score", 0)
        content_len = len((c.get("content") or ""))
        source_lines.append(f'{i}. [{label} {number}] {title} (score {score:.1f}, {content_len} chars)')
    sources_detail = "\n".join(source_lines)

    _reasoning(
        "Selection",
        f"Selected {num_sources} source{'s' if num_sources != 1 else ''} "
        f"(~{tokens_used} tokens) from {len(all_chunks)} candidates:\n{sources_detail}",
        "Best results chosen within the token budget to use as context",
    )

    logger.info(
        "RAG retrieved %d chunks (~%d tokens) from %d candidates for: %s",
        len(selected), tokens_used, len(all_chunks), query[:80],
    )

    # Clear status before generation starts
    _status("")

    return _format_context(selected)
