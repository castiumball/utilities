"""
SDD (Software Design Document) Parser
======================================

Splits STARS CSCI SDD documents into chunks for graph RAG ingestion.

Chunking strategy:
    - One chunk per numbered section (e.g. 3.1.1.1)
    - Tables and figures are separate chunks
    - Each chunk has a parent_section link for graph edges
    - Boilerplate headers/footers are stripped

Figure handling:
    When a figures_dir is provided, figure images are extracted from the
    PDF using PyMuPDF's get_images() and saved to disk.  Each figure
    chunk's image_path field contains the filename (e.g. "figure_5.png")
    which the API serves via /api/documents/<hash>/figures/<filename>.
    If figures_dir is None, figure chunks are caption-only graph nodes.

Pipeline (called from SDDParser.parse):
    1. _strip_boilerplate      Remove headers/footers
    2. _parse_chunks            Split markdown into section/table/figure chunks
    3. _map_chunk_pages         Map each chunk to PDF page numbers
    4. _recover_vector_figures  Rasterise garbled vector-art diagrams
    5. _scrub_section_chunks    Remove pipe/table remnants from sections
    6. _normalize_content       Clean up whitespace in section text
    7. _extract_figure_images   Save raster images from the PDF

Key helpers:
    _classify_line             Determine if a line is a heading/table/figure
    _find_page_figure_captions Find "Figure N:" captions in raw PDF text
    _compute_figure_bounds     Bounding box for vector drawings above a caption
    _rasterize_figure_region   Render a page region to PNG
    _clean_garbled_from_sections  Scrub garbled vector-art from a section
"""

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import fitz  # PyMuPDF

from core.ingestion.registry import register_parser
from core.ingestion.parsers.base import Parser

logger = logging.getLogger(__name__)


# ============================================
# Constants
# ============================================

BOILERPLATE_PATTERNS = [
    re.compile(r"(?i)export[\s\-]*control"),
    re.compile(r"(?i)cage\s*code\s*\d+"),
    re.compile(r"(?i)CGH\d+.*FS-\d+"),
    # Tolerate bold markers, Unicode dashes, and any formatting between words
    re.compile(r"(?i)CSCI.{1,10}SDD.{1,10}Rev(ision)?"),
    re.compile(r"^\s*\d{1,4}\s*$"),
    re.compile(r"^-{3,}\s*$"),
    re.compile(r"(?i)raytheon\s+(company|proprietary)"),
    re.compile(r"(?i)RTX\s+Corporation"),
    # Broadened: don't require "restrictions" on the same line
    re.compile(r"(?i)use\s+or\s+disclosure\s+of\s+this\s+(information|data)"),
    re.compile(r"(?i)restrictions\s+on\s+the\s+title\s+page"),
    # EAR export-control warning fragments (footer may wrap across lines)
    re.compile(r"(?i)\(EAR\)\s*WARNING"),
    re.compile(r"(?i)Export\s+Administration\s+Regulations"),
    re.compile(r"(?i)15\s*C\.?\s*F\.?\s*R\.?\s*Sections?\s*730"),
    re.compile(r"(?i)severe\s+criminal\s+penalties"),
    re.compile(r"(?i)non-U\.?S\.?\s+persons"),
    re.compile(r"(?i)subject\s+to.*Export\s+Admin"),
    # Date formats: "22 November 2017" or "November 22, 2017"
    re.compile(
        r"(?i)^\*{0,2}"
        r"(?:\d{1,2}\s+)?"  # optional leading day
        r"(january|february|march|april|may|june|july|august|september|october|november|december)"
        r"\s+\d{1,4},?\s*\d{0,4}"
        r"\*{0,2}$"
    ),
]

# Multi-line boilerplate blocks that span line breaks in the raw markdown.
# Matched with re.DOTALL so `.` crosses newlines.  Char limits prevent
# runaway matches across pages.
MULTILINE_BOILERPLATE = [
    # EAR export-control warning (appears as footer on every page)
    re.compile(
        r"(?i)\(EAR\)\s*WARNING\b.{0,600}?criminal\s+penalties\.?",
        re.DOTALL,
    ),
    # Restriction notice footer that wraps across lines
    re.compile(
        r"(?i)Use\s+or\s+disclosure\s+of\s+this\s+(?:information|data)"
        r".{0,300}?title\s+page\s+of\s+this\s*\n?\s*document\.?",
        re.DOTALL,
    ),
]

# Dot-leader pattern for TOC lines like "1.0 SCOPE ................3"
TOC_DOT_LEADER = re.compile(r"\.{4,}")

# Table/figure checked first so "Table 5" isn't misclassified as a section.
# Allow colon or dot or space after the number (e.g. "Table 5: Title" or "Table 5 Title").
TABLE_RE = re.compile(
    r"(?i)^#{0,6}\s*\*{0,2}table\s+(\d[\d\-]*)[:.]\s*(.*?)\*{0,2}\s*$"
)
# Space-only after number: requires bold ** to distinguish from inline
# prose references like "Table 1 shows that..."
TABLE_RE_BOLD = re.compile(
    r"(?i)^#{0,6}\s*\*{2}table\s+(\d[\d\-]*)\s+(.*?)\*{2}\s*$"
)
# Caption embedded in the first cell of a pipe-delimited markdown table
# e.g. "|Table 1 Full Service SAS Subsystem Descriptions|Col2|"
TABLE_RE_PIPE = re.compile(
    r"(?i)^\|\s*table\s+(\d[\d\-]*)\s+(.*?)\s*\|"
)
# Colon/period after number: unambiguously a caption, bold optional
FIGURE_RE = re.compile(
    r"(?i)^#{0,6}\s*\*{0,2}figure\s+(\d[\d\-]*)[:.]\s*(.*?)\*{0,2}\s*$"
)
# Space-only after number: requires bold ** to distinguish from inline
# prose references like "Figure 1 below represents..."
FIGURE_RE_BOLD = re.compile(
    r"(?i)^#{0,6}\s*\*{2}figure\s+(\d[\d\-]*)\s+(.*?)\*{2}\s*$"
)
# Markdown headings: ## 3.1.1 Title  or  ## **1** **SCOPE**
SECTION_RE = re.compile(r"^#{1,6}\s+\*{0,2}(\d+(?:\.\d[\d.]*)?)\*{0,2}\s+(.*)")
# Bold numbered lines: **3.1.1 Title** or **3.1.1** **Title**
# Requires bold markers on the section number to avoid false positives
# from body text that starts with a number (e.g. "41 Operational Sites").
# Allows bare integers (e.g. **1** **SCOPE**) for top-level sections and
# split bold groups where number and title are wrapped separately.
SECTION_ALT = re.compile(r"^\*{2}(\d+(?:\.\d[\d.]*)?)\*{0,2}\s+\*{0,2}(.*?)\*{0,2}\s*$")

# Unnumbered bold subheadings: **Operational Site Redundancy**
# Must be entirely bold, standalone (no numbers), not a table/figure caption,
# and between 2-12 words (typical heading length).
UNNUMBERED_HEADING_RE = re.compile(r"^\*{2}([^*]+)\*{2}\s*$")

DOC_NUMBER_RE = re.compile(r"(?i)(CGH\d+\s*FS-\d+)")

# Pattern for finding figure captions in raw PDF text blocks.
# Group 1 = figure number, group 2 = separator char (colon/period or
# None when only whitespace), group 3 = title text.
FIGURE_CAPTION_PDF = re.compile(r"(?i)figure\s+(\d+)(?:([:.])\s*|\s+)(.*)")

# Minimum area (square points) for a drawing cluster to count as a figure.
# Filters out decorative lines, borders, and tiny ornaments.
# ~45×45 pt ≈ 0.6 × 0.6 in — anything smaller is unlikely to be a diagram.
MIN_DRAWING_CLUSTER_AREA = 2000


# ============================================
# Parser
# ============================================

@register_parser("SDD")
class SDDParser(Parser):
    """Parses STARS CSCI SDD documents into section/table/figure chunks."""

    def parse(
        self,
        doc_path: Path,
        *,
        page_nums: Optional[Tuple[int, int]] = None,
        preprocessed: Optional[Dict[str, Any]] = None,
        figures_dir: Optional[Path] = None,
    ) -> Dict[str, Any]:
        full_markdown = preprocessed.get("markdown", "") if preprocessed else ""
        page_map = preprocessed.get("page_map") if preprocessed else None

        if not full_markdown:
            return {
                "metadata": {
                    "title": "CSCI-1 SDD",
                    "doc_number": None,
                    "pages_processed": 0,
                },
                "chunks": [],
                "summary": _build_summary([]),
                "debug_info": _build_debug(doc_path, preprocessed, full_markdown, page_nums, 0),
            }

        # Always parse the full document so every chunk has correct parent
        # context and page mapping. Then filter to the requested page range.
        cleaned, boilerplate_removed = _strip_boilerplate(full_markdown)
        chunks = _parse_chunks(cleaned)

        sub_chunks = [c for c in chunks if "_sub" in c.get("number", "")]
        if sub_chunks:
            logger.info(
                "Sub-chunks after _parse_chunks: %s",
                [(c["number"], c["title"], len(c["content"])) for c in sub_chunks],
            )

        if page_map:
            _map_chunk_pages(chunks, full_markdown, page_map)

            # Sub-chunks have synthetic numbers that don't exist in the raw
            # markdown, so _map_chunk_pages may fail to locate their heading.
            # Inherit page numbers from the parent section as a fallback.
            chunks_by_number = {c["number"]: c for c in chunks}
            for chunk in chunks:
                if "_sub" in chunk.get("number", "") and chunk.get("page_start") is None:
                    parent = chunks_by_number.get(chunk.get("parent_section"))
                    if parent and parent.get("page_start") is not None:
                        chunk["page_start"] = parent["page_start"]
                        chunk["page_end"] = parent.get("page_end")
                        logger.info(
                            "Inherited page %s for sub-chunk %s from parent %s",
                            parent["page_start"], chunk["number"], parent["number"],
                        )

        # Create figures directory early — needed by both recovery passes
        if figures_dir:
            figures_dir.mkdir(parents=True, exist_ok=True)

        # Recover vector-art figures that pymupdf4llm garbled into
        # markdown tables.  Runs BEFORE normalisation so the <br> / pipe
        # patterns used to identify garbled content are still intact.
        _recover_vector_figures(doc_path, chunks, figures_dir)

        # Unconditional cleanup: strip pipe-delimited content from
        # section chunks, clear content from figure chunks.
        _scrub_section_chunks(chunks)

        # Normalize content whitespace after page mapping (which needs
        # raw content length to estimate chunk boundaries accurately).
        # Skip table/figure chunks — their content is markdown with
        # structural newlines (row separators, pipe-delimited columns)
        # that normalization would destroy.
        for chunk in chunks:
            if chunk["type"] not in ("table", "figure"):
                chunk["content"] = _normalize_content(chunk["content"])

        # Extract raster figure images from the PDF and save to disk
        if figures_dir:
            _extract_figure_images(doc_path, chunks, figures_dir)

        if page_nums:
            start_page, end_page = page_nums
            before_count = len(chunks)
            dropped = [
                c for c in chunks
                if c.get("page_start") is None or not (start_page <= c["page_start"] < end_page)
            ]
            if dropped:
                logger.info(
                    "Page filter (%d-%d) dropping %d chunks: %s",
                    start_page, end_page, len(dropped),
                    [(c["number"], c["title"][:30], c.get("page_start")) for c in dropped],
                )
            chunks = [
                c for c in chunks
                if c.get("page_start") is not None
                and start_page <= c["page_start"] < end_page
            ]

        doc_number = _extract_doc_number(cleaned)
        pages_processed = (page_nums[1] - page_nums[0]) if page_nums else None

        return {
            "metadata": {
                "title": "CSCI-1 SDD",
                "doc_number": doc_number,
                "pages_processed": pages_processed,
            },
            "chunks": chunks,
            "summary": _build_summary(chunks),
            "debug_info": _build_debug(
                doc_path, preprocessed, full_markdown, page_nums, boilerplate_removed
            ),
        }


# ============================================
# Boilerplate Stripping
# ============================================

def _strip_boilerplate(text: str) -> Tuple[str, int]:
    """Remove repeated header/footer lines. Returns (cleaned, lines_removed)."""
    # First pass: remove multi-line boilerplate blocks that span line breaks
    for pat in MULTILINE_BOILERPLATE:
        text = pat.sub("", text)

    # Second pass: remove individual boilerplate lines
    lines = text.splitlines()
    kept = []
    removed = 0
    for line in lines:
        if any(pat.search(line) for pat in BOILERPLATE_PATTERNS):
            removed += 1
        else:
            kept.append(line)
    return "\n".join(kept), removed


# ============================================
# Line Classification
# ============================================

def _classify_line(line: str) -> Optional[Tuple[str, str, str]]:
    """
    Classify a line as a section heading, table caption, or figure caption.

    Returns (type, number, title) or None.
    """
    stripped = line.strip()
    if not stripped:
        return None

    # Skip TOC lines with dot leaders (e.g. "1.0 SCOPE ..............3")
    if TOC_DOT_LEADER.search(stripped):
        return None

    m = TABLE_RE.match(stripped)
    if not m:
        m = TABLE_RE_BOLD.match(stripped)
    if not m:
        m = TABLE_RE_PIPE.match(stripped)
    if m:
        return ("table", m.group(1), m.group(2).strip())

    m = FIGURE_RE.match(stripped)
    if not m:
        m = FIGURE_RE_BOLD.match(stripped)
    if m:
        return ("figure", m.group(1), m.group(2).strip())

    m = SECTION_RE.match(stripped)
    if m:
        return ("section", m.group(1), m.group(2).strip().strip("*").strip())

    m = SECTION_ALT.match(stripped)
    if m:
        return ("section", m.group(1), m.group(2).strip())

    return None


def _is_unnumbered_subheading(
    line: str, boilerplate: Optional[Set[str]] = None
) -> Optional[str]:
    """
    Detect standalone bold lines that are unnumbered subheadings.

    Returns the heading title (stripped of bold markers) or None.

    Guards against false positives:
    - Must be entirely bold (**...**)
    - Must be 2-12 words (typical heading length)
    - Must not start with Figure/Table/Note (those are captions, not headings)
    - Must not contain sentence-ending punctuation (., ;, :) — headings don't
    - Must not be ALL-CAPS (those are numbered section titles, not subheadings)
    - Must not be in the boilerplate set (repeated page headers/footers)
    """
    stripped = line.strip()
    m = UNNUMBERED_HEADING_RE.match(stripped)
    if not m:
        return None

    title = m.group(1).strip()
    if not title:
        return None

    # Word count check — headings are typically 2-12 words
    words = title.split()
    if len(words) < 2 or len(words) > 12:
        return None

    # Reject table/figure/note captions
    first_word = words[0].lower()
    if first_word in ("figure", "table", "note", "notes"):
        return None

    # Reject lines that look like body text (contain punctuation typical
    # of sentences or labels — not headings)
    if any(c in title for c in ".;:"):
        return None

    # Reject lines with digits — headings don't contain numbers
    # (numbered headings are handled by _classify_line, not here)
    if any(c.isdigit() for c in title):
        return None

    # Reject ALL-CAPS text — real subheadings use title case
    # (e.g. "Operational Site Redundancy" not "OPERATIONAL SITE REDUNDANCY")
    alpha_chars = [c for c in title if c.isalpha()]
    if alpha_chars and all(c.isupper() for c in alpha_chars):
        return None

    # Reject known boilerplate (repeated page headers/footers)
    if boilerplate and title in boilerplate:
        return None

    return title


def _find_boilerplate_bold_lines(markdown: str) -> Set[str]:
    """
    Pre-scan markdown to find bold lines that appear 3+ times.

    These are page headers/footers that survived boilerplate stripping
    and should not be treated as subheadings.
    """
    from collections import Counter
    counts: Counter = Counter()
    for line in markdown.splitlines():
        m = UNNUMBERED_HEADING_RE.match(line.strip())
        if m:
            counts[m.group(1).strip()] += 1
    return {title for title, count in counts.items() if count >= 3}


# ============================================
# Chunking
# ============================================

def _parse_chunks(markdown: str) -> List[Dict[str, Any]]:
    """Split markdown into chunks by heading boundaries.

    Detects both numbered headings (via _classify_line) and unnumbered
    bold subheadings (via _is_unnumbered_subheading). Unnumbered
    subheadings create child Section chunks with synthetic numbers
    like "1.2.4_sub1", parented to the current numbered section.
    """
    lines = markdown.splitlines()
    chunks: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None
    # Tracks the most recent *numbered* section (for sub_counter resets
    # and for deriving parent of numbered sub-sections via _derive_parent)
    last_numbered_section: Optional[str] = None
    # Tracks the most recent section of *any* kind (numbered or synthetic)
    # so that figures/tables parent to their nearest subheading
    last_any_section: Optional[str] = None
    # Counter for synthetic sub-numbering within a numbered section
    sub_counter: int = 0
    # Pre-scan for repeated bold lines (boilerplate page headers/footers)
    boilerplate = _find_boilerplate_bold_lines(markdown)

    for line in lines:
        cls = _classify_line(line)
        if cls:
            if current:
                current["content"] = current["content"].strip()
                chunks.append(current)

            chunk_type, number, title = cls
            if chunk_type == "section":
                last_numbered_section = number
                last_any_section = number
                sub_counter = 0  # reset for new numbered section

            current = {
                "type": chunk_type,
                "number": number,
                "title": title,
                "content": "",
                "parent_section": _derive_parent(
                    chunk_type, number, last_any_section
                ),
                "page_start": None,
                "page_end": None,
                "image_path": None,
            }
            continue

        # Check for unnumbered bold subheadings — only split if we're
        # inside a numbered section (last_numbered_section is set)
        if current is not None and last_numbered_section is not None:
            sub_title = _is_unnumbered_subheading(line, boilerplate)
            if sub_title:
                # Save current chunk
                current["content"] = current["content"].strip()
                chunks.append(current)

                # Create new subsection with synthetic number
                sub_counter += 1
                synthetic_num = f"{last_numbered_section}_sub{sub_counter}"
                last_any_section = synthetic_num

                logger.debug(
                    "Unnumbered subheading -> %s '%s' (parent: %s)",
                    synthetic_num, sub_title, last_numbered_section,
                )

                current = {
                    "type": "section",
                    "number": synthetic_num,
                    "title": sub_title,
                    "content": "",
                    "parent_section": last_numbered_section,
                    "page_start": None,
                    "page_end": None,
                    "image_path": None,
                }
                continue

        if current is not None:
            current["content"] += line + "\n"

    if current:
        current["content"] = current["content"].strip()
        chunks.append(current)

    # Filter out bogus sections — e.g. address lines parsed as headings.
    # A real section should have meaningful body content (>= 20 chars).
    # Tables, figures, and synthetic subsections (_sub) are exempt:
    # - Tables/figures carry value through their image_path
    # - Synthetic subsections were intentionally created from bold
    #   subheadings; dropping them would lose content that was already
    #   split out of the parent section (destructive loss)
    MIN_SECTION_CONTENT_LEN = 20
    filtered = []
    for chunk in chunks:
        if chunk["type"] == "section" and "_sub" not in chunk["number"]:
            content = chunk["content"].strip()
            if len(content) < MIN_SECTION_CONTENT_LEN:
                logger.debug(
                    "Dropped thin section %s '%s' (%d chars)",
                    chunk["number"], chunk["title"][:40], len(content),
                )
                continue
        filtered.append(chunk)

    return filtered


def _derive_parent(
    chunk_type: str, number: str, last_section_num: Optional[str]
) -> Optional[str]:
    """Derive the parent section number for a chunk."""
    if chunk_type in ("table", "figure"):
        return last_section_num

    # For sections, trim the last segment: "3.1.1.1" -> "3.1.1"
    parts = number.rstrip(".").split(".")
    if len(parts) <= 1:
        return None
    return ".".join(parts[:-1])


# ============================================
# Page Mapping
# ============================================

def _map_chunk_pages(
    chunks: List[Dict[str, Any]],
    full_markdown: str,
    page_map: List[Dict[str, Any]],
) -> None:
    """Map each chunk to its start/end page numbers using character offsets."""
    if not page_map or not full_markdown:
        return

    valid_pages = [
        entry for entry in page_map
        if isinstance(entry, dict)
        and entry.get("start") is not None
        and entry.get("end") is not None
    ]
    if not valid_pages:
        return

    for chunk in chunks:
        heading_pattern = _build_heading_pattern(chunk)
        match = _find_content_heading(full_markdown, heading_pattern)
        if match is None:
            continue

        content_len = len(chunk["content"]) if chunk["content"] else 0
        chunk_start = match.start()
        chunk_end = match.end() + content_len

        start_page = None
        end_page = None
        for entry in valid_pages:
            page_start_offset = entry["start"]
            page_end_offset = entry["end"]
            page_num = entry.get("page_number")
            if page_num is None:
                continue

            if page_start_offset <= chunk_start < page_end_offset:
                if start_page is None:
                    start_page = page_num
            if page_start_offset < chunk_end <= page_end_offset:
                end_page = page_num
            if page_start_offset >= chunk_start and page_end_offset <= chunk_end:
                if start_page is None:
                    start_page = page_num
                end_page = page_num

        chunk["page_start"] = start_page
        chunk["page_end"] = end_page


def _find_content_heading(
    text: str, pattern: re.Pattern
) -> Optional[re.Match]:
    """Find a heading in the markdown using regex, skipping TOC lines and
    inline references.

    Returns the Match object (for .start()/.end()) or None.
    """
    start = 0
    while True:
        match = pattern.search(text, start)
        if not match:
            return None

        pos = match.start()

        # Get the full line containing this match
        line_start = text.rfind("\n", 0, pos) + 1
        line_end = text.find("\n", match.end())
        if line_end == -1:
            line_end = len(text)
        line = text[line_start:line_end]

        # Skip TOC lines with dot leaders
        if TOC_DOT_LEADER.search(line):
            start = match.end()
            continue

        # The heading must appear at the start of the line (after
        # optional #/*/whitespace markers), not as an inline reference
        prefix = text[line_start:pos]
        stripped_prefix = prefix.replace("#", "").replace("*", "").strip()
        if stripped_prefix:
            # There's real text before the heading � it's an inline reference
            start = match.end()
            continue

        return match


def _build_heading_pattern(chunk: Dict[str, Any]) -> re.Pattern:
    """Build a regex pattern to find a heading in the original markdown.

    Tolerates optional bold markers (**) around the number and title,
    so '1 SCOPE', '**1** **SCOPE**', and '**1 SCOPE**' all match.

    Synthetic sub-chunks (number contains '_sub') have no number in the
    raw markdown — their heading is just ``**Title**`` on its own line.
    """
    num = re.escape(chunk["number"])
    title = re.escape(chunk["title"]) if chunk["title"] else ""

    if chunk["type"] == "table":
        # Match both standalone captions and pipe-embedded captions
        return re.compile(
            rf"(?:\*{{0,2}}|\|)\s*Table\s+{num}(?:[:.]\s*|\s+)\*{{0,2}}\s*\*{{0,2}}{title}"
        )
    if chunk["type"] == "figure":
        return re.compile(
            rf"\*{{0,2}}Figure\s+{num}(?:[:.]\s*|\s+)\*{{0,2}}\s*\*{{0,2}}{title}\*{{0,2}}"
        )

    # Synthetic sub-chunks: match the bold-only title (no number in raw markdown)
    if "_sub" in chunk["number"]:
        return re.compile(rf"^\*{{2}}{title}\*{{2}}\s*$", re.MULTILINE)

    return re.compile(
        rf"\*{{0,2}}{num}\*{{0,2}}\s+\*{{0,2}}{title}\*{{0,2}}"
    )


# ============================================
# Content Normalisation
# ============================================

def _normalize_content(text: str) -> str:
    """Normalize chunk content whitespace for cleaner RAG ingestion.

    PDF-to-markdown converters insert hard line breaks wherever the
    original page width forced a wrap.  These single newlines carry no
    semantic meaning and waste tokens.  Paragraph breaks (2+ consecutive
    newlines) *do* carry meaning and are preserved as exactly ``\\n\\n``.

    Steps:
        1. Strip trailing whitespace from every line so whitespace-only
           lines become truly empty (and therefore act as paragraph
           separators).
        2. Collapse runs of 2+ newlines into a single ``\\n\\n``.
        3. Split on ``\\n\\n``, join lines within each paragraph with a
           space, and reassemble with ``\\n\\n`` between paragraphs.
        4. Collapse any resulting multiple spaces into one.
    """
    # Trailing whitespace on a line is never meaningful
    text = re.sub(r"[ \t]+$", "", text, flags=re.MULTILINE)

    # Normalise paragraph breaks
    text = re.sub(r"\n{2,}", "\n\n", text)

    # Split on paragraph breaks, clean up each paragraph
    paragraphs = text.split("\n\n")
    cleaned = []
    for para in paragraphs:
        para = para.replace("\n", " ")      # line wraps → spaces
        para = re.sub(r" {2,}", " ", para)  # collapse multiple spaces
        para = para.strip()
        if para:
            cleaned.append(para)

    return "\n\n".join(cleaned)


# ============================================
# Figure Extraction
# ============================================

def _extract_figure_images(
    doc_path: Path,
    chunks: List[Dict[str, Any]],
    figures_dir: Path,
) -> None:
    """Extract figure images from the PDF and save to disk.

    Groups figure chunks by page, then assigns images to chunks using
    vertical position on the page so that multiple figures on the same
    page each get the correct image.

    Sets ``chunk["image_path"]`` to the filename (e.g. ``figure_5.png``)
    so the API can serve it later.
    """
    figure_chunks = [
        c for c in chunks
        if c["type"] == "figure" and c.get("page_start") and not c.get("image_path")
    ]
    if not figure_chunks:
        return

    try:
        doc = fitz.open(doc_path)
    except Exception as exc:
        logger.warning("Could not open PDF for figure extraction: %s", exc)
        return

    try:
        # Group figure chunks by their page so we handle multi-figure
        # pages correctly instead of giving every chunk the same image.
        page_to_chunks: Dict[int, List[Dict[str, Any]]] = {}
        for chunk in figure_chunks:
            page_idx = chunk["page_start"] - 1  # 1-indexed → 0-indexed
            page_to_chunks.setdefault(page_idx, []).append(chunk)

        for page_idx, page_chunks in page_to_chunks.items():
            if page_idx < 0 or page_idx >= len(doc):
                continue

            page = doc[page_idx]
            page_images = _collect_page_images(doc, page)

            if not page_images:
                continue

            if len(page_chunks) == 1:
                # Single figure on this page — pick the largest image
                best = max(page_images, key=lambda img: img["size"])
                _save_figure(best, page_chunks[0], figures_dir)
            else:
                # Multiple figures — match by vertical position.
                # Sort both chunks and images top-to-bottom, then zip.
                # Chunks are already in document order (which is top-to-bottom
                # within a page), so we sort only the images by y-position.
                sorted_images = sorted(page_images, key=lambda img: img["y"])

                # Filter out tiny images (logos/icons) — keep only images
                # larger than 10% of the biggest image's byte count
                max_size = max(img["size"] for img in sorted_images)
                significant = [img for img in sorted_images if img["size"] > max_size * 0.1]

                for i, chunk in enumerate(page_chunks):
                    if i < len(significant):
                        _save_figure(significant[i], chunk, figures_dir)

    finally:
        doc.close()


def _collect_page_images(
    doc: fitz.Document, page: fitz.Page
) -> List[Dict[str, Any]]:
    """Collect all images on a page with their vertical position and bytes.

    Returns a list of dicts with keys: xref, y, size, image, ext.
    Skips images that can't be extracted or are trivially small (< 1 KB).
    """
    results = []
    for img_info in page.get_images(full=True):
        xref = img_info[0]
        try:
            extracted = doc.extract_image(xref)
        except Exception:
            continue
        if not extracted or "image" not in extracted:
            continue

        img_bytes = extracted["image"]
        if len(img_bytes) < 1024:
            # Skip tiny images (icons, bullets, decorations)
            continue

        # Find the image's vertical position on the page by looking up
        # where this xref appears in the page's display list.
        y_pos = _get_image_y_position(page, xref)

        results.append({
            "xref": xref,
            "y": y_pos,
            "size": len(img_bytes),
            "image": img_bytes,
            "ext": extracted.get("ext", "png"),
        })

    return results


def _get_image_y_position(page: fitz.Page, xref: int) -> float:
    """Return the top y-coordinate of an image on a page.

    Searches the page's image list for the matching xref and returns
    the top edge of its bounding box.  Falls back to 0.0 if not found.
    """
    for img in page.get_image_info():
        if img.get("xref") == xref:
            bbox = img.get("bbox")
            if bbox:
                return bbox[1]  # top-y coordinate
    return 0.0


def _save_figure(
    image_data: Dict[str, Any],
    chunk: Dict[str, Any],
    figures_dir: Path,
) -> None:
    """Write an image to disk and set the chunk's image_path."""
    ext = image_data["ext"]
    filename = f"figure_{chunk['number']}.{ext}"
    save_path = figures_dir / filename

    with open(save_path, "wb") as f:
        f.write(image_data["image"])

    chunk["image_path"] = filename
    logger.debug(
        "Saved figure %s → %s (%d bytes)",
        chunk["number"], filename, image_data["size"],
    )


# ============================================
# Vector Figure Recovery
# ============================================

def _recover_vector_figures(
    doc_path: Path,
    chunks: List[Dict[str, Any]],
    figures_dir: Optional[Path],
) -> None:
    """Detect vector-art figures missed by markdown parsing and patch chunks.

    pymupdf4llm cannot convert vector drawings to markdown — it produces
    garbled tables with ``<br>`` tags from the text labels inside the art.
    This function bypasses the markdown entirely:

    **Pass 1 — caption-based (strict):**

    1. Scans each PDF page for "Figure N:" text captions via
       ``page.get_text("dict")``.
    2. Compares against existing figure chunks to find missed figures.
    3. For each missed figure, confirms vector art is present using
       ``page.get_drawings()``, rasterises the region with
       ``page.get_pixmap(clip=...)``, creates a proper figure chunk,
       and scrubs the garbled content from whichever section chunk
       absorbed it.
    4. For figures already chunked but missing an image (vector art
       with a clean caption), just rasterises and sets ``image_path``.

    **Pass 2 — rasterise markdown-detected figures:**

    Only processes figures already found by the markdown chunker (bold
    ``**Figure N ...**`` captions).  Uses ``page.search_for()`` to
    locate the caption on the PDF page (font-flag-agnostic), then
    applies tiered rasterisation:

    * *Tier 1:* tight clip via ``_compute_figure_bounds()`` (best).
    * *Tier 2:* page-top to just below the caption line (always works).

    **Garbled-content cleanup** is handled separately by
    ``_scrub_section_chunks()`` after this function returns — it is
    fully decoupled from figure detection.
    """
    known_figures: Dict[str, Dict[str, Any]] = {
        c["number"]: c for c in chunks if c["type"] == "figure"
    }

    try:
        doc = fitz.open(doc_path)
    except Exception as exc:
        logger.warning("Could not open PDF for vector figure recovery: %s", exc)
        return

    try:
        for page_idx in range(len(doc)):
            page = doc[page_idx]
            page_num = page_idx + 1  # 1-indexed

            captions = _find_page_figure_captions(page)
            if not captions:
                continue

            for fig_num, fig_title, caption_rect in captions:
                existing = known_figures.get(fig_num)
                if existing and existing.get("image_path"):
                    continue  # already detected with image — nothing to do

                # Confirm vector art is present on this page
                try:
                    drawings = page.get_drawings()
                except Exception:
                    continue
                if not drawings:
                    continue

                figure_rect = _compute_figure_bounds(
                    drawings, caption_rect, page.rect
                )
                if figure_rect is None:
                    continue

                # Use full page width to avoid horizontal clipping
                figure_rect.x0 = page.rect.x0
                figure_rect.x1 = page.rect.x1

                # Rasterise the figure region
                image_path = None
                if figures_dir:
                    image_path = _rasterize_figure_region(
                        page, figure_rect, fig_num, figures_dir
                    )

                if existing:
                    # Chunk exists (clean caption in markdown) but had
                    # no raster image — just attach the rasterised one.
                    if image_path:
                        existing["image_path"] = image_path
                    # Backfill page mapping when _map_chunk_pages couldn't
                    # locate the heading (e.g. space-only separator not
                    # matched by _build_heading_pattern).
                    if not existing.get("page_start"):
                        existing["page_start"] = page_num
                        existing["page_end"] = page_num
                    # Still scrub garbled vector-art from section chunks:
                    # the markdown parser created a figure chunk from the
                    # caption, but the garbled table ABOVE the caption was
                    # already absorbed into the previous section chunk.
                    _clean_garbled_from_sections(chunks, existing)
                else:
                    # Brand-new figure missed by markdown parsing.
                    new_chunk: Dict[str, Any] = {
                        "type": "figure",
                        "number": fig_num,
                        "title": fig_title,
                        "content": f"Figure {fig_num}: {fig_title}",
                        "parent_section": None,
                        "page_start": page_num,
                        "page_end": page_num,
                        "image_path": image_path,
                    }
                    _clean_garbled_from_sections(chunks, new_chunk)
                    _insert_figure_chunk(chunks, new_chunk)
                    known_figures[fig_num] = new_chunk

                    logger.debug(
                        "Recovered vector figure %s on page %d",
                        fig_num, page_num,
                    )

        # ── Pass 2: rasterise markdown-detected figures ─────────
        # Only processes figures already in known_figures (from bold
        # **Figure N ...** captions the markdown chunker found).
        # No PDF text scanning — avoids over-detecting inline refs.
        # Tiered rasterisation:
        #   Tier 1: tight clip via _compute_figure_bounds
        #   Tier 2: page-top to just below caption (always works)
        for fig_num, fig_chunk in list(known_figures.items()):
            if fig_chunk.get("image_path"):
                continue

            # Find which page the figure is on
            fig_page = fig_chunk.get("page_start")
            if fig_page is not None:
                pages_to_check = [fig_page - 1]  # 0-indexed
            else:
                # page_start missing — scan all pages
                pages_to_check = range(len(doc))

            for page_idx in pages_to_check:
                if page_idx < 0 or page_idx >= len(doc):
                    continue
                page = doc[page_idx]
                page_num = page_idx + 1

                caption_hits = page.search_for(f"Figure {fig_num}")
                if not caption_hits:
                    continue
                caption_rect = caption_hits[-1]

                # Backfill page if it was missing
                if not fig_chunk.get("page_start"):
                    fig_chunk["page_start"] = page_num
                    fig_chunk["page_end"] = page_num

                # Tiered rasterisation — always use full page width
                image_path = None
                if figures_dir:
                    # Tier 1: full width, vertical bounds from drawings
                    try:
                        drawings = page.get_drawings()
                    except Exception:
                        drawings = []

                    if drawings:
                        figure_rect = _compute_figure_bounds(
                            drawings, caption_rect, page.rect,
                        )
                        if figure_rect is not None:
                            # Use full page width to avoid horizontal clipping
                            figure_rect.x0 = page.rect.x0
                            figure_rect.x1 = page.rect.x1
                            image_path = _rasterize_figure_region(
                                page, figure_rect, fig_num, figures_dir,
                            )

                    # Tier 2: full page
                    if image_path is None:
                        image_path = _rasterize_figure_region(
                            page, page.rect, fig_num, figures_dir,
                        )

                if image_path:
                    fig_chunk["image_path"] = image_path

                logger.debug(
                    "Pass 2: rasterised figure %s on page %d",
                    fig_num, page_num,
                )
                break  # found the page, stop searching
    finally:
        doc.close()


def _find_page_figure_captions(
    page: fitz.Page,
) -> List[Tuple[str, str, fitz.Rect]]:
    """Find ``Figure N: Title`` captions on a page using raw PDF text.

    Works directly on PDF text spans rather than markdown, so it sees
    captions even when the surrounding vector art is garbled.

    To avoid false-positives from inline prose references like
    "Figure 1 below represents…", a space-only separator (no colon
    or period) is only accepted when the span containing "Figure" is
    rendered in a **bold** font (PDF flag bit 4).  Captions with a
    colon or period after the number are unambiguous and accepted
    regardless of font weight.

    Returns list of ``(figure_number, title, caption_rect)``.
    """
    results: List[Tuple[str, str, fitz.Rect]] = []
    text_dict = page.get_text("dict")

    for block in text_dict.get("blocks", []):
        if block.get("type") != 0:  # text blocks only
            continue

        # Concatenate all spans and track whether the "Figure" span
        # is bold (PDF font flag bit 4 = 16).
        block_text = ""
        fig_span_is_bold = False
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                span_text = span.get("text", "")
                block_text += span_text + " "
                if re.search(r"(?i)\bfigure\s+\d+", span_text):
                    if span.get("flags", 0) & 16:
                        fig_span_is_bold = True

        match = FIGURE_CAPTION_PDF.search(block_text)
        if not match:
            continue

        has_punct_sep = match.group(2) is not None  # colon or period
        if not has_punct_sep and not fig_span_is_bold:
            continue  # space-only + not bold → inline reference, skip

        fig_num = match.group(1)
        fig_title = match.group(3).strip()
        bbox = block.get("bbox", (0, 0, 0, 0))
        results.append((fig_num, fig_title, fitz.Rect(bbox)))

    return results


def _compute_figure_bounds(
    drawings: List[Dict[str, Any]],
    caption_rect: fitz.Rect,
    page_rect: fitz.Rect,
) -> Optional[fitz.Rect]:
    """Compute the bounding box of a vector figure from its drawings.

    In technical documents the convention is *caption below figure*, so
    we look for drawing clusters **above** the caption.  Individual
    drawing paths are merged into one rectangle; tiny decorations are
    filtered out by ``MIN_DRAWING_CLUSTER_AREA``.

    Returns the combined rect (drawings + caption) with a small margin,
    or ``None`` if no significant drawings are found above the caption.
    """
    caption_bottom = caption_rect.y1
    caption_center_x = (caption_rect.x0 + caption_rect.x1) / 2
    page_width = page_rect.width

    relevant: List[fitz.Rect] = []
    for d in drawings:
        r = fitz.Rect(d["rect"])
        if r.is_empty or r.is_infinite:
            continue
        # Skip tiny decorations
        if r.width * r.height < MIN_DRAWING_CLUSTER_AREA:
            continue
        # Drawing should be above the caption (small tolerance for overlap)
        if r.y0 > caption_bottom + 20:
            continue
        # Keep drawings horizontally near the caption centre
        drawing_cx = (r.x0 + r.x1) / 2
        if abs(drawing_cx - caption_center_x) > page_width * 0.4:
            continue
        relevant.append(r)

    if not relevant:
        return None

    # Union of all relevant drawing rects + caption
    result = relevant[0]
    for r in relevant[1:]:
        result = result | r  # fitz.Rect union
    result = result | caption_rect

    # Small padding so we don't clip lines on the boundary
    pad = 5
    result.x0 = max(0, result.x0 - pad)
    result.y0 = max(0, result.y0 - pad)
    result.x1 = min(page_rect.width, result.x1 + pad)
    result.y1 = min(page_rect.height, result.y1 + pad)

    return result


def _rasterize_figure_region(
    page: fitz.Page,
    clip_rect: fitz.Rect,
    fig_num: str,
    figures_dir: Path,
) -> Optional[str]:
    """Render a rectangular page region to a PNG file.

    Uses ``page.get_pixmap(clip=...)`` which rasterises *everything*
    inside the rectangle — vector paths, text labels, and any embedded
    raster images — into a single bitmap.

    Returns the filename (e.g. ``figure_5.png``) or ``None`` on failure.
    """
    try:
        pixmap = page.get_pixmap(clip=clip_rect, dpi=150)
        filename = f"figure_{fig_num}.png"
        save_path = figures_dir / filename
        pixmap.save(str(save_path))
        logger.debug(
            "Rasterised vector figure %s → %s (%d×%d px)",
            fig_num, filename, pixmap.width, pixmap.height,
        )
        return filename
    except Exception as exc:
        logger.warning("Failed to rasterise figure %s: %s", fig_num, exc)
        return None


def _clean_garbled_from_sections(
    chunks: List[Dict[str, Any]],
    fig_chunk: Dict[str, Any],
) -> None:
    """Scrub garbled vector-art remnants from section chunks.

    When pymupdf4llm encounters vector art it produces markdown tables
    with ``<br>`` tags from the spatially-positioned text labels.  This
    function locates the section chunk that absorbed the garbled content
    (by matching page ranges) and removes:

    * The figure caption line (``Figure N: ...``)
    * Any lines containing ``<br>`` tags
    * Orphaned markdown table separator rows (``|---|---|``)
    * Resulting excess blank lines
    """
    fig_page = fig_chunk.get("page_start")
    fig_num = fig_chunk["number"]
    if fig_page is None:
        return

    for chunk in chunks:
        if chunk["type"] != "section":
            continue

        cs = chunk.get("page_start")
        ce = chunk.get("page_end")
        if cs is None or ce is None:
            continue
        if not (cs <= fig_page <= ce):
            continue

        content = chunk["content"]

        # Remove bold-wrapped figure caption lines only — NOT inline
        # prose references like "Figure 1 below represents..."
        content = re.sub(
            rf"(?im)^\s*(?:#{{0,6}}\s+)?\*\*\s*Figure\s+{re.escape(fig_num)}\b[^\n]*?\*\*\s*$",
            "", content,
        )
        # Remove lines containing <br> tags (garbled vector-art text)
        content = re.sub(
            r"(?im)^.*<br\s*/?>.*$", "", content,
        )
        # Remove orphaned markdown table separators
        content = re.sub(
            r"(?m)^\s*\|[\s|:\-]+\|\s*$", "", content,
        )
        # Remove orphaned markdown table rows that only contain
        # short label fragments (< 40 chars total, typical of
        # vector-art text labels arranged into pipe-delimited rows)
        content = re.sub(
            r"(?m)^\|(?:[^|\n]{0,40}\|)+\s*$", "", content,
        )
        # Collapse excess blank lines left behind
        content = re.sub(r"\n{3,}", "\n\n", content)

        chunk["content"] = content.strip()


def _scrub_section_chunks(chunks: List[Dict[str, Any]]) -> None:
    """Unconditional cleanup pass — runs independently of figure detection.

    * **Section chunks:** removes all pipe-delimited lines (garbled
      vector-art remnants AND any tables that should have been their
      own chunk).  Also removes lines containing ``<br>`` tags.
    * **Figure chunks:** clears the ``content`` field — figure chunks
      carry only ``image_path``, ``number``, and ``title``.
    """
    for chunk in chunks:
        if chunk["type"] == "section":
            content = chunk["content"]
            # Remove any line that starts with a pipe (table / garbled art)
            content = re.sub(r"(?m)^\|.*$", "", content)
            # Remove lines containing <br> tags (garbled vector-art text)
            content = re.sub(r"(?im)^.*<br\s*/?>.*$", "", content)
            # Collapse excess blank lines left behind
            content = re.sub(r"\n{3,}", "\n\n", content)
            chunk["content"] = content.strip()
        elif chunk["type"] == "figure":
            chunk["content"] = ""


def _insert_figure_chunk(
    chunks: List[Dict[str, Any]],
    fig_chunk: Dict[str, Any],
) -> None:
    """Insert a recovered figure chunk at the correct position.

    Finds the last chunk whose ``page_start`` is at or before the
    figure's page and inserts immediately after it.  Also sets
    ``parent_section`` to the most recent section heading.
    """
    fig_page = fig_chunk.get("page_start", 0)
    insert_idx = len(chunks)  # fallback: append
    last_section_num: Optional[str] = None

    for i, chunk in enumerate(chunks):
        cs = chunk.get("page_start")
        if cs is not None and cs <= fig_page:
            insert_idx = i + 1
            if chunk["type"] == "section":
                last_section_num = chunk["number"]

    fig_chunk["parent_section"] = last_section_num
    chunks.insert(insert_idx, fig_chunk)


# ============================================
# Helpers
# ============================================

def _get_markdown_slice(
    preprocessed: Dict[str, Any],
    page_nums: Optional[Tuple[int, int]],
) -> Optional[str]:
    """Extract markdown for a specific page range."""
    markdown = preprocessed.get("markdown")
    if not isinstance(markdown, str):
        return None

    if not page_nums:
        return markdown

    page_map = preprocessed.get("page_map")
    if not isinstance(page_map, list):
        return markdown

    start_page, end_page = page_nums
    if start_page is None or end_page is None:
        return markdown

    selected = [
        entry for entry in page_map
        if isinstance(entry, dict)
        and entry.get("start") is not None
        and entry.get("end") is not None
        and start_page <= entry.get("page_number", 0) < end_page
    ]

    if not selected:
        return markdown

    start_offset = min(entry["start"] for entry in selected)
    end_offset = max(entry["end"] for entry in selected)
    return markdown[start_offset:end_offset]


def _extract_doc_number(text: str) -> Optional[str]:
    """Try to find a document number like CGH309191 FS-2."""
    m = DOC_NUMBER_RE.search(text[:2000])
    return m.group(1).strip() if m else None


def _build_summary(chunks: List[Dict[str, Any]]) -> Dict[str, int]:
    return {
        "total_chunks": len(chunks),
        "sections": sum(1 for c in chunks if c["type"] == "section"),
        "tables": sum(1 for c in chunks if c["type"] == "table"),
        "figures": sum(1 for c in chunks if c["type"] == "figure"),
    }


def _build_debug(
    doc_path: Path,
    preprocessed: Optional[Dict[str, Any]],
    markdown: Optional[str],
    page_nums: Optional[Tuple[int, int]],
    boilerplate_removed: int,
) -> Dict[str, Any]:
    return {
        "file_name": doc_path.name,
        "file_size_bytes": doc_path.stat().st_size,
        "preprocessed_used": bool(preprocessed),
        "markdown_chars": len(markdown) if markdown else 0,
        "page_range": page_nums,
        "boilerplate_lines_removed": boilerplate_removed,
    }
