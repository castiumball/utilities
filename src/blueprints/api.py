"""
API Blueprint — Document Management & Parsing
==============================================

All routes are prefixed with ``/api``.

Routes:
    GET  /api/parsers                         list available parsers
    GET  /api/documents                       list all files with metadata
    POST /api/documents/upload                upload a file (deduplicates)
    GET  /api/documents/<hash>                single document metadata
    GET  /api/documents/<hash>/file           serve the raw PDF
    GET  /api/documents/<hash>/figures/<fn>   serve an extracted figure image
    POST /api/documents/<hash>/pin            pin a document globally
    POST /api/documents/<hash>/unpin          unpin a document
    GET  /api/documents/<hash>/flags          flagged pages for a document
    POST /api/documents/<hash>/pages/N/flag   flag a page
    DEL  /api/documents/<hash>/pages/N/flag   unflag a page
    DEL  /api/documents/<hash>                delete a document
    POST /api/documents/<hash>/save-chunks    parse full doc → save to disk
    POST /api/parse                           parse (from library or upload)
    POST /api/markdown                        get markdown (optionally sliced)

Internal helpers:
    _parse_from_library / _parse_from_upload    dispatch parse by source
    _markdown_from_library / _markdown_from_upload   same for markdown
    _run_parser              shared parser execution
    _ensure_preprocessed     lazy PDF → markdown preprocessing
    _load_preprocessed       load cached preprocessed JSON
    _get_page_nums_from_*    extract page range from request
    _normalize_page_range    validate & coerce page numbers
    _slice_markdown          extract a page range from markdown
"""

import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from flask import Blueprint, Response, jsonify, request, send_file

from core.documents import DocumentManager
from core.ingestion.factory import ParserFactory
from core.ingestion.preprocess import preprocess_pdf_to_markdown
from core.errors import ResourceNotFoundError, ValidationError, ProcessingError

# ============================================
# Blueprint Setup
# ============================================

api_blueprint = Blueprint("api", __name__, url_prefix="/api")

logger = logging.getLogger(__name__)

# Allowed file extensions for upload
ALLOWED_EXTENSIONS = {".pdf"}

# Document manager instance (initialized when blueprint is registered)
# TODO is there a point in not having this?
_document_manager: Optional[DocumentManager] = None

def init_document_manager(manager: DocumentManager) -> None:
    """
    Initialize the document manager for this blueprint.

    Called from the main application during setup
    """

    # TODO need to explain this
    # TODO why is _document_manager private and being assined to a non-private variable?
    # TODO will this be a problem if multiple run this?
    global _document_manager
    _document_manager = manager


def get_document_manager() -> DocumentManager:
    """Get the document manager instance."""
    if _document_manager is None:
        raise RuntimeError("Document manager not initialized. Call init_document_manager first.")
    return _document_manager

# ============================================
# Parser Endpoints
# ============================================

@api_blueprint.route("/parsers", methods=["GET"])
def get_available_parsers() -> Response:
    """
    Return list of available document parsers.

    Returns:
        JSON response with list of parser names.
    """
    parsers: List[str] = ParserFactory.available_parsers()
    return jsonify({"parsers": parsers})


# ============================================
# Document Endpoints
# ============================================

@api_blueprint.route("/documents", methods=["GET"])
def list_documents() -> Response:
    """
    List all documents with their metadata.

    Query Parameters:
        status (optional): Filter by status ("uploaded", "preprocessed", "parsed", "entity_extracted", "graph_staged", "graph_ready")

    Returns:
        JSON response with list of document metadata and stats.
    """
    manager = get_document_manager()
    status_filter = request.args.get("status")

    documents = manager.list_documents(status=status_filter)
    stats = manager.get_stats()

    return jsonify({
        "documents": documents,
        "stats": stats,
    })


@api_blueprint.route("/documents/<doc_hash>/pin", methods=["POST"])
def pin_document(doc_hash: str) -> Tuple[Response, int]:
    manager = get_document_manager()
    updated = manager.set_pinned(doc_hash, True)
    if not updated:
        raise ResourceNotFoundError("Document not found")

    return jsonify({
        "message": "Document pinned",
        "hash": doc_hash
    }), 200


@api_blueprint.route("/documents/<doc_hash>/unpin", methods=["POST"])
def unpin_document(doc_hash: str) -> Tuple[Response, int]:
    manager = get_document_manager()
    updated = manager.set_pinned(doc_hash, False)
    if not updated:
        raise ResourceNotFoundError("Document not found")

    return jsonify({
        "message": "Document unpinned",
        "hash": doc_hash
    }), 200


@api_blueprint.route("/documents/<doc_hash>/progress", methods=["PUT"])
def set_document_progress(doc_hash: str) -> Tuple[Response, int]:
    """Set ingestion progress percentage (0-100) for a document."""
    manager = get_document_manager()
    data = request.get_json(silent=True) or {}
    progress = int(data.get("progress", 0))
    updated = manager.set_progress(doc_hash, progress)
    if not updated:
        raise ResourceNotFoundError("Document not found")

    return jsonify({
        "message": "Progress updated",
        "hash": doc_hash,
        "progress": max(0, min(100, progress))
    }), 200


@api_blueprint.route("/documents/<doc_hash>/flags", methods=["GET"])
def get_document_flags(doc_hash: str) -> Tuple[Response, int]:
    """Get flagged pages for a document."""
    manager = get_document_manager()
    if manager.get_document(doc_hash) is None:
        raise ResourceNotFoundError("Document not found")
    flagged_pages = manager.get_flagged_pages(doc_hash)
    return jsonify({"hash": doc_hash, "flagged_pages": flagged_pages}), 200


@api_blueprint.route("/documents/<doc_hash>/pages/<int:page_number>/flag", methods=["POST"])
def flag_page(doc_hash: str, page_number: int) -> Tuple[Response, int]:
    """Flag a page as problematic."""
    manager = get_document_manager()
    if manager.get_document(doc_hash) is None:
        raise ResourceNotFoundError("Document not found")
    if page_number < 1:
        raise ValidationError("Invalid page number")
    note = None
    if request.is_json:
        note = request.json.get("note") or None
    manager.flag_page(doc_hash, page_number, note=note)
    return jsonify({"message": "Page flagged", "hash": doc_hash, "page": page_number}), 200


@api_blueprint.route("/documents/<doc_hash>/pages/<int:page_number>/flag", methods=["DELETE"])
def unflag_page(doc_hash: str, page_number: int) -> Tuple[Response, int]:
    """Remove a page flag."""
    manager = get_document_manager()
    if manager.get_document(doc_hash) is None:
        raise ResourceNotFoundError("Document not found")
    unflagged = manager.unflag_page(doc_hash, page_number)
    if not unflagged:
        raise ResourceNotFoundError("Flag not found")
    return jsonify({"message": "Page unflagged", "hash": doc_hash, "page": page_number}), 200


@api_blueprint.route("/documents/upload", methods=["POST"])
def upload_document() -> Tuple[Response, int]:
    """
    Upload a file to the document library.

    Uses hash-based deduplication. If the same document already exists,
    returns the existing document info without storing a duplicate.

    Query Parameters:
        file: The file to upload

    Returns:
        JSON response with document info and whether it was newly added.
    """
    manager = get_document_manager()

    # Validate request
    if "file" not in request.files:
        raise ValidationError("No file provided")

    uploaded_file = request.files["file"]

    if uploaded_file.filename == "":
        raise ValidationError("No file selected")

    # Check file extension
    # TODO is the .lower() necessary?
    file_extension = Path(uploaded_file.filename).suffix.lower()
    if file_extension not in ALLOWED_EXTENSIONS:
        raise ValidationError(f"File extension: {file_extension} not in approved list: {ALLOWED_EXTENSIONS}")

    # TODO do I need a try except block?
    try:
        doc_hash, is_new = manager.add_document(
            uploaded_file.stream,  # TODO Explain this
            uploaded_file.filename
        )

        document = manager.get_document(doc_hash)

        response_data = {
            "hash": doc_hash,
            "is_new": is_new,
            "document": document,
        }

        if not is_new:
            response_data["message"] = "Document already exists"

        status_code = 201 if is_new else 200
        return jsonify(response_data), status_code

    except Exception as error:
        logger.exception("Error uploading document")
        raise ProcessingError(f"Upload failed: {error}")  # TODO does this need a str(errror)


@api_blueprint.route("/documents/<doc_hash>", methods = ["GET"])
# TODO Explain why does this one not have Query Parameters, instead it has regular argument parameters
def get_document(doc_hash: str) -> Tuple[Response, int]:
    """
    Get metadata for a single document by hash.

    Args:
        doc_hash: truncated hash of file to search for

    Returns:
        The desired document if it exists

    Raises:
        ResourceNotFoundError if it can't find the document
    """
    manager = get_document_manager()
    document = manager.get_document(doc_hash)

    if document is None:
        raise ResourceNotFoundError("Document not found")

    return jsonify({"document": document}), 200


@api_blueprint.route("/documents/<doc_hash>/file", methods=["GET"])
def get_document_file(doc_hash: str) -> Union[Response, Tuple[Response, int]]:
    """
    Serve the raw file for a document.
    """
    manager = get_document_manager()

    document = manager.get_document(doc_hash)
    if document is None:
        raise ResourceNotFoundError("Document not found")

    file_path = manager.get_document_path(doc_hash, "uploaded")

    if not file_path.exists():
        logger.error(f"File could not be found at {file_path}")
        raise ResourceNotFoundError("File not found")

    download_name = (
        document.get("original_filename")
        or document.get("name")
        or f"{doc_hash}.pdf"
    )

    return send_file(
        file_path,
        mimetype="application/pdf",  # TODO maybe explain this
        as_attachment=False,
        download_name=download_name
    )


@api_blueprint.route("/documents/<doc_hash>/figures/<filename>", methods=["GET"])
def get_figure_image(doc_hash: str, filename: str) -> Response:
    """
    Serve an extracted figure image for a document.

    The filename is stored in each figure chunk's ``image_path`` field
    (e.g. ``figure_5.png``).
    """
    manager = get_document_manager()

    if manager.get_document(doc_hash) is None:
        raise ResourceNotFoundError("Document not found")

    figures_dir = manager.get_figures_dir(doc_hash)
    file_path = figures_dir / filename

    if not file_path.exists():
        raise ResourceNotFoundError("Figure not found")

    return send_file(file_path)


@api_blueprint.route("/documents/<doc_hash>", methods=["DELETE"])
def delete_document(doc_hash: str) -> Tuple[Response, int]:
    """
    Delete a document and all its associated files.
    """
    manager = get_document_manager()
    deleted = manager.delete_document(doc_hash)
    
    if not deleted:
        raise ResourceNotFoundError('Document not found')
    
    return jsonify({'message': 'Document deleted', 'hash': doc_hash}), 200


# ============================================
# Parsing Endpoints
# ============================================

@api_blueprint.route("/documents/<doc_hash>/save-chunks", methods=["POST"])
def save_chunks(doc_hash: str) -> Tuple[Response, int]:
    """
    Parse the full document and save the result to data/parsed/{hash}.json.

    This persists the parse output so the graph pipeline can consume it.
    Unlike the /parse endpoint (which returns results for UI display and
    supports page ranges), this always parses the entire document.

    Request JSON:
        { "parser": "<parser_name>" }

    Response JSON:
        { "success": true, "chunks_saved": <int>, "hash": "<hash>" }
    """
    manager = get_document_manager()

    data = request.get_json(silent=True) or {}
    parser_name = data.get("parser")
    if not parser_name:
        raise ValidationError("No parser specified")

    document = manager.get_document(doc_hash)
    if document is None:
        raise ResourceNotFoundError("Document not found in library")

    file_path = manager.get_document_path(doc_hash, "uploaded")
    if not file_path.exists():
        raise ResourceNotFoundError("File not found")

    preprocessed = _ensure_preprocessed(manager, doc_hash, file_path)
    figures_dir = manager.get_figures_dir(doc_hash)
    result = _run_parser(
        file_path, parser_name,
        preprocessed=preprocessed,
        page_nums=None,
        figures_dir=figures_dir,
    )

    if result is None:
        result = {}

    # Persist to data/parsed/{hash}.json
    parsed_path = manager.get_document_path(doc_hash, "parsed")
    parsed_path.parent.mkdir(parents=True, exist_ok=True)
    with open(parsed_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    manager.update_status(doc_hash, "parsed")

    chunks_saved = len(result.get("chunks", []))
    logger.info("Saved %d chunks for %s to %s", chunks_saved, doc_hash, parsed_path)

    return jsonify({
        "success": True,
        "chunks_saved": chunks_saved,
        "hash": doc_hash,
    }), 200


@api_blueprint.route("/parse", methods=["POST"])
def parse_document() -> Tuple[Response, int]:
    """
    Parse a PDF document with the specified parser.
    """
    if request.is_json:
        return _parse_from_library(request.json)
    else:
        return _parse_from_upload(request)


@api_blueprint.route("/markdown", methods=["POST"])
def get_markdown() -> Tuple[Response, int]:
    """
    Return markdown for a document (optionally sliced by page range).
    """
    if request.is_json:
        return _markdown_from_library(request.json)
    else:
        return _markdown_from_upload(request)


# ============================================
# Parse / Markdown Dispatch
# ============================================

def _parse_from_library(data: Dict[str, Any]) -> Tuple[Response, int]:
    """Parse a document from the library by hash."""
    manager = get_document_manager()

    doc_hash = data.get("hash")
    parser_name = data.get("parser")

    if not doc_hash:
        raise ValidationError("No document hash provided")

    if not parser_name:
        raise ValidationError("No parser specified")

    document = manager.get_document(doc_hash)
    if document is None:
        raise ResourceNotFoundError("Document not found in library")

    file_path = manager.get_document_path(doc_hash, "uploaded")
    if not file_path.exists():
        raise ResourceNotFoundError("File not found")

    try:
        page_nums = _get_page_nums_from_payload(data)
        preprocessed = _ensure_preprocessed(manager, doc_hash, file_path)
        figures_dir = manager.get_figures_dir(doc_hash)
        result = _run_parser(
            file_path, parser_name,
            preprocessed=preprocessed,
            page_nums=page_nums,
            figures_dir=figures_dir,
        )
        manager.update_status(doc_hash, "parsed")

        return jsonify({
            "success": True,
            "parser": parser_name,
            "hash": doc_hash,
            "result": result,
        }), 200

    except ValueError as error:
       logger.warning(f"Validation error: {error}")
       raise ValidationError(str(error))

    except Exception as error:
       logger.exception(f"Parse error for parser {parser_name}")
       raise ProcessingError(f"Parse error: {str(error)}")
        

def _parse_from_upload(req) -> Tuple[Response, int]:
    """Directly parse an uploaded file."""
    manager = get_document_manager()

    _validate_parse_request(req)

    uploaded_file = req.files["file"]  # TODO maybe standardize .get with [] accesses
    parser_name = req.form.get("parser")

    try:
        doc_hash, is_new = manager.add_document(
            uploaded_file.stream,
            uploaded_file.filename
        )

        file_path = manager.get_document_path(doc_hash, "uploaded")
        page_nums = _get_page_nums_from_form(req.form)  # TODO Explain why there is a from form and from pyaload versions
        preprocessed = _ensure_preprocessed(manager, doc_hash, file_path)
        figures_dir = manager.get_figures_dir(doc_hash)
        result = _run_parser(
            file_path, parser_name,
            preprocessed=preprocessed,
            page_nums=page_nums,
            figures_dir=figures_dir,
        )
        manager.update_status(doc_hash, "parsed")

        return jsonify({
            "success": True,
            "parser": parser_name,
            "hash": doc_hash,
            "result": result,
        }), 200 

    except ValueError as error:
        logger.warning(f"Validation error: {error}")
        raise ValidationError(str(error))

    except Exception as error:
        logger.exception(f"Parse error for parser {parser_name}")
        raise ProcessingError(f"Parse error: {str(error)}")


def _markdown_from_library(data: Dict[str, Any]) -> Tuple[Response, int]:
    manager = get_document_manager()

    doc_hash = data.get("hash")
    if not doc_hash:
        raise ValidationError("No document hash provided")

    document = manager.get_document(doc_hash)
    if document is None:
        raise ResourceNotFoundError("Document not found in library")

    file_path = manager.get_document_path(doc_hash, "uploaded")
    if not file_path.exists():
        raise ResourceNotFoundError("Document file not found")

    page_nums = _get_page_nums_from_payload(data)
    preprocessed = _ensure_preprocessed(manager, doc_hash, file_path)
    markdown = _slice_markdown(preprocessed, page_nums)

    return jsonify({
        "success": True,
        "hash": doc_hash,
        "markdown": markdown,
        "page_start": page_nums[0] if page_nums else None,
        "page_end": page_nums[1] if page_nums else None,
    }), 200 


def _markdown_from_upload(req) -> Tuple[Response, int]:
    manager = get_document_manager()

    if "file" not in req.files:
        raise ValidationError("No file provided")

    uploaded_file = req.files["file"]
    if uploaded_file.filename == "":  # TODO this could be more pythonic
        raise ValidationError("No file selected")

    file_extension = Path(uploaded_file.filename).suffix.lower()
    if file_extension not in ALLOWED_EXTENSIONS:
        raise ValidationError(f"File extension: {file_extension} not in approved list: {ALLOWED_EXTENSIONS}")

    doc_hash, _ = manager.add_document(
        uploaded_file.stream,
        uploaded_file.filename
    )

    file_path = manager.get_document_path(doc_hash, "uploaded")
    page_nums = _get_page_nums_from_form(req.form)
    preprocessed = _ensure_preprocessed(manager, doc_hash, file_path)  # TODO might nmake this a function
    markdown = _slice_markdown(preprocessed, page_nums)

    return jsonify({
        "success": True,
        "hash": doc_hash,
        "markdown": markdown,
        "page_start": page_nums[0] if page_nums else None,
        "page_end": page_nums[1] if page_nums else None,
    }), 200 


# ============================================
# Helper Functions
# ============================================

def _validate_parse_request(req) -> None:
    """Validate the parse request has all required fields."""
    if "file" not in req.files:
        raise ValidationError("No file provided")

    if not req.form.get("parser"):
        raise ValidationError("No parser specified")

    uploaded_file = req.files["file"]

    if uploaded_file.filename == "":
        raise ValidationError("No file selected")

    file_extension = Path(uploaded_file.filename).suffix.lower()
    if file_extension not in ALLOWED_EXTENSIONS:
        raise ValidationError(f"File extension: {file_extension} not in approved list: {ALLOWED_EXTENSIONS}")


def _run_parser(
    file_path: Path,
    parser_name: str,
    *,
    preprocessed: Optional[Dict[str, Any]] = None,
    page_nums: Optional[Tuple[int, int]] = None,
    figures_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Run a parser on a file."""
    parser = ParserFactory.get_parser(parser_name)

    logger.info(f"Parsing {file_path.name} with {parser_name}")
    result = parser.parse(
        file_path,
        page_nums=page_nums,
        preprocessed=preprocessed,
        figures_dir=figures_dir,
    )
    logger.info("Parser completed successfully")

    return result


def _get_page_nums_from_payload(data: Dict[str, Any]) -> Optional[Tuple[int, int]]:
    if not data:
        return None

    if "page_num" in data:
        return _normalize_page_range(data.get("page_num"))

    if "page_start" in data or "page_end" in data:
        return _normalize_page_range(data.get("page_start"), data.get("page_end"))  # TODO might need to explain this

    return None


def _get_page_nums_from_form(form) -> Optional[Tuple[int, int]]:
    if form is None:
        return None

    if form.get("page_num"):
        return _normalize_page_range(form.get("page_num"))

    if form.get("page_start") or form.get("page_end"):
        return _normalize_page_range(form.get("page_start"), form.get("page_end"))

    return None


def _normalize_page_range(page_start, page_end=None) -> Optional[Tuple[int, int]]:
    if page_start is None or page_start == "":  # TODO is an empty string falsy
        return None

    try:
        page_start = int(page_start)
    except (TypeError, ValueError):
        raise ValidationError(f"Invalid page_start value: {page_start}")

    if page_end is None or page_end == "":
        page_end = page_start + 1
    else:
        try:
            page_end = int(page_end)
        except (TypeError, ValueError):
            raise ValidationError(f"Invalid page_end value: {page_end}")

    if page_start < 1 or int(page_end) <= page_start:
        raise ValidationError("Invalid page_end value")

    return (page_start, page_end)


def _slice_markdown(preprocessed: Dict[str, Any], page_nums: Optional[Tuple[int, int]]) -> str:
    markdown = preprocessed.get("markdown")
    if not isinstance(markdown, str):
        return ""

    if not page_nums:
        return markdown

    page_map = preprocessed.get("page_map")
    if not isinstance(page_map, list):
        return markdown

    start_page, end_page = page_nums
    selected = [
        entry for entry in page_map
        if isinstance(entry, dict)
        and entry.get("start") is not None
        and entry.get("end") is not None
        and start_page <= entry.get("page_number", 0) < end_page
    ]

    if not selected:
        return markdown

    # TODO
    start_offset = min(entry["start"] for entry in selected)
    end_offset = max(entry["end"] for entry in selected)
    return markdown[start_offset:end_offset]


# ============================================
# Preprocessing
# ============================================

def _ensure_preprocessed(
    manager: DocumentManager,
    doc_hash: str,
    file_path: Path
) -> Dict[str, Any]:
    preprocessed = _load_preprocessed(manager, doc_hash)
    if preprocessed is not None:
        return preprocessed

    preprocessed = preprocess_pdf_to_markdown(file_path)
    preprocessed_path = manager.get_document_path(doc_hash, "preprocessed")
    if preprocessed_path is None:
        raise ProcessingError("Preprocessed path not available: {preprocessed_path}")

    preprocessed_path.parent.mkdir(parents=True, exist_ok=True)
    with open(preprocessed_path, "w", encoding="utf-8") as file:
        json.dump(preprocessed, file, indent=2, ensure_ascii=True)

    return preprocessed


def _load_preprocessed(manager: DocumentManager, doc_hash: str) -> Optional[Dict[str, Any]]:
    preprocessed_path = manager.get_document_path(doc_hash, "preprocessed")
    if preprocessed_path is None or not preprocessed_path.exists():
        return None

    with open(preprocessed_path, "r", encoding="utf-8") as file:  #TODO would it make since to call it handle instead of file
        data = json.load(file)
        return data
