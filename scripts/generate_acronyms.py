"""
Generate acronyms CSV from a parsed document's acronym table.

Reads the "Acronyms and Abbreviations" chunk from a parsed JSON file,
resolves nested acronyms (e.g. "MDTS" -> "MDT Session file" becomes
"Minutia Deviation Tool (MDT) Session file"), and writes a CSV with
columns: Acronym, Expansion.

Usage:
    python scripts/generate_acronyms.py [--parsed PATH] [--output PATH]

Defaults:
    --parsed  data/parsed/<first .json file found>
    --output  data/acronyms.csv
"""

import argparse
import csv
import json
import re
import sys
from pathlib import Path
from typing import Dict, Optional


def parse_acronym_table(content: str) -> Dict[str, str]:
    """Parse a markdown table of acronyms into a dict."""
    acronyms = {}
    for line in content.split("\n"):
        line = line.strip()
        if not line.startswith("|") or "---" in line:
            continue
        parts = [p.strip().strip("*") for p in line.split("|")]
        parts = [p for p in parts if p]
        if len(parts) >= 2 and parts[0] not in ("ACRONYM", ""):
            acronyms[parts[0]] = parts[1]
    return acronyms


def resolve_nested(acronyms: dict[str, str]) -> Dict[str, str]:
    """
    Resolve nested acronyms in expansions.

    If "MDTS" -> "MDT Session file" and "MDT" -> "Minutia Deviation Tool",
    the expansion becomes "Minutia Deviation Tool (MDT) Session file".

    Uses multiple passes until no more substitutions are found.
    """
    resolved = dict(acronyms)

    # Sort by length descending so longer acronyms match first
    sorted_keys = sorted(resolved.keys(), key=len, reverse=True)

    # Match acronyms only when NOT inside parentheses — this prevents
    # re-expanding citations like "(MDT)" that were inserted by a
    # previous pass.
    pattern = re.compile(
        r"(?<!\()\b(" + "|".join(re.escape(k) for k in sorted_keys) + r")\b(?!\))"
    )

    max_passes = 5
    for _ in range(max_passes):
        changed = False
        for key in list(resolved.keys()):
            expansion = resolved[key]

            def replacer(match, _key=key):
                word = match.group(0)
                # Don't expand a term within its own definition
                if word.upper() == _key.upper():
                    return word
                # Find the matching acronym (case-insensitive)
                for k in sorted_keys:
                    if k.upper() == word.upper():
                        return f"{acronyms[k]} ({word})"
                return word

            new_expansion = pattern.sub(replacer, expansion)
            if new_expansion != expansion:
                resolved[key] = new_expansion
                changed = True

        if not changed:
            break

    return resolved


def find_acronym_chunk(parsed_data: dict) -> Optional[str]:
    """Find the acronyms/abbreviations chunk in parsed data."""
    for chunk in parsed_data.get("chunks", []):
        title = chunk.get("title", "").lower()
        if "acronym" in title or "abbreviat" in title:
            return chunk.get("content", "")
    return None


def main():
    parser = argparse.ArgumentParser(description="Generate acronyms CSV")
    parser.add_argument("--parsed", type=Path, help="Path to parsed JSON file")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/acronyms.csv"),
        help="Output CSV path",
    )
    args = parser.parse_args()

    # Find parsed JSON file
    if args.parsed:
        parsed_path = args.parsed
    else:
        parsed_dir = Path("data/parsed")
        json_files = list(parsed_dir.glob("*.json"))
        if not json_files:
            print("No parsed JSON files found in data/parsed/", file=sys.stderr)
            sys.exit(1)
        parsed_path = json_files[0]

    print(f"Reading: {parsed_path}")

    with open(parsed_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    content = find_acronym_chunk(data)
    if not content:
        print("No acronyms/abbreviations section found", file=sys.stderr)
        sys.exit(1)

    # Parse and resolve
    raw = parse_acronym_table(content)
    print(f"Parsed {len(raw)} acronyms")

    resolved = resolve_nested(raw)

    # Show nested resolutions
    for key in sorted(resolved.keys()):
        if resolved[key] != raw[key]:
            print(f"  Nested: {key} = {raw[key]!r} -> {resolved[key]!r}")

    # Write CSV
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["Acronym", "Expansion"])
        writer.writeheader()
        for key in sorted(resolved.keys()):
            writer.writerow({"Acronym": key, "Expansion": resolved[key]})

    print(f"Wrote {len(resolved)} acronyms to {args.output}")


if __name__ == "__main__":
    main()
