"""
Home Blueprint

Serves the home/landing page.

Routes:
    GET /       - Home page
    GET /*      - Static files for home page
"""

import os
from flask import Blueprint, send_from_directory

# ============================================
# Blueprint Setup
# ============================================

# Pathto home static files
STATIC_DIR = os.path.join(os.path.dirname(__file__), "..", "web", "home")

home_blueprint = Blueprint(
    "home",
    __name__,
    url_prefix="",
    static_folder=STATIC_DIR,
    static_url_path="/home"
)

# ============================================
# Routes
# ============================================

@home_blueprint.route("/")
def index():
    """Serve the home page."""
    return send_from_directory(STATIC_DIR, "index.html")
