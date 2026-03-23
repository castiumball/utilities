"""
Chat Manager
=============

Handles conversation and message persistence for the chat interface.

Conversations and messages are stored in a SQLite database (chat.db),
separate from the document database to keep concerns isolated.

ChatManager — Core conversation lifecycle manager:

  Initialization & Database:
    __init__              Set up database path and initialize
    _init_database        Create tables and indexes
    _get_connection       Context manager for SQLite connections

  Conversation CRUD:
    create_conversation   Create a new conversation
    list_conversations    List all conversations with preview snippets
    get_conversation      Get a single conversation with its messages
    rename_conversation   Update a conversation's title
    delete_conversation   Delete a conversation and its messages

  Summary (LangChain Memory):
    get_summary_state     Get the summary and how many messages it covers
    update_summary        Update the summary and its coverage count

  Messages:
    add_message           Add a message to a conversation
    get_messages          Get all messages for a conversation
"""

import logging
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, List, Optional, Union

from config import settings

# ============================================
# Module Configuration
# ============================================

logger = logging.getLogger(__name__)


# ============================================
# Chat Manager Class
# ============================================

class ChatManager:
    """
    Manages conversation and message storage for the chat interface.
    """

    def __init__(self, data_dir: Optional[Union[str, Path]] = None):
        """
        Initialize the chat manager.
        """
        if data_dir is None:
            data_dir = settings.DATA_DIR

        self.data_dir = Path(data_dir).resolve()
        self.db_path = self.data_dir / "chat.db"

        # Ensure data directory exists
        self.data_dir.mkdir(parents=True, exist_ok=True)

        # Initialize database
        self._init_database()

    # ============================================
    # Initialization & Database
    # ============================================

    def _init_database(self) -> None:
        """Create database tables if they don't exist."""
        with self._get_connection() as conn:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL DEFAULT 'New Chat',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('user', 'bot')),
                    content TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_messages_conversation "
                "ON messages(conversation_id, created_at)"
            )
            conn.commit()

            # Migration: add columns incrementally
            for col in [
                "summary TEXT DEFAULT NULL",
                "summary_up_to INTEGER DEFAULT 0",
                "user_id TEXT DEFAULT NULL",
            ]:
                try:
                    conn.execute(f"ALTER TABLE conversations ADD COLUMN {col}")
                    conn.commit()
                except sqlite3.OperationalError:
                    pass  # Column already exists

            # Index for user_id filtering
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_conversations_user "
                "ON conversations(user_id)"
            )
            conn.commit()

            logger.debug("Chat database initialized")

    @contextmanager
    def _get_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
        finally:
            conn.close()

    # ============================================
    # Conversation Operations
    # ============================================

    def create_conversation(self, title: str = "New Chat", user_id: Optional[str] = None) -> Dict:
        """
        Create a new conversation.

        Returns:
            Dict with id, title, created_at, updated_at, user_id.
        """
        conversation_id = uuid.uuid4().hex
        with self._get_connection() as conn:
            conn.execute(
                "INSERT INTO conversations (id, title, user_id) VALUES (?, ?, ?)",
                (conversation_id, title, user_id)
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM conversations WHERE id = ?",
                (conversation_id,)
            ).fetchone()

        logger.info(f"Created conversation: {conversation_id} (user={user_id})")
        return dict(row)

    def list_conversations(self, user_id: Optional[str] = None) -> List[Dict]:
        """
        List conversations ordered by most recently updated.

        If user_id is provided, only returns conversations owned by that user.
        Each conversation includes a preview snippet from its latest message.
        """
        with self._get_connection() as conn:
            if user_id:
                rows = conn.execute("""
                    SELECT c.*,
                        (SELECT SUBSTR(m.content, 1, 80)
                         FROM messages m
                         WHERE m.conversation_id = c.id
                         ORDER BY m.created_at DESC
                         LIMIT 1) AS preview
                    FROM conversations c
                    WHERE c.user_id = ?
                    ORDER BY c.updated_at DESC
                """, (user_id,)).fetchall()
            else:
                rows = conn.execute("""
                    SELECT c.*,
                        (SELECT SUBSTR(m.content, 1, 80)
                         FROM messages m
                         WHERE m.conversation_id = c.id
                         ORDER BY m.created_at DESC
                         LIMIT 1) AS preview
                    FROM conversations c
                    ORDER BY c.updated_at DESC
                """).fetchall()

        return [dict(row) for row in rows]

    def get_conversation(self, conversation_id: str) -> Optional[Dict]:
        """
        Get a single conversation's metadata.

        Returns:
            Dict with conversation data, or None if not found.
        """
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM conversations WHERE id = ?",
                (conversation_id,)
            ).fetchone()

        return dict(row) if row else None

    def rename_conversation(self, conversation_id: str, title: str) -> bool:
        """
        Rename a conversation.

        Returns:
            True if the conversation was found and renamed.
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                "UPDATE conversations SET title = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (title, conversation_id)
            )
            conn.commit()

        if cursor.rowcount > 0:
            logger.info(f"Renamed conversation {conversation_id} to '{title}'")
            return True
        return False

    def delete_conversation(self, conversation_id: str) -> bool:
        """
        Delete a conversation and all its messages.

        Returns:
            True if the conversation was found and deleted.
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                "DELETE FROM conversations WHERE id = ?",
                (conversation_id,)
            )
            conn.commit()

        if cursor.rowcount > 0:
            logger.info(f"Deleted conversation: {conversation_id}")
            return True
        return False

    # ============================================
    # Summary Operations (LangChain Memory)
    # ============================================

    def get_summary_state(self, conversation_id: str) -> dict:
        """
        Get the summary state for a conversation.

        Returns:
            Dict with 'summary' (str or None) and 'summary_up_to' (int).
        """
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT summary, summary_up_to FROM conversations WHERE id = ?",
                (conversation_id,)
            ).fetchone()
        if row:
            return {"summary": row["summary"], "summary_up_to": row["summary_up_to"] or 0}
        return {"summary": None, "summary_up_to": 0}

    def update_summary(self, conversation_id: str, summary: str, up_to: int) -> None:
        """Update the running summary and the message count it covers."""
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE conversations SET summary = ?, summary_up_to = ?, "
                "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (summary, up_to, conversation_id)
            )
            conn.commit()
        logger.debug(f"Updated summary for conversation {conversation_id} (up_to={up_to})")

    # ============================================
    # Message Operations
    # ============================================

    def add_message(self, conversation_id: str, role: str, content: str) -> Dict:
        """
        Add a message to a conversation.

        Also updates the conversation's updated_at timestamp.

        Returns:
            Dict with the created message data.
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                "INSERT INTO messages (conversation_id, role, content) VALUES (?, ?, ?)",
                (conversation_id, role, content)
            )
            # Update conversation timestamp
            conn.execute(
                "UPDATE conversations SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (conversation_id,)
            )
            conn.commit()

            row = conn.execute(
                "SELECT * FROM messages WHERE id = ?",
                (cursor.lastrowid,)
            ).fetchone()

        return dict(row)

    def get_messages(self, conversation_id: str) -> List[Dict]:
        """
        Get all messages for a conversation, ordered chronologically.
        """
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM messages WHERE conversation_id = ? ORDER BY created_at ASC",
                (conversation_id,)
            ).fetchall()

        return [dict(row) for row in rows]
