"""
Flask Blueprints

Modular route handlers.

Blueprints:
    - home_blueprint: Home/landing page (/)
    - ingest_blueprint: Ingestion web page (/ingest)
    - chat_blueprint: Chat interface (/chat)
    - chat_api_blueprint: Chat API endpoints (/api/chat)
    - api_blueprint: Shared API endpoints (/api)
    - graph_blueprint: Graph extraction & ingestion (/api/graph)
"""

from .api import api_blueprint
from .chat import chat_blueprint
from .chat_api import chat_api_blueprint
from .graph import graph_blueprint
from .home import home_blueprint
from .ingest import ingest_blueprint

__all__ = [
    "api_blueprint",
    "chat_api_blueprint",
    "chat_blueprint",
    "graph_blueprint",
    "home_blueprint",
    "ingest_blueprint",
]
