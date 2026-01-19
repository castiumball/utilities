"""
PDF Parser Server

Flask application that serves the PDF parser web interface and provides
API endpoints for listing available processors and parsing PDF documents.

API Endpoints:
    GET  /              - Serve the web interface
    GET  /processors    - List available document processors
    POST /parse         - Parse a PDF with a specified processor

Usage:
    python server.py

    Then open http://localhost:5000 in your browser.
"""

import logging
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Tuple

from flask import Flask, Response, jsonify, request, send_from_directory
from flask_cors import CORS

# ============================================
# Path Configuration
# ============================================
# Add core module to Python path for processor imports.
# This allows the server to be run from the src/ directory.
CORE_MODULE_PATH = os.path.join(os.path.dirname(__file__), 'core')
sys.path.insert(0, CORE_MODULE_PATH)

from ingestion.factory import ProcessorFactory  # noqa: E402

# ============================================
# Constants
# ============================================

# Directory containing static web files (HTML, CSS, JS)
STATIC_FILES_DIR = os.path.join(os.path.dirname(__file__), 'web', 'parser')

# Server configuration
DEFAULT_PORT = 5000
DEBUG_MODE = True

# Allowed file extensions for upload
ALLOWED_EXTENSIONS = {'.pdf'}

# ============================================
# Logging Configuration
# ============================================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ============================================
# Flask Application Setup
# ============================================

app = Flask(__name__, static_folder=STATIC_FILES_DIR, static_url_path='')
CORS(app)  # Enable cross-origin requests for local development


# ============================================
# Route Handlers
# ============================================

@app.route('/')
def serve_index() -> Response:
    """
    Serve the main web interface.
    
    Returns:
        The index.html file from the static directory.
    """
    return send_from_directory(STATIC_FILES_DIR, 'index.html')


@app.route('/processors', methods=['GET'])
def get_available_processors() -> Response:
    """
    Return list of available document processors.
    
    The processor list is auto-discovered from the ingestion.processors
    package. Each processor registered with @register_processor decorator
    will appear in this list.
    
    Returns:
        JSON response with list of processor names.
        
    Example Response:
        {"processors": ["Reference_Card", "Standard"]}
    """
    processors: List[str] = ProcessorFactory.available_processors()
    return jsonify({'processors': processors})


@app.route('/parse', methods=['POST'])
def parse_document() -> Tuple[Response, int]:
    """
    Parse an uploaded PDF document with the specified processor.
    
    Expects multipart/form-data with:
        - file: The PDF file to parse
        - processor: Name of the processor to use
    
    Returns:
        JSON response with parse result on success, or error message on failure.
        
    Example Success Response:
        {
            "success": true,
            "processor": "Reference_Card",
            "result": {...parsed data...}
        }
        
    Example Error Response:
        {"error": "No file provided"}
    """
    # Validate request has required file
    validation_error = _validate_parse_request(request)
    if validation_error:
        return jsonify({'error': validation_error}), 400
    
    uploaded_file = request.files['file']
    processor_name = request.form.get('processor')
    
    try:
        result = _process_pdf_file(uploaded_file, processor_name)
        return jsonify({
            'success': True,
            'processor': processor_name,
            'result': result,
        }), 200
        
    except ValueError as error:
        # ValueError indicates invalid processor name or validation failure
        logger.warning(f'Validation error: {error}')
        return jsonify({'error': str(error)}), 400
        
    except Exception as error:
        # Catch-all for unexpected errors during parsing
        logger.exception(f'Parse error for processor {processor_name}')
        return jsonify({'error': f'Parse error: {str(error)}'}), 500


# ============================================
# Helper Functions
# ============================================

def _validate_parse_request(req) -> str | None:
    """
    Validate the parse request has all required fields.
    
    Args:
        req: Flask request object
        
    Returns:
        Error message string if validation fails, None if valid.
    """
    if 'file' not in req.files:
        return 'No file provided'
    
    if not req.form.get('processor'):
        return 'No processor specified'
    
    uploaded_file = req.files['file']
    
    if uploaded_file.filename == '':
        return 'No file selected'
    
    # Check file extension
    file_extension = Path(uploaded_file.filename).suffix.lower()
    if file_extension not in ALLOWED_EXTENSIONS:
        return f'File must be a PDF (got {file_extension})'
    
    return None


def _process_pdf_file(uploaded_file, processor_name: str) -> Dict[str, Any]:
    """
    Process a PDF file using the specified processor.
    
    Saves the uploaded file to a temporary location, runs the processor,
    and cleans up the temp file regardless of success or failure.
    
    Args:
        uploaded_file: Flask FileStorage object
        processor_name: Name of the registered processor to use
        
    Returns:
        Dictionary containing the parsed document data.
        
    Raises:
        ValueError: If processor name is not registered.
        Exception: If parsing fails for any reason.
    """
    processor = ProcessorFactory.get_processor(processor_name)
    
    # Save to temp file because processors expect a file path
    with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp_file:
        uploaded_file.save(tmp_file.name)
        temp_path = Path(tmp_file.name)
    
    try:
        logger.info(f'Parsing {uploaded_file.filename} with {processor_name}')
        result = processor.parse(temp_path)
        logger.info(f'Parse completed successfully')
        return result
        
    finally:
        # Always clean up temp file, even if parsing fails
        temp_path.unlink(missing_ok=True)


# ============================================
# Application Entry Point
# ============================================

def main() -> None:
    """
    Start the Flask development server.
    """
    print('=' * 60)
    print('PDF Parser Server')
    print('=' * 60)
    print(f'Available processors: {ProcessorFactory.available_processors()}')
    print(f'Server running on http://localhost:{DEFAULT_PORT}')
    print('=' * 60)
    
    app.run(port=DEFAULT_PORT, debug=DEBUG_MODE)


if __name__ == '__main__':
    main()
