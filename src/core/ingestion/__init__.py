"""
Ingestion Package

Document ingestion and processing framework for parsing technical documents.

This package provides:
    - A decorator-based parser architecture with auto-discovery
    - Factory pattern for instantiating parsers by name
    - Abstract base class for implementing custom processors
"""

from .factory import ParserFactory
from .registry import list_parsers, register_parser

__all__ = [
    "ParserFactory",
    "register_parser",
    "list_parser"
]
