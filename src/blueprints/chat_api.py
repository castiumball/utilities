"""
Chat API Blueprint — Conversation & Message Management
=======================================================

All routes are prefixed with ``/api/chat``.

Routes:
    POST   /api/chat/conversations                      create a new conversation
    GET    /api/chat/conversations                      list all conversations
    GET    /api/chat/conversations/<id>                 get conversation with messages
    PATCH  /api/chat/conversations/<id>                 rename a conversation
    DELETE /api/chat/conversations/<id>                 delete a conversation
    POST   /api/chat/conversations/<id>/messages        add a message
    POST   /api/chat/conversations/<id>/completions     stream a chat completion via vLLM
"""

import json
import logging
import threading
import queue
from typing import Optional, Tuple

from flask import Blueprint, Response, jsonify, request

from config import settings
from core.chat import ChatManager
from core.chat.llm import build_messages, generate_title, get_llm, maybe_summarize
from core.chat.retrieval import retrieve_context
from core.errors import ResourceNotFoundError, ValidationError

# ============================================
# Blueprint Setup
# ============================================

chat_api_blueprint = Blueprint("chat_api", __name__, url_prefix="/api/chat")

logger = logging.getLogger(__name__)

_chat_manager: Optional[ChatManager] = None

# ============================================
# vLLM Concurrency Queue
# ============================================

_vllm_semaphore = threading.Semaphore(settings.CHAT_MAX_CONCURRENCY)
_queue_lock = threading.Lock()
_queue_waiting = 0  # number of requests waiting for a semaphore slot


def init_chat_manager(manager: ChatManager) -> None:
    """
    Initialize the chat manager for this blueprint.

    Called from the main application during setup.
    """
    global _chat_manager
    _chat_manager = manager


def get_chat_manager() -> ChatManager:
    """Get the chat manager instance."""
    if _chat_manager is None:
        raise RuntimeError("Chat manager not initialized. Call init_chat_manager first.")
    return _chat_manager


def _get_user_id() -> str:
    """Extract user ID from X-User-ID header. Returns 'anonymous' if missing."""
    return request.headers.get("X-User-ID", "anonymous")


# ============================================
# Conversation Routes
# ============================================

@chat_api_blueprint.route("/conversations", methods=["POST"])
def create_conversation() -> Tuple[Response, int]:
    """Create a new conversation."""
    manager = get_chat_manager()
    data = request.get_json(silent=True) or {}
    title = data.get("title", "New Chat")
    conversation = manager.create_conversation(title=title, user_id=_get_user_id())
    return jsonify({"conversation": conversation}), 201


@chat_api_blueprint.route("/conversations", methods=["GET"])
def list_conversations() -> Tuple[Response, int]:
    """List conversations for the current user."""
    manager = get_chat_manager()
    conversations = manager.list_conversations(user_id=_get_user_id())
    return jsonify({"conversations": conversations}), 200


@chat_api_blueprint.route("/conversations/<conversation_id>", methods=["GET"])
def get_conversation(conversation_id: str) -> Tuple[Response, int]:
    """Get a conversation with all its messages."""
    manager = get_chat_manager()
    conversation = manager.get_conversation(conversation_id)
    if conversation is None:
        raise ResourceNotFoundError("Conversation not found")
    messages = manager.get_messages(conversation_id)
    return jsonify({"conversation": conversation, "messages": messages}), 200


@chat_api_blueprint.route("/conversations/<conversation_id>", methods=["PATCH"])
def rename_conversation(conversation_id: str) -> Tuple[Response, int]:
    """Rename a conversation."""
    manager = get_chat_manager()
    data = request.get_json(silent=True) or {}
    title = data.get("title")
    if not title or not title.strip():
        raise ValidationError("Title is required")
    if not manager.rename_conversation(conversation_id, title.strip()):
        raise ResourceNotFoundError("Conversation not found")
    conversation = manager.get_conversation(conversation_id)
    return jsonify({"conversation": conversation}), 200


@chat_api_blueprint.route("/conversations/<conversation_id>", methods=["DELETE"])
def delete_conversation(conversation_id: str) -> Tuple[Response, int]:
    """Delete a conversation and all its messages."""
    manager = get_chat_manager()
    if not manager.delete_conversation(conversation_id):
        raise ResourceNotFoundError("Conversation not found")
    return jsonify({"message": "Conversation deleted"}), 200


# ============================================
# Message Routes
# ============================================

@chat_api_blueprint.route("/conversations/<conversation_id>/messages", methods=["POST"])
def add_message(conversation_id: str) -> Tuple[Response, int]:
    """Add a message to a conversation."""
    manager = get_chat_manager()

    # Verify conversation exists
    if manager.get_conversation(conversation_id) is None:
        raise ResourceNotFoundError("Conversation not found")

    data = request.get_json(silent=True) or {}
    role = data.get("role")
    content = data.get("content")

    if role not in ("user", "bot"):
        raise ValidationError("Role must be 'user' or 'bot'")
    if not content or not content.strip():
        raise ValidationError("Content is required")

    message = manager.add_message(conversation_id, role, content.strip())
    return jsonify({"message": message}), 201


# ============================================
# Chat Completion (Streaming via vLLM)
# ============================================

@chat_api_blueprint.route("/conversations/<conversation_id>/completions", methods=["POST"])
def stream_completion(conversation_id: str):
    """Stream a chat completion from vLLM via LangChain for the given conversation."""
    manager = get_chat_manager()

    conversation = manager.get_conversation(conversation_id)
    if conversation is None:
        raise ResourceNotFoundError("Conversation not found")

    data = request.get_json(silent=True) or {}
    user_message = data.get("message", "").strip()
    if not user_message:
        raise ValidationError("Message is required")

    # Check if this is the first exchange (needs LLM title generation)
    needs_title = conversation.get("title") == "New Chat"

    # Save user message
    manager.add_message(conversation_id, "user", user_message)

    # Load conversation state (before generator, so it's available)
    history = manager.get_messages(conversation_id)
    summary_state = manager.get_summary_state(conversation_id)
    llm = get_llm()

    def generate():
        global _queue_waiting
        accumulated = []

        # -- Concurrency queue: tell user their position, then wait for a slot --
        acquired = _vllm_semaphore.acquire(blocking=False)
        if not acquired:
            with _queue_lock:
                _queue_waiting += 1
                position = _queue_waiting
            yield f"data: {json.dumps({'queue_position': position})}\n\n"
            logger.info("Chat request queued at position %d", position)
            _vllm_semaphore.acquire()  # block until a slot opens
            with _queue_lock:
                _queue_waiting -= 1
            yield f"data: {json.dumps({'queue_position': 0})}\n\n"

        try:
            # --- Begin vLLM slot (semaphore held) ---

            # RAG retrieval in a background thread so status events stream in real-time
            retrieved_context = None
            if settings.RAG_ENABLED:
                event_q = queue.Queue()
                retrieval_result = [None]  # mutable container for thread result

                def _run_retrieval():
                    try:
                        retrieval_result[0] = retrieve_context(
                            user_message, llm=llm,
                            status_callback=lambda msg: event_q.put(("status", msg)),
                            reasoning_callback=lambda label, detail, desc="": event_q.put(
                                ("reasoning", label, detail, desc)
                            ),
                        )
                    except Exception:
                        logger.exception("RAG retrieval failed, continuing without context")
                    finally:
                        event_q.put(None)  # sentinel: retrieval done

                thread = threading.Thread(target=_run_retrieval, daemon=True)
                thread.start()

                # Yield status and reasoning events as they arrive
                while True:
                    event = event_q.get()
                    if event is None:
                        break  # retrieval finished
                    if event[0] == "status":
                        yield f"data: {json.dumps({'status': event[1]})}\n\n"
                    elif event[0] == "reasoning":
                        step = {'label': event[1], 'detail': event[2]}
                        if len(event) > 3 and event[3]:
                            step['description'] = event[3]
                        yield f"data: {json.dumps({'reasoning_step': step})}\n\n"

                thread.join()
                retrieved_context = retrieval_result[0]

            # Build LangChain messages with retrieved context
            lc_messages = build_messages(
                settings.CHAT_SYSTEM_PROMPT, history, summary_state["summary"],
                retrieved_context=retrieved_context,
            )

            try:
                for chunk in llm.stream(lc_messages):
                    token = chunk.content
                    if token:
                        accumulated.append(token)
                        yield f"data: {json.dumps({'token': token})}\n\n"

            except Exception as exc:
                logger.exception("Error during chat completion")
                yield f"data: {json.dumps({'error': str(exc)})}\n\n"
            finally:
                # Save the accumulated bot response
                full_response = "".join(accumulated)
                if full_response:
                    manager.add_message(conversation_id, "bot", full_response)

                # Signal frontend to re-enable input before summarization
                yield "data: [DONE]\n\n"

                # Post-stream tasks (run after [DONE], input already re-enabled)
                if full_response:
                    # Generate title for first exchange
                    if needs_title:
                        try:
                            title = generate_title(llm, user_message, full_response)
                            manager.rename_conversation(conversation_id, title)
                            yield f"data: {json.dumps({'title': title})}\n\n"
                        except Exception:
                            logger.exception("Failed to generate conversation title")

                    # Summarize if needed
                    try:
                        updated_history = manager.get_messages(conversation_id)
                        result = maybe_summarize(
                            llm, updated_history,
                            summary_state["summary"],
                            summary_state["summary_up_to"],
                        )
                        if result:
                            yield f"data: {json.dumps({'status': 'Compressing conversation...'})}\n\n"
                            manager.update_summary(
                                conversation_id, result["summary"], result["up_to"]
                            )
                            yield f"data: {json.dumps({'status': ''})}\n\n"
                    except Exception:
                        logger.exception("Failed to update conversation summary")
        finally:
            # --- Release vLLM slot so the next queued request can proceed ---
            _vllm_semaphore.release()

    return Response(generate(), mimetype="text/event-stream")
