from pathlib import Path
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    """
    Application settings use Pydantic.

    Reads from environment variables prefixed with POLARIS_
    """

    model_config = SettingsConfigDict(
        env_prefix="POLARIS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )


    # Core Paths
    # Types need to be annotated in configuration files otherwise pydantic throws an error
    PROJECT_ROOT: Path = Path(__file__).resolve().parents[1]
    DATA_DIR: Path = PROJECT_ROOT / "data"

    # Server
    PORT: int = 7999
    HOST: str = "0.0.0.0"
    DEBUG: bool = True

    # Logging
    LOG_LEVEL: str = "INFO"

    # vLLM (local LLM server)
    VLLM_BASE_URL: str = "http://localhost:8001/v1"
    VLLM_MODEL: str = "meta-llama/Meta-Llama-3.1-8B-Instruct"
    VLLM_TIMEOUT: int = 300
    VLLM_MAX_TOKENS: int = 4096
    VLLM_TEMPERATURE: float = 0.0

    # Chat
    CHAT_MAX_CONCURRENCY: int = 4  # Max concurrent vLLM chat requests (others queue)
    CHAT_SYSTEM_PROMPT: str = (
        "You are Polaris, a helpful and reliable technical assistant specializing in STARS manuals. "
        "Accuracy is your top priority — only state what you can support from your knowledge. "
        "When appropriate, format your responses using markdown: use **bold** for key terms and "
        "important values, bullet lists for steps or multiple items, and headings (##) to organize "
        "longer answers into sections. For short, simple answers, plain text is fine — do not "
        "over-format. Keep responses clear and concise. "
        "IMPORTANT: Never mention 'reference material', 'provided context', 'retrieved sources', "
        "or any internal retrieval mechanism. Speak as if the knowledge is your own. "
        "If you lack information, say 'I don't have detailed information on that' rather than "
        "'the reference material does not contain...'. "
        "You CAN display images. When your knowledge includes a markdown image tag like "
        "![alt](url), you MUST include it verbatim in your response. Never say 'I cannot "
        "display images' — the chat interface renders markdown images. Always include the "
        "image tag exactly as provided."
    )
    CHAT_MAX_TOKENS: int = 1024
    CHAT_TEMPERATURE: float = 0.7
    CHAT_SUMMARY_RECENT_COUNT: int = 10   # Messages to keep verbatim in context
    CHAT_SUMMARY_THRESHOLD: int = 12      # Trigger summarization above this count

    # RAG Retrieval
    RAG_ENABLED: bool = True
    RAG_MAX_CHUNKS: int = 8              # Max chunks to inject into context
    RAG_TOKEN_BUDGET: int = 4500         # Token budget for retrieved context
    RAG_BM25_RESULT_LIMIT: int = 15      # BM25 candidates before re-ranking
    RAG_ENTITY_HOP_DEPTH: int = 1        # Relationship hops from matched entities
    RAG_MIN_SCORE: float = 0.5           # Minimum BM25 score to include
    RAG_VECTOR_RESULT_LIMIT: int = 15    # Vector candidates before fusion
    RAG_VECTOR_MIN_SCORE: float = 0.3    # Cosine similarity threshold
    RAG_RRF_K: int = 60                  # RRF fusion constant

    # Embedding
    EMBEDDING_MODEL: str = "all-MiniLM-L6-v2"
    EMBEDDING_DIMENSION: int = 384

    # Extraction
    EXTRACTION_CONCURRENCY: int = 16
    EXTRACTION_BATCH_SIZE: int = 50

    # Neo4j
    NEO4J_URI: str = "bolt://localhost:7687"
    NEO4J_USER: str = "neo4j"
    NEO4J_PASSWORD: str = "polaris_graph"
    NEO4J_DATABASE: str = "neo4j"
    NEO4J_BATCH_SIZE: int = 500

    # Acronym CSV
    ACRONYM_CSV_PATH: Optional[Path] = None

    # Validation
    VALIDATION_ENABLED: bool = True
    VALIDATION_MIN_DESCRIPTION_LENGTH: int = 10

    # Entity resolution
    FUZZY_MATCH_THRESHOLD: int = 85

settings = Settings()
