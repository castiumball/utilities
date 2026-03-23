"""
Ingest Blueprint

Serves the ingestion web interface.

Routes:
    GET /ingest     - Ingestion web interface
    GET /ingest/*   - Static files
"""

import os

from flask import Blueprint, send_from_directory

# ============================================
# Blueprint Setup
# ============================================

# Path to ingestion static files
STATIC_DIR = os.path.join(os.path.dirname(__file__), "..", "web", "ingest")

ingest_blueprint = Blueprint(
    "ingest",
    __name__,
    url_prefix="/ingest",
    static_folder=STATIC_DIR,
    static_url_path=""
)


# ============================================
# Routes
# ============================================

@ingest_blueprint.route("/")
def index():
    """Serve the ingestion web interface."""
    return send_from_directory(STATIC_DIR, "index.html")
