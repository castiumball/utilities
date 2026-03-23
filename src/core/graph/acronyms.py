"""
Acronym Loader
==============

Loads an acronym CSV (columns: Acronym, Expansion) and provides
functions for expanding abbreviations in text before sending to
the LLM. This improves extraction quality since the 8B model sees
full terms instead of ambiguous abbreviations.

The CSV format matches the output of process_acronyms() in
core/ingestion/ingest.py.

Functions:
    load_acronyms       Load acronym mappings from a CSV file
    expand_acronyms     Expand known acronyms in text (first occurrence only)
"""

import csv
import logging
import re
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)


def load_acronyms(csv_path: Optional[Path]) -> Dict[str, str]:
    """
    Load acronym mappings from a CSV file.

    CSV must have columns: Acronym, Expansion (header row required).

    Returns:
        Dictionary mapping uppercase acronyms to their expansions.
        Example: {"STARS": "Standard Terminal Automation Replacement System"}
    """
    if csv_path is None or not Path(csv_path).exists():
        logger.info("No acronym CSV provided or file not found; skipping")
        return {}

    acronyms = {}
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            abbr = row.get("Acronym", "").strip().upper()
            expansion = row.get("Expansion", "").strip()
            if abbr and expansion:
                acronyms[abbr] = expansion

    logger.info("Loaded %d acronyms from %s", len(acronyms), csv_path)
    return acronyms


def expand_acronyms(text: str, acronym_map: Dict[str, str]) -> str:
    """
    Expand known acronyms in text by appending the full form on first occurrence.

    Replaces "STARS" with "STARS (Standard Terminal Automation Replacement System)"
    on the FIRST occurrence only. Uses word-boundary matching to avoid partial
    replacements (e.g., won't match "STARSYSTEM" when looking for "STARS").
    """
    if not acronym_map:
        return text

    expanded = set()

    def replacer(match):
        word = match.group(0).upper()
        if word in acronym_map and word not in expanded:
            expanded.add(word)
            return f"{match.group(0)} ({acronym_map[word]})"
        return match.group(0)

    # Build a single regex matching any known acronym as a whole word.
    # Sort by length descending so longer acronyms match first.
    sorted_acronyms = sorted(acronym_map.keys(), key=len, reverse=True)
    pattern = re.compile(
        r"\b(" + "|".join(re.escape(a) for a in sorted_acronyms) + r")\b",
        re.IGNORECASE,
    )

    return pattern.sub(replacer, text)
