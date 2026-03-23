"""
Polaris Server

Flask application that serves multiple web interfaces and provides
a shared API for document management and ingestion.

Web App:
    /           - Home page
    /ingest     - Ingestion web application
    /chat       - Chat interface

API Enpoints (all under /api):
    GET /api/parsers                - List available parsers
    GET /api/documents              - List all documents with metadata
    POST /api/document/upload       - Upload a document (detect duplicates)
    GET /api/documents/<hash>/file  - Serve the raw document
    DELETE /api/documents/<hash>    - Delete a document
    POST /api/parse                 - Parse a document with a specified parser

Local Usage:
    # Install package
    pip install -e  # TODO seems to be broken

    # Run a server
    python3 server.py

    Then open localhost:7999 in your browser (port may change once version 1 is released)
"""

import logging
import os
import sys

from flask import Flask, jsonify

# ==========================================
# Path Configuration
# ==========================================
# Add src module to python path so we can import.
# TODO This allows running without 'pip install -e'
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.join(ROOT_DIR, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

# core module
from core.documents import DocumentManager
from core.chat import ChatManager
from core.ingestion.factory import ParserFactory
from core.errors import PolarisError  # Custom errors specific to Polaris
from config import settings

# Import blueprints
from blueprints import api_blueprint, chat_api_blueprint, chat_blueprint, graph_blueprint, home_blueprint, ingest_blueprint
from blueprints.api import init_document_manager  # TODO should this be here?
from blueprints.chat_api import init_chat_manager
from blueprints.graph import init_graph_document_manager


# ==========================================
# Logging Configuration
# ==========================================

logging.basicConfig(
    level=settings.LOG_LEVEL,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("polaris")


# ==========================================
# Flask Application Factory
# ==========================================

def create_app() -> Flask:
    """
    Create and configure the Flask application.

    Uses the application factory pattern for better testing and flexibility.

    Returns:
        Configured Flask application instance.
    """
    app = Flask(__name__)

    # Configure from settings
    app.config["DEBUG"] = settings.DEBUG

    # Register Error Handlers
    @app.errorhandler(PolarisError)
    def handle_polaris_errors(error):
        return jsonify(error.to_dict()), error.code

    # to ensure consistent JSON error messages
    @app.errorhandler(404)
    def handle_404(error):
        return jsonify({"error": "Not found"}), 404

    @app.errorhandler(500)
    def handler_500(error):
        return jsonify({"error": "Internal server error"}), 500

    # Initialize document manager
    # We pass the data directory from settings
    document_manager = DocumentManager(data_dir=settings.DATA_DIR)
    init_document_manager(document_manager)
    init_graph_document_manager(document_manager)

    # Initialize chat manager
    chat_manager = ChatManager(data_dir=settings.DATA_DIR)
    init_chat_manager(chat_manager)

    # Store reference for access in startup message
    app.document_manager = document_manager

    # Register blueprints
    # Order does matter because more specific routes should come first
    app.register_blueprint(graph_blueprint)     # /api/graph/* (most specific first)
    app.register_blueprint(chat_api_blueprint)  # /api/chat/*
    app.register_blueprint(api_blueprint)       # /api/*
    app.register_blueprint(ingest_blueprint)    # /ingest/*
    app.register_blueprint(chat_blueprint)      # /chat/*
    app.register_blueprint(home_blueprint)      # /* (catch-all, must be last)

    return app


# ==========================================
# Application Entry Point
# ==========================================

def main() -> None:
    """
    Start the Flask development server.
    """
    app = create_app()
    stats = app.document_manager.get_stats()

    print(f"Polaris Server starting on port {settings.PORT}")

    app.run(host=settings.HOST, port=settings.PORT)


if __name__ == "__main__":
    main()