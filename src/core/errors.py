"""
Custom Exceptions
=================

Hierarchy of custom exceptions for standardized error handling
across Polaris. Each exception carries a message, an HTTP-like
status code, and an optional payload for additional context.

Exception Hierarchy:
    PolarisError            Base class for all custom exceptions (500)
    ResourceNotFoundError   Resource (document, file) not found (404)
    ValidationError         Input validation failure (400)
    ProcessingError         Internal processing step failure (500)
    ExtractionError         LLM entity extraction failure (500)
    GraphError              Neo4j graph operation failure (500)
"""

from typing import Any, Dict, Optional

class PolarisError(Exception):
    """Base class for all custom Polaris exceptions."""
    def __init__(self, message: str, code: int = 500, payload: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.payload = payload

    def to_dict(self) -> Dict[str, Any]:
        ret = dict(self.payload or ())
        ret["error"] = self.message
        ret["code"] = self.code
        return ret


class ResourceNotFoundError(PolarisError):
    """Raised when a requested resource (document, file) is not found."""
    def __init__(self, message: str = "Resource not found"):
        super().__init__(message, code=404)


class ValidationError(PolarisError):
    """Raised when input validation fails."""
    def __init__(self, message: str = "Invalid input"):
        super().__init__(message, code=400)


class ProcessingError(PolarisError):
    """Raised when a processing step fails."""
    def __init__(self, message: str = "Processing failed"):
        super().__init__(message, code=500)


class ExtractionError(PolarisError):
    """Raised when entity extraction via LLM fails."""
    def __init__(self, message: str = "Entity extraction failed"):
        super().__init__(message, code=500)


class GraphError(PolarisError):
    """Raised when Neo4j graph operations fail."""
    def __init__(self, message: str = "Graph operation failed"):
        super().__init__(message, code=500)
