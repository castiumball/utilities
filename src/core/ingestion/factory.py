"""
Parser Factory
==============

Factory class for instantiating and retrieving document parsers by name
without needing to know their classes or import paths.

Usage::

    from ingest.factory import ParserFactory
    names = ParserFactory.available_parsers()
    parser = ParserFactory.get_parser(<name_of_parser>)
    result = parser.parse(<document_path>)

Classes:
    ParserFactory       Factory with get_parser() and available_parsers()
"""

import logging
from typing import List

from .parsers.base import Parser
from .registry import get_parser_class, list_parsers, discover_parsers

# ============================================
# Configuration
# ============================================

# For debugging
logger = logging.getLogger(__name__)

# Auto-discover all parsers when this module is imported.
# This populates the registry with all available parsers.
discover_parsers()

class ParserFactory:
    """
    Factory for creating parser instances with a registered name.

    Parsers are automatically discovered from the ingestion.parsers
    package when this module is imported. Use the @register_parser
    decorator to add new parsers to the registry.
    """

    @staticmethod
    def get_parser(name: str) -> Parser:
        """
        Create and return a parser instance by its registered name.

        Args:
            name: The registered name of the parser.

        Returns:
            A new instance of the requested parser.

        Raises:
            ValueError: If no parser is registered with the given name.
                        The error message includes a list of available parsers.
        """
        parser_class = get_parser_class(name)

        if parser_class:
            return parser_class()
        else:
            raise ValueError(f"No parser registered as '{name}'. Available: {list_parsers()}")

    @staticmethod
    def available_parsers() -> list[str]:
        """
        Get list of all registered parser names.

        Returns:
            List of strings that can be passed to get_parser()
        """
        return list_parsers()
