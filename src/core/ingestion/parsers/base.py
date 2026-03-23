"""
Abstract Parser Base Class

Defines the interface that all document parsers must implement.

All concrete parsers should:
    1. Inherit from Parser
    2. Implement the parse() method
    3. Register with @register_parser decorator
"""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

class Parser(ABC):
    """
    Abstract base class for document parsers.

    Parsers are responsible for extracting structured data from documents.
    Each parser handles a specific document format.

    Subclasses must implement:
        - parse(): Extract data from a document
    """

    @abstractmethod
    def parse(
        self,
        doc_path: Path,
        *,
        page_nums: Optional[Tuple[int, int]] = None,
        figures_dir: Optional[Path] = None,
    ) -> Dict[str, Any]:
        """
        Parse a document and extract structured data.

        Args:
            doc_path: Path to the document to parse.
            page_nums: Optional tuple of (start_page, end_page) to limit parsing.
                       to a specific page range. Note that start_page is inclusive,
                       end_page is exclusive and page numbers are 1-indexed. If None,
                       the entire document should be parsed.
            figures_dir: Optional directory path for saving extracted figure
                         images. If None, figure extraction is skipped.

        Returns:
            Dictionary containing the extracted data. The exact structure depends
            on the parser and therefore the layout of the document.

        Raises:
            FileNotFoundError: If the documents do not exist.
            ValueError: If the document is invalid or cannot be parsed.
            Exception: Implementation-specific parsing error.
        """
        ...

