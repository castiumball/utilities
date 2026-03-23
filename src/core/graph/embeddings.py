"""
Embedding Model Singleton
=========================

Provides a shared sentence-transformers model for generating text
embeddings. The model is lazy-loaded on first use and kept in memory
as a singleton (~90 MB RAM for all-MiniLM-L6-v2).

Used by:
    - pipeline.py   (ingestion-time embedding generation)
    - retrieval.py  (query-time embedding for vector search)
"""

import logging
from typing import List

from config import settings

logger = logging.getLogger(__name__)

_model = None


def get_model():
    """Lazy-load the sentence-transformers model singleton."""
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        logger.info("Loading embedding model: %s", settings.EMBEDDING_MODEL)
        _model = SentenceTransformer(settings.EMBEDDING_MODEL)
        logger.info("Embedding model loaded")
    return _model


def embed_texts(texts: List[str]) -> List[List[float]]:
    """Batch-encode texts into embedding vectors."""
    if not texts:
        return []
    model = get_model()
    embeddings = model.encode(texts, batch_size=64, show_progress_bar=False)
    return [e.tolist() for e in embeddings]


def embed_query(text: str) -> List[float]:
    """Encode a single query string into an embedding vector."""
    model = get_model()
    return model.encode(text).tolist()
