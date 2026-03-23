"""
Chat Blueprint

Serves the chat web interface.

Routes:
    GET /chat           - Chat web interface
    GET /chat/*         - Static files
"""

import os
from flask import Blueprint, send_from_directory

# ============================================#
# Blueprint Setup
# ============================================#

# Oath to chat static files
STATIC_DIR = os.path.join(os.path.dirname(__file__), "..", "web", "chat")

chat_blueprint = Blueprint(
    "chat",
    __name__,
    url_prefix="/chat",
    static_folder=STATIC_DIR,
    static_url_path=""
)

# ============================================#
# Routes
# ============================================#

@chat_blueprint.route("/")
def index():
    """Serve the chat web interface."""
    return send_from_directory(STATIC_DIR, "index.html")
