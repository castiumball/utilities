"""
Chat Package

Conversation persistence and LLM integration for the chat interface.

This package provides:
    - SQLite-backed conversation storage
    - Message history per conversation
    - Conversation CRUD (create, list, rename, delete)
    - LangChain integration for vLLM chat completions
    - Progressive conversation summarization for long-term memory

Example Usage:
    from core.chat import ChatManager
    from core.chat.llm import get_llm, build_messages

    manager = ChatManager()
    llm = get_llm()

    history = manager.get_messages(conversation_id)
    messages = build_messages("You are Polaris.", history)

    for chunk in llm.stream(messages):
        print(chunk.content, end="")
"""

from .manager import ChatManager

__all__ = [
    "ChatManager",
]
