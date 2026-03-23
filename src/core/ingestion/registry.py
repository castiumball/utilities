"""
Parser Registry
===============

Manages registration and discovery of document parsers.
The registry uses a dictionary to map parser names to classes.
Parsers are discovered automatically when the factory module is imported.

Registration:
    register_parser     Decorator to register a parser class by name

Registry Access:
    get_parser_class    Retrieve a parser class by its registered name
    list_parsers        Get a list of all registered parser names

Discovery:
    discover_parsers    Auto-discover and import all parser modules
"""

import logging
import importlib
import pkgutil
from typing import Dict, List, Optional, Type

from .parsers.base import Parser

# ============================================
# Configuration
# ============================================

logger = logging.getLogger(__name__)

# Private registry mapping parser names to their classes
_PARSER_REGISTRY: Dict[str, Type[Parser]] = {}

# ============================================
# Registration
# ============================================

def register_parser(name: str):
    """
    Decorator to register a parser class with a unique name.

    Args:
        name: Unique identifier for the parser. This name will be
              used to retrieve the parser via ParserFactory.

    Returns:
        Decorator function that registers the class and returns it unchanged.

    Raises:
        ValueError: If a parser with the same name is already registered.

    Example:
        @register_parser("My_Parser")
        class MyParser(Parser):
            def parse(self, doc_path, *, page_nums=None):
                pass
    """
    def registration(parser_class: Type[Parser]) -> Type[Parser]:
        if name in _PARSER_REGISTRY:
            existing_class = _PARSER_REGISTRY[name]
            raise ValueError(
                f"Cannot register '{parser_class.__name__}' as '{name}': "
                f"name already registered by '{existing_class.__name__}'"
            )
        _PARSER_REGISTRY[name] = parser_class
        #logger.debug(f"Registered parser: '{name}' -> {parser_class}")

        return parser_class 

    return registration


# ============================================
# Registry Access
# ============================================

def get_parser_class(name: str) -> Optional[Type[Parser]]:
    """
    Retrieve a parser class by its registered name.

    Args:
        name: The registered name of the parser.

    Returns:
        The parser class if found, None otherwise.
    """
    return _PARSER_REGISTRY.get(name)


def list_parsers() -> List[str]:
    """
    Get a list of all registered parser names.

    Returns:
        List of parser names that can be used with ParserFactory.
    """
    return list(_PARSER_REGISTRY.keys())


def discover_parsers(package_name: str = "core.ingestion.parsers") -> None:
    """
    Auto-discover and import all parser modules in a package.

    Walks through all submodules of the specified package and imports them by
    triggering the @register_parser decorators, thus populating the registry.

    This function is called automatically when the ParserFactory loads, so you
    typically don't need to call it manually.

    Args:
        package_name: Fully qualified name of the package to discover and import.
                      Defaults to 'ingestion.parsers'.
    """
    try:
        package = importlib.import_module(package_name)
    except ImportError as error:
        logger.error(f"Could not import package '{package_name}': {error}")
        return

    if hasattr(package, "__path__"):  # Ensure you don't try walking through a file (only packages have __path__)

        # Walk through all submodules recursively
        packages = pkgutil.walk_packages(package.__path__, package.__name__ + ".")
        for _, module_name, _ in packages:
            #logger.info(f"{module_name}, {is_pkg}")
            try:
                importlib.import_module(module_name)
                #logger.debug(f"Discovered module: {module_name}")
            except ImportError as error:
                # Log but continue
                logger.warning(f"Could not import module '{module_name}': {error}")
