"""
Ingestion Utilities
===================

Shared helpers for PDF ingestion, text extraction, and visual debugging.

Header/Footer Detection:
    get_header_footer_bounds     Identify recurring header/footer regions by sampling pages

Page Sampling:
    get_random_pages             Select a random subset of unique pages from a document

Span Extraction:
    get_typed_spans              Yield text spans or image blocks from a list of pages
    get_sorted_spans             Extract and sort all text spans top-to-bottom, left-to-right
    is_span_inside_bbox          Check whether a span falls within any of the given bounding boxes

Text Formatting:
    get_text_attributes          Decode a PyMuPDF flags integer into human-readable attributes
    apply_text_attributes_md     Wrap span text in Markdown formatting based on its attributes
    get_color_name               Map an integer RGB color to the nearest named color

Debugging:
    pretty_print                 Print a hierarchical representation of a page's text structure

CLI:
    parse_args                   Parse command-line arguments for the debugging entry point
    run_debugging                Orchestrate a debugging session from parsed arguments
    main                        Entry point for command-line invocation
"""

import sys
import argparse
import logging
import time
import random
import re
import os
import csv

from tqdm import tqdm
from pathlib import Path
from typing import TypedDict, Tuple, List, Union, Generator, Dict
from collections import defaultdict

import pymupdf as fitz
import pdfplumber
import multiprocessing

from PIL import Image

# ============================================
# Logger Setup
# ============================================

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
handler = logging.StreamHandler(sys.stdout)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)
logger.addHandler(handler)

# ============================================
# Compiled Regex Patterns
# ============================================

SECTION_NUMBER = re.compile(r"^\d+(?:\.\d+)*$")
DESCRIPTION_HEADER = re.compile(r"\bDescription\b")
TECH_MAN_CONTENT_TITLE = re.compile(r"CONTENT TITLE\s*\n(.*?)\n\s*SECURITY")

# ============================================
# Header / Footer Detection
# ============================================

def get_header_footer_bounds(doc: fitz.Document, *, num_samples: int=100, threshold_occurence: float=0.5, max_distance_from_edge: float=0.1) -> Tuple[float, float]:
    """
    Identifies recurring header and footer text by analyzing a random sample of pages

    Args:
        doc (fitz.Document): A PyMuPDF document objects.
    Returns:
        Tuple[float, float]: A tuple containing the two pixel heights which segment
                             the document into header, content, footer.
    """ 
    sample_pages = get_random_pages(doc, num_pages=num_samples)

    if not sample_pages:
        logger.info("No pages found in the sample. Assuming no header/footer.")
        return (0.0, float(doc[0].rect.height))

    # We assume all pages have the same height
    page_height = float(sample_pages[0].rect.height)

    header_candidates = defaultdict(int)
    footer_candidates = defaultdict(int)

    for span in tqdm(get_typed_spans(sample_pages), desc="Calculating header/footer bounds", leave=False):
        text = span.get("text", "").strip()
        if not text:
            continue
    
        bbox = span.get("bbox")
        y_top, y_bottom = bbox[1], bbox[3]

        # Record all spans whose lowest point is in the top 10% of that page height
        if y_bottom / page_height < max_distance_from_edge:
            header_candidates[(text, int(y_bottom + 1))] += 1 

        # Record all spans whose highest point is in the bottom 10% of the page height
        if y_top / page_height > max_distance_from_edge:
            footer_candidates[(text, int(y_top))] += 1

    # Get headers which occured at least 50% of the time
    header_top_candidates = [key[1] for key, count in header_candidates.items() if count >= threshold_occurence * num_samples]
    header_threshold = max(header_top_candidates) if header_top_candidates else 0.0

    # Get footers which occured at least 50% of the time
    footer_top_candidates = [key[1] for key, count in footer_candidates.items() if count >= threshold_occurence * num_samples]
    footer_threshold = min(footer_top_candidates) if footer_top_candidates else page_height

    return (header_threshold, footer_threshold)



# ============================================
# Page Sampling
# ============================================

def get_random_pages(doc: fitz.Document, *, num_pages: int=10, start: int=1, stop: int=-1) -> List[fitz.Page]:
    """
    Selects a specified number of unique random pages from a document
    
    Args:
        doc (fitz.Document): The PyMuPDF document object.
        num_pages(int): The number of random pages to select.

    Returns:
        list: A list of the random pages
    """

    # Set default value of stop
    if stop == -1:
        stop = len(doc)

    # Check if start and stop are in bounds
    if not 0 <= start < stop <= len(doc):
        raise ValueError(f"Invalid page value. Expected 0 <= start < stop <= {len(doc)}, "
                         f"but start={start}, stop={stop}")

    valid_page_indices = range(start, stop)
    num_pages = min(num_pages, len(valid_page_indices))

    if num_pages == 0:
        return []

    random_indices = random.sample(valid_page_indices, num_pages)

    # Return the pages corresponding to those indices
    return [doc[i] for i in random_indices]



# ============================================
# Span Extraction
# ============================================

def get_typed_spans(pages: Union[List[fitz.Page], fitz.Document], *, block_type: int = 0) -> Generator[Dict, None, None]:
    """
    A generator function that yields text spans from a list of pages
    
    Args:
        pages (List[fitz.Page] or fitz.Document): A list of PyMuPDF page objects
                                                  or a PyMuPDF document object
    
    Yields:
        Dict: A dictionary representing a single text span or an image block.

    """
    if block_type not in {0, 1}:
        raise ValueError(f"Invalid block_type value. Expected type to be 0 or 1, "
                         f"but block_type = {block_type}")

    for page in pages:
        for block in page.get_text("dict", sort=True)["blocks"]:
            print(f"block {block.get('number')} {block.get('bbox')}")
            if block["type"] != block_type:
                continue

            # Handle text spans
            if block_type == 0:
                for line in block["lines"]:
                    for span in line["spans"]:
                        yield span

            # Handle image blocks
            elif block_type == 1:
                yield block



def get_sorted_spans(pages: Union[List[fitz.Page], fitz.Document], *, ignores=[]) -> list:
    """
    Extracts all text spans from a page and sorts them top-to-bottom,
    then left-to-right.
    """
    all_spans = []
    # Loop through each block and line to collect all spans
    for page in pages:
        page_spans = []
        counter = 0
        for block in page.get_text("dict")["blocks"]:
            counter += 1
            if block["type"] == 0:  # Text block
                for line in block["lines"]:
                    for span in line["spans"]:
                        if not is_span_inside_bbox(span, ignores):
                            page_spans.append(span)

        all_spans.extend(sorted(page_spans, key=lambda s: (round(s.get('bbox')[1]), round(s.get('bbox')[0]))))

    # Sort all spans based on y0 first (top to bottom),
    # then x0 (left to right)
    return all_spans



def is_span_inside_bbox(span, bboxs: List[Tuple[int, int, int, int]]) -> bool:
    """Returns true of the span is within any of the bboxs"""

    if not bboxs:
        return False

    x0, y0, x1, y1 = span.get('bbox')
    for bbox in bboxs:
        if not bbox:
            return False
        bx0, by0, bx1, by1 = bbox
        if bx0 <= x0 and x1 <= bx1 and by0 <= y0 and y1 <= by1:
            return True
    return False



# ============================================
# Text Formatting
# ============================================

def get_text_attributes(flags: int) -> list[str]:
    """Translates a fitz flags integer into a list of text attributes."""
    attributes = []
    if flags & 1:
        attributes.append("superscript")
    if flags & 2:
        attributes.append("italic")
    if flags & 4:
        attributes.append("serif")
    if flags & 8:
        attributes.append("monospaced")
    if flags & 16:
        attributes.append("bold")
    return attributes



def apply_text_attributes_md(span: Dict) -> str:
    attributes = get_text_attributes(span.get('flags'))
    span_size = span.get('size')
    span_text = span.get('text')
    span_color = span.get('color')

    # Existence
    if span_text.strip() == "":
        return ""

    # Coloring
    if get_color_name(span_color) in ['blue', 'teal']:
        span_text = "(" + span_text + ")[]"

    # Sizing
    if span_size >= 14:
        span_text = "#" + span_text
    elif span_size >= 11: 
        span_text = "##" + span_text
    elif 'bold' in attributes:
        span_text = "**" + span_text + "**"

    # Styling
    if 'italics' in attributes:
        span_text = "*" + span_text + "*"

    return span_text



def get_color_name(color: int) -> str:
    """Returns the name of the closest color to the given RGB tuple."""

    # Extract the RGB components from the integer
    red = (color >> 16) & 0xFF
    green = (color >> 8) & 0xFF
    blue = color & 0xFF

    rgb_tuple = (red, green, blue)

    colors = {
        "red": (255, 0, 0), "green": (0, 128, 0), "blue": (0, 0, 255),
        "yellow": (255, 255, 0), "cyan": (0, 255, 255), "magenta": (255, 0, 255),
        "white": (255, 255, 255), "black": (0, 0, 0), "gray": (128, 128, 128),
        "maroon": (128, 0, 0), "navy": (0, 0, 128), "olive": (128, 128, 0),
        "teal": (0, 128, 128), "purple": (128, 0, 128), "aquamarine": (127, 255, 212),
        "lime": (0, 255, 0), "silver": (192, 192, 192)
    }
    min_distance = float('inf')
    closest_color_name = "unknown"
    r1, g1, b1 = rgb_tuple
    for name, rgb2 in colors.items():
        r2, g2, b2 = rgb2
        squared_distance = (r2 - r1)**2 + (g2 - g1)**2 + (b2 - b1)**2
        if squared_distance < min_distance:
            min_distance = squared_distance
            closest_color_name = name
    return closest_color_name



# ============================================
# Debugging
# ============================================

def pretty_print(page: fitz.Page) -> None:
    """
    Print a hierarchical representation of a page.
    """

    def print_with_indent(text: str, level: int):
        """Prints a string with a specified level of indentation."""
        indent = "    " * level  # Four spaces per level
        print(f"{indent}{text}")

    def get_color_block(color: int) -> str:
        """
        Creates a colored block string from a fitz color integer.
        """
            
        if color is None:
            return "No Color"
        
        # Extract the RGB components from the integer
        red = (color >> 16) & 0xFF
        green = (color >> 8) & 0xFF
        blue = color & 0xFF
        
        # ANSI escape code for a colored background
        color_code = f"\033[48;2;{red};{green};{blue}m"
        reset_code = "\033[0m"
        
        # Create the colored block with a label
        rgb_tuple = (red, green, blue)
        block = f"{color_code}  {reset_code} RGB:({red},{green},{blue}) - {get_color_name(color)}"
        return block

    for block in page.get_text("dict", sort=True)["blocks"]:
        block_bbox = [round(b) for b in block.get('bbox')]
        print_with_indent(f"block {block.get('number')}", 0)
        print_with_indent(f"bbox: {block_bbox[:2]}, {block_bbox[2:]}", 1)
        print_with_indent(f"line", 1)
        for i, line in enumerate(sorted(block.get("lines", []), key=lambda s: s.get('bbox')[0])):
            line_bbox = [round(b) for b in line.get('bbox')]
            print_with_indent(f"line {i}", 2)
            print_with_indent(f"bbox: {line_bbox[:2]}, {line_bbox[2:]}", 3)
            print_with_indent(f"spans", 3)
            for j, span in enumerate(sorted(line.get("spans"), key=lambda s: round(s.get('bbox')[0]))):
                span_bbox = [round(b) for b in span.get('bbox')]
                print_with_indent(f"span {j}", 3)
                print_with_indent(f"bbox: {span_bbox[:2]}, {span_bbox[2:]}", 4)
                print_with_indent(f"size: {round(span.get('size'))}", 4)
                print_with_indent(f"font: {span.get('font')}", 4)
                print_with_indent(f"flags: {get_text_attributes(span.get('flags'))}", 4)
                print_with_indent(f"color: {get_color_block(span.get('color'))}", 4)
                print_with_indent(f"text: \033[7m{span.get('text', '').strip()}\033[0m", 4)


# ============================================
# CLI
# ============================================

def parse_args() -> argparse.Namespace:
    """
    Parses comand-line argument for debugging.

    Returns:
        argparse.Namespace: An object containing the parsed arguments
    """
    ap = argparse.ArgumentParser(description="Debugging")

    ap.add_argument(
        "--data", 
        required=True, 
        help="Path to the data for ingestion."
    )

    ap.add_argument(
        "--mode", 
        required=True, 
        choices=["pretty_print"], 
        help="Category of the debugging method. "
    )

    ap.add_argument(
        "--page", 
        required=True, 
        help="Which page number to debug"
    )

    return ap.parse_args()


def run_debugging(args: argparse.Namespace) -> None:
    """
    Orchestrates the data ingestion process by preparing and validating aruments.
    
    This function takes the raw arguments, converts types, and then call other functions
    to perform the necessary setup.
    """

    try:
        data_path = Path(args.data)
    except ValueError:
        raise ValueError(f"{data_path} is not a valid path")

    mode = args.mode

    doc = fitz.open(data_path)
    page = doc[int(args.page)]

    logger.info(f"Debugging started for '{data_path.stem}' in '{mode}' mode.")

    if mode == "pretty_print":
        pretty_print(page)
    else:
        logger.error(f"Unsupported mode: {mode}")
        sys.exit(1)


def main() -> None:
    """
    Main function to orchestrate the debugging process.
    """
    args = parse_args()
    run_debugging(args)


if __name__ == "__main__":
    main()
