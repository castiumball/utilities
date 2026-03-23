"""
LLM Integration Layer
======================

LangChain-based integration with the local vLLM server for chat completions
and conversation memory management.

Functions:
    get_llm              Create a ChatOpenAI instance for the local vLLM server
    build_messages        Build the LLM messages array with summary support
    maybe_summarize       Progressively summarize older messages when threshold exceeded
"""

import logging
from typing import Dict, List, Optional

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from config import settings

logger = logging.getLogger(__name__)


# ============================================
# LLM Factory
# ============================================

def get_llm() -> ChatOpenAI:
    """
    Create a ChatOpenAI instance configured for the local vLLM server.

    vLLM exposes an OpenAI-compatible API, so ChatOpenAI works directly
    by pointing base_url at the vLLM server. No real API key is needed.
    """
    return ChatOpenAI(
        base_url=settings.VLLM_BASE_URL,
        api_key="not-needed",
        model=settings.VLLM_MODEL,
        max_tokens=settings.CHAT_MAX_TOKENS,
        temperature=settings.CHAT_TEMPERATURE,
    )


# ============================================
# Message Building
# ============================================

def build_messages(
    system_prompt: str,
    history: List[Dict],
    summary: Optional[str] = None,
    retrieved_context: Optional[str] = None,
) -> list:
    """
    Build the LangChain messages array for the LLM.

    Structure:
      1. SystemMessage with the base system prompt
      2. SystemMessage with retrieved document context (if any)
      3. SystemMessage with the running summary (if one exists)
      4. The last RECENT_COUNT messages from history, verbatim

    This keeps the context window bounded while preserving both
    long-term context (via summary) and recent detail (verbatim).
    """
    messages = [SystemMessage(content=system_prompt)]

    if retrieved_context:
        messages.append(SystemMessage(content=retrieved_context))

    if summary:
        messages.append(SystemMessage(
            content=f"Summary of earlier conversation:\n{summary}"
        ))

    # Keep only recent messages verbatim
    recent = history[-settings.CHAT_SUMMARY_RECENT_COUNT:]
    for msg in recent:
        if msg["role"] == "user":
            messages.append(HumanMessage(content=msg["content"]))
        else:
            messages.append(AIMessage(content=msg["content"]))

    return messages


# ============================================
# Title Generation
# ============================================

TITLE_PROMPT = (
    "Generate a very short title (3-6 words) for this conversation. "
    "Return ONLY the title, no quotes, no punctuation at the end.\n\n"
    "User: {user_message}\n"
    "Assistant: {bot_response}\n\n"
    "Title:"
)


def generate_title(llm: ChatOpenAI, user_message: str, bot_response: str) -> str:
    """
    Generate a concise conversation title from the first exchange.

    Uses the LLM to produce a 3-6 word title based on the user's
    first message and the bot's response.
    """
    prompt = TITLE_PROMPT.format(
        user_message=user_message[:500],
        bot_response=bot_response[:500],
    )
    response = llm.invoke([HumanMessage(content=prompt)])
    # Clean up: strip quotes, trailing punctuation, limit length
    title = response.content.strip().strip('"\'').strip('.')
    if len(title) > 60:
        title = title[:57] + "..."
    return title or "New Chat"


# ============================================
# Conversation Summarization
# ============================================

SUMMARIZE_PROMPT = (
    "Progressively summarize the conversation so far, incorporating new lines "
    "into the existing summary. Return ONLY the updated summary, nothing else.\n\n"
    "Current summary:\n{existing_summary}\n\n"
    "New lines of conversation:\n{new_lines}\n\n"
    "Updated summary:"
)


def maybe_summarize(
    llm: ChatOpenAI,
    history: List[Dict],
    existing_summary: Optional[str],
    summary_up_to: int = 0,
) -> Optional[dict]:
    """
    If the conversation has grown past the threshold, summarize ONLY
    the newly unsummarized messages outside the recent window.

    Tracks progress via summary_up_to — the count of messages already
    folded into the summary. Only processes messages between summary_up_to
    and the current cutoff, avoiding redundant re-summarization.

    Returns:
        Dict with 'summary' and 'up_to' if summarization occurred, else None.
    """
    if len(history) <= settings.CHAT_SUMMARY_THRESHOLD:
        return None

    # Messages outside the recent window need summarizing
    cutoff = len(history) - settings.CHAT_SUMMARY_RECENT_COUNT
    if cutoff <= 0 or cutoff <= summary_up_to:
        return None  # No new messages to summarize

    # Only summarize the NEW messages (between last summary point and cutoff)
    new_messages = history[summary_up_to:cutoff]
    if not new_messages:
        return None

    lines = []
    for msg in new_messages:
        role_label = "User" if msg["role"] == "user" else "Assistant"
        lines.append(f"{role_label}: {msg['content']}")
    new_lines = "\n".join(lines)

    prompt = SUMMARIZE_PROMPT.format(
        existing_summary=existing_summary or "(No existing summary)",
        new_lines=new_lines,
    )

    logger.info(
        "Summarizing %d new messages (messages %d-%d)",
        len(new_messages), summary_up_to, cutoff
    )

    response = llm.invoke([HumanMessage(content=prompt)])
    return {"summary": response.content, "up_to": cutoff}
