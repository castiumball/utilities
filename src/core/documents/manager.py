"""
Document Manager
================

Handles document storage, metadata tracking, and ingestion status management.

Documents are stored with hash-based filenames for deduplication purposes.
Metadata (original filename, upload date, processing status) is tracked
in a SQLite database.

Folder Structure:
    data/
    |- documents.db             # SQLite metadata database
    |- uploaded/                # Original uploaded PDFs
    |- preprocessed/            # Preprocessed documents (markdown)
    |- parsed/                  # Parser output (JSON chunks)
    |- entity_extracted/        # Entity extracted chunks
    |- graph_ready/             # Formatted to be put into graph database

Configuration:
    HASH_LENGTH              Truncation length for SHA-256 file hashes
    STATUSES                 Valid document processing statuses

Utilities:
    compute_file_hash        Compute a truncated SHA-256 hash of a file

DocumentManager -- Core document lifecycle manager:

  Initialization & Database:
    __init__                 Set up data directories and database
    _init_directories        Create stage directories if missing
    _init_database           Create database tables and indexes
    _ensure_pinned_column    Migration: add pinned column if absent
    _ensure_note_column      Migration: add note column if absent
    _get_connection          Context manager for SQLite connections

  Document CRUD:
    add_document             Store a document and record its metadata
    get_document             Retrieve metadata for a single document
    list_documents           List documents, optionally filtered by status
    update_status            Advance a document to a new pipeline status
    set_pinned               Pin or unpin a document
    delete_document          Remove a document and all its artifacts

  Page Flags:
    flag_page                Flag a page as problematic
    unflag_page              Remove a page flag
    get_flagged_pages        List flagged pages for a document

  Path Resolution & Stats:
    get_document_path        Resolve the file path for a document at a given stage
    get_figures_dir          Get the per-document figures directory
    document_exists_at_status  Check whether a stage artifact exists on disk
    get_stats                Aggregate document counts by status
"""

import hashlib
import logging
import os
import shutil
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, BinaryIO, Dict, List, Optional, Tuple, Union

from config import settings

# ============================================
# Module Configuration
# ============================================

logger = logging.getLogger(__name__)

# Hash length for filename (first n characters of hash)
# 16 chars = 8 bytes of entropy.
HASH_LENGTH = 16

STATUSES = ("uploaded", "preprocessed", "parsed", "entity_extracted", "graph_staged", "graph_ready")

# ============================================
# Utility Functions
# ============================================

def compute_file_hash(file: Union[BinaryIO, Path, str], length: int = HASH_LENGTH) -> str:
    """
    Compute SHA-256 hash of a file.

    Args:
        file: str/Path to a file or a raw file objects
        length: desired length of output, defaults to HASH_LENGTH

    Returns:
        hash of file, truncated to length
    """

    sha256 = hashlib.sha256()

    if isinstance(file, (str, Path)):
        with open(file, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b''):
                sha256.update(chunk)
    else:
        start_pos = file.tell()
        file.seek(0)
        for chunk in iter(lambda: file.read(8192), b''):
            sha256.update(chunk)
        file.seek(start_pos)

    return sha256.hexdigest()[:length]

# ============================================
# Document Manager Class
# ============================================

class DocumentManager:
    """
    Manages document storage and metadata for ingestion.
    """

    def __init__(self, data_dir: Optional[Union[str, Path]] = None):
        """
        Initialize the document manager.
        """
        if data_dir is None:
            data_dir = settings.DATA_DIR

        self.data_dir = Path(data_dir).resolve()
        self.db_path = self.data_dir / "documents.db"

        # Stage directories
        self.uploaded_dir = self.data_dir / "uploaded"
        self.preprocessed_dir = self.data_dir / "preprocessed"
        self.parsed_dir = self.data_dir / "parsed"
        self.entity_extracted_dir = self.data_dir / "entity_extracted"
        self.graph_staged_dir = self.data_dir / "graph_staged"
        self.graph_ready_dir = self.data_dir / "graph_ready"
        self.figures_dir = self.data_dir / "figures"

        # Ensure directories exist
        self._init_directories()

        # Initialized database
        self._init_database()


    # ============================================
    # Initialization & Database
    # ============================================

    def _init_directories(self) -> None:
        """Create data directories if they don't exist."""
        for directory in [
            self.data_dir,
            self.uploaded_dir,
            self.preprocessed_dir,
            self.parsed_dir,
            self.entity_extracted_dir,
            self.graph_staged_dir,
            self.graph_ready_dir,
            self.figures_dir,
        ]:
            directory.mkdir(parents=True, exist_ok=True)
            logger.debug(f"Ensured directory exists: {directory}")


    def _init_database(self) -> None:
        """Create database tables if they don't exist."""
        with self._get_connection() as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS documents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    hash TEXT UNIQUE NOT NULL,
                    name TEXT NOT NULL,
                    size INTEGER,
                    upload_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    status TEXT DEFAULT 'uploaded',
                    parsed_at TIMESTAMP,
                    entity_extracted_at TIMESTAMP,
                    graph_staged_at TIMESTAMP,
                    graph_ready_at TIMESTAMP,
                    pinned INTEGER DEFAULT 0
                )
            ''')
            conn.execute("CREATE INDEX IF NOT EXISTS idx_status ON documents(status)")
            self._ensure_pinned_column(conn)
            self._ensure_graph_staged_column(conn)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_pinned ON documents(pinned)")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS page_flags (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    doc_hash TEXT NOT NULL,
                    page_number INTEGER NOT NULL,
                    flagged_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(doc_hash, page_number)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_flags_doc ON page_flags(doc_hash)")
            self._ensure_note_column(conn)
            self._ensure_progress_column(conn)
            conn.commit()
            logger.debug("Database initialized")

    # TODO is this needed any more?
    def _ensure_pinned_column(self, conn) -> None:
        cursor = conn.execute("PRAGMA table_info(documents)")
        columns = {row[1] for row in cursor.fetchall()}
        if "pinned" not in columns:
            conns.execute("ALTER TABLE documents ADD COLUMN pinned INTEGER DEFAULT 0")


    def _ensure_graph_staged_column(self, conn) -> None:
        cursor = conn.execute("PRAGMA table_info(documents)")
        columns = {row[1] for row in cursor.fetchall()}
        if "graph_staged_at" not in columns:
            conn.execute("ALTER TABLE documents ADD COLUMN graph_staged_at TIMESTAMP")


    def _ensure_note_column(self, conn) -> None:
        cursor = conn.execute("PRAGMA table_info(page_flags)")
        columns = {row[1] for row in cursor.fetchall()}
        if "note" not in columns:
            conn.execute("ALTER TABLE page_flags ADD COLUMN note TEXT")

    def _ensure_progress_column(self, conn) -> None:
        cursor = conn.execute("PRAGMA table_info(documents)")
        columns = {row[1] for row in cursor.fetchall()}
        if "progress" not in columns:
            conn.execute("ALTER TABLE documents ADD COLUMN progress INTEGER DEFAULT 0")


    @contextmanager
    def _get_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    # ============================================
    # Document Operations
    # ============================================

    def add_document(self, file: Union[BinaryIO, Path, str], name: str) -> Tuple[str, bool]:
        """
        Add a document to storage and database.
        """

        doc_hash = compute_file_hash(file)

        # Check if already exists
        existing = self.get_document(doc_hash)
        if existing:
            logger.info(f"Document already exists: {doc_hash} ({name})")
            return doc_hash, False

        # Determine file size
        if isinstance(file, (str, Path)):
            file_size = Path(file).stat().st_size
        else:
            current_pos = file.tell()
            file.seek(0, 2)  # Seek start to end
            file_size = file.tell()
            file.seek(current_pos)  # Restore position

        # Copy file to raw storage
        dest_path = self.uploaded_dir / f"{doc_hash}.pdf"

        if isinstance(file, (str, Path)):
            shutil.copy2(file, dest_path)
        else:
            file.seek(0)
            with open(dest_path, "wb") as dest:
                shutil.copyfileobj(file, dest)

        # Add to database
        with self._get_connection() as conn:
            conn.execute(
                '''
                INSERT INTO documents (hash, name, size)
                VALUES (?, ?, ?)
                ''',
                (doc_hash, name, file_size)
            )
            conn.commit()

        logger.info(f"Added document: {doc_hash} ({name}), {file_size} bytes")
        return doc_hash, True


    def get_document(self, doc_hash: str) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT d.*, COUNT(f.id) as flagged_pages
                FROM documents d
                LEFT JOIN page_flags f ON d.hash = f.doc_hash
                WHERE d.hash = ?
                GROUP BY d.id
                """,
                (doc_hash,),
            )
            row = cursor.fetchone()

            if row:
                return dict(row)
            return None


    def list_documents(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            if status:
                cursor = conn.execute(
                    """
                    SELECT d.*, COUNT(f.id) AS flagged_pages
                    FROM documents d
                    LEFT JOIN page_flags f ON d.hash = f.doc_hash
                    WHERE d.status = ?
                    GROUP BY d.id
                    ORDER BY d.upload_date DESC
                    """,
                    (status,),
                )
            else:
                cursor = conn.execute(
                    """
                    SELECT d.*, COUNT(f.id) AS flagged_pages
                    FROM documents d
                    LEFT JOIN page_flags f ON d.hash = f.doc_hash
                    GROUP BY d.id
                    ORDER BY d.upload_date DESC
                    """
                )
            return [dict(row) for row in cursor.fetchall()]


    def update_status(self, doc_hash: str, status: str) -> bool:
        if status not in STATUSES:
            raise ValueError(f"Invalid status '{status}'. Must be one of: {STATUSES}")

        timestamp_field = f"{status}_at"

        with self._get_connection() as conn:
            if status in ["parsed", "entity_extracted", "graph_staged", "graph_ready"]:
                cursor = conn.execute(
                    f'''
                    UPDATE documents
                    SET status = ?, {timestamp_field} = ?
                    WHERE hash = ?
                    ''',
                    (status, datetime.now().isoformat(), doc_hash)
                )
            else:
                cursor = conn.execute(
                    'UPDATE documents SET status = ? WHERE hash = ?',
                    (status, doc_hash)
                )

            conn.commit()
            updated = cursor.rowcount > 0

            if updated:
                logger.info(f"Updated document {doc_hash} status to '{status}'")
            else:
                logger.warning(f"Document not found for status update: {doc_hash}")

            return updated


    def set_pinned(self, doc_hash: str, pinned: bool) -> bool:
        with self._get_connection() as conn:
            cursor = conn.execute(
                'UPDATE documents SET pinned = ? WHERE hash = ?',
                (1 if pinned else 0, doc_hash)
            )
            conn.commit()
            updated = cursor.rowcount > 0   # TODO document how this works

        if updated:
            logger.info(f"Updated document {doc_hash} pinned={pinned}")
        else:
            logger.warning(f"Document not found for pin update: {doc_hash}")

        return updated


    def set_progress(self, doc_hash: str, progress: int) -> bool:
        """Set ingestion progress percentage (0-100) for a document."""
        progress = max(0, min(100, progress))
        with self._get_connection() as conn:
            cursor = conn.execute(
                'UPDATE documents SET progress = ? WHERE hash = ?',
                (progress, doc_hash)
            )
            conn.commit()
            updated = cursor.rowcount > 0

        if updated:
            logger.info(f"Updated document {doc_hash} progress={progress}%")
        else:
            logger.warning(f"Document not found for progress update: {doc_hash}")

        return updated

    # ============================================
    # Page Flags
    # ============================================

    def flag_page(self, doc_hash: str, page_number: int, note: Optional[str] = None) -> bool:
        """Flag a page as problematic. Returns True if newly flagged."""
        with self._get_connection() as conn:
            try:
                conn.execute(
                    "INSERT INTO page_flags (doc_hash, page_number, note) VALUES (?, ?, ?)",
                    (doc_hash, page_number, note or None),
                )
                conn.commit()
                return True
            except sqlite3.IntegrityError:
                return False

    def unflag_page(self, doc_hash: str, page_number: int) -> bool:
        """Remove a page flag. Returns True if the flag existed."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "DELETE FROM page_flags WHERE doc_hash = ? AND page_number = ?",
                (doc_hash, page_number),
            )
            conn.commit()
            return cursor.rowcount > 0

    def get_flagged_pages(self, doc_hash: str) -> List[Dict[str, Any]]:
        """Return sorted list of flagged page info for a document."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT page_number, note FROM page_flags WHERE doc_hash = ? ORDER BY page_number",
                (doc_hash,),
            )
            return [{"page_number": row["page_number"], "note": row["note"]} for row in cursor.fetchall()]


    # ============================================
    # Deletion, Path Resolution & Stats
    # ============================================

    def delete_document(self, doc_hash: str) -> bool:
        doc = self.get_document(doc_hash)
        if not doc:
            logger.warning(f"Document not found for deletion: {doc_hash}")
            return False

        doc_name = doc["name"]

        # TODO is this the most efficient way of doing this?
        for status in STATUSES:
            file_path = self.get_document_path(doc_hash, status)
            if file_path and file_path.exists():
                file_path.unlink()
                logger.debug(f"Deleted {status} file: {file_path}")

        # Remove extracted figure images
        figures_dir = self.get_figures_dir(doc_hash)
        if figures_dir.exists():
            shutil.rmtree(figures_dir, ignore_errors=True)
            logger.debug(f"Deleted figures directory: {figures_dir}")

        with self._get_connection() as conn:
            conn.execute('DELETE FROM page_flags WHERE doc_hash = ?', (doc_hash,))
            conn.execute('DELETE FROM documents WHERE hash = ?', (doc_hash,))
            conn.commit()

        logger.info(f"Deleted document: {doc_name} ({doc_hash})")
        return True


    # TODO should this function come earlier? TODO should status be its own type or dataclass?
    # TODO should this handle case sensitivity?
    def get_document_path(self, doc_hash: str, status: str) -> Optional[Path]:
        if status not in STATUSES:
            raise ValueError(f"Invalid status '{status}'. Must be one of: {STATUSES}")

        status_dirs = {
            "uploaded": self.uploaded_dir,
            "parsed": self.parsed_dir,
            "preprocessed": self.preprocessed_dir,
            "entity_extracted": self.entity_extracted_dir,
            "graph_staged": self.graph_staged_dir,
            "graph_ready": self.graph_ready_dir
        }

        # TODO what if we ingest .csv or other file formats?
        # TODO should it be .jsonl?
        extensions = {
            "uploaded": ".pdf",
            "preprocessed": ".json",
            "parsed": ".json",
            "entity_extracted": ".json",
            "graph_staged": ".json",
            "graph_ready": ".json"
        }

        # TODO is storing documents by their hash the best? I guess only for dedpulication
        # TODO is there a better way of storing these documents so that I can navigate into data/ and
        # see which documents are there without knowing their hash? is that even necessary?
        return status_dirs[status] / f"{doc_hash}{extensions[status]}"

    def get_figures_dir(self, doc_hash: str) -> Path:
        """Return the per-document figures directory: data/figures/{doc_hash}/."""
        return self.figures_dir / doc_hash

    def document_exists_at_status(self, doc_hash: str, stage: str) -> bool:
        path = self.get_document_path(doc_hash, stage)
        return path is not None and path.exists()

    def get_stats(self) -> Dict[str, int]:
        with self._get_connection() as conn:
            cursor = conn.execute(
                '''
                SELECT status, COUNT(*) as count
                FROM documents
                GROUP BY status
                '''
            )

            stats = {status: 0 for status in STATUSES}
            for row in cursor.fetchall():
                stats[row["status"]] = row["count"]

            stats["total"] = sum(stats.values())
            return stats
