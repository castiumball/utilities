"""
Post-Extraction Validation
==========================

Validation functions that run after extraction and deduplication to
remove low-quality entities and relationships. Each validator logs
what it removes for transparency. Called from pipeline.run_extraction()
after fuzzy_deduplicate().

Constants / Pattern Sets:
    GENERIC_NAMES                   Names that should never be entities
    KNOWN_LANGUAGES                 Recognized programming languages
    DIRECTION_RULES                 Source/target label constraints per relationship type
    _DATA_FIELD_PATTERNS            Regex for DataStore fields vs. real stores
    _HARDWARE_PATTERNS              Regex for hardware components
    _NOT_LANGUAGE_PATTERN           Regex for bare acronyms (not languages)
    _NOT_LANGUAGE_NAMES             Expanded names that are not languages
    _NOT_LANGUAGE_DESC_PATTERNS     Description patterns that disqualify Language label
    _FIELD_ID_PATTERN               Regex for EBTS/standard field identifiers
    _CITATION_PATTERN               Regex for citation references like "[5]"
    _SECTION_REF_PATTERN            Regex for section heading references
    _FIGURE_REF_PATTERN             Regex for figure/table/appendix references
    _ORG_DESC_PATTERNS              Description patterns indicating an organization
    _NOT_PROTOCOL_DESC_PATTERNS     Description patterns disqualifying Protocol label

Entity Validators:
    remove_generic_entities         Remove common nouns without specific names
    remove_low_value_entities       Remove data fields, hardware, non-language acronyms
    reclassify_person_to_org        Relabel Person to Organization based on description
    remove_misclassified_protocols  Remove formats/conventions mislabeled as Protocol
    check_description_quality       Remove entities with too-short descriptions

Relationship Validators:
    remove_orphan_relationships     Remove relationships referencing missing entities
    validate_relationship_directions  Enforce logical source/target label constraints

Orchestration:
    validate_extraction             Run all validators in dependency order
"""

import logging
import re
from typing import Dict, List, Set, Tuple

from config import settings

logger = logging.getLogger(__name__)


# ============================================
# Constants and Pattern Sets
# ============================================

# ── Generic entity names ───────────────────────────────────────────
# Names that should never be entities regardless of label.
# Only single-word or known-boilerplate names; multi-word names like
# "File Manager" or "Session Database" are preserved.
GENERIC_NAMES = {
    # Generic roles
    "user", "users", "author", "author(s)", "authors",
    "operator", "operators", "administrator", "client", "server",
    # Generic architecture terms
    "system", "data", "input", "output",
    "file", "files", "database", "module",
    "component", "function", "interface",
    "process", "service", "application",
    "software", "hardware",
    # Generic OOP / programming concepts
    "code", "class", "method", "variable", "object",
    "library", "framework", "pointer",
    # Generic document / UI terms
    "standard", "new", "none", "summary",
    "baseline", "comparison", "title",
    "title:", "export",
    # The label definition itself should not be an entity
    "computer software configuration item",
    "computer software component",
    "computer software unit",
    # Colors (descriptions, not named entities)
    "red", "blue", "green", "yellow", "orange", "purple",
    "white", "black", "gray", "grey", "cyan", "magenta",
    # Positional / directional terms
    "origin", "center", "left", "right", "top", "bottom",
    "upper left", "upper right", "lower left", "lower right",
    # Button labels / UI primitives
    "ok", "cancel", "yes", "no", "close",
    "mouse cursor", "left mouse button", "right mouse button",
    "mouse click", "keyboard", "scroll bar",
    # Generic UI / display elements
    "numerical label", "action taken", "pop-up window",
    "radio button", "drop-down menu", "coordinate axis",
    # Generic infrastructure terms
    "network", "local computer", "government desktop computer",
    # Generic math / measurement terms
    "maximum values", "minimum values",
    # Label definitions — the label type name itself
    "csci", "csc", "csu", "srs", "sdd",
    # Generic concepts that are never specific entities
    "naming convention", "fingerprint",
    "object oriented development", "object-oriented development",
    "object oriented development practices",
    "object-oriented development practices",
    "adopted standard",
}


# ── Known programming languages ───────────────────────────────────
# Used to distinguish real Language entities from misclassified
# acronyms, file formats, and units.
KNOWN_LANGUAGES = {
    "c", "c++", "c#", "java", "python", "ada", "ada 83", "ada 95",
    "fortran", "cobol", "javascript", "typescript", "ruby", "go",
    "rust", "swift", "kotlin", "scala", "perl", "php", "lua",
    "visual basic", "vb.net", ".net", "objective-c",
    "assembly", "lisp", "haskell", "erlang", "elixir",
    "matlab", "r", "sql", "pl/sql", "t-sql",
    "html", "css", "xml", "json", "yaml",
    "react", "angular", "vue", "django", "flask", "spring",
    "qt", "gtk", "wxwidgets", "mfc",
}


# ── Regex patterns for low-value entity detection ──────────────────

# DataStore entities that look like individual data fields
_DATA_FIELD_PATTERNS = [
    re.compile(r"^[A-Z],?\s*[A-Z]", re.IGNORECASE),       # "X, Y, phi"
    re.compile(r"^\d+\s+(micron|pixel|byte|bit)", re.IGNORECASE),  # "10 micron units"
    re.compile(
        r"^(threshold|filter|parameter|criteria|coordinate|value|unit|flag|count|index)",
        re.IGNORECASE,
    ),
]

# Hardware items that should not be System entities
_HARDWARE_PATTERNS = [
    re.compile(
        r"(processor|cpu|memory|hard\s*drive|ram|disk|monitor|keyboard|mouse|printer|gpu)",
        re.IGNORECASE,
    ),
    re.compile(r"^\d+\s*(gb|mb|tb|ghz|mhz)", re.IGNORECASE),  # "2 Gigabytes (GB)"
]

# Bare acronyms that should not be Language entities
_NOT_LANGUAGE_PATTERN = re.compile(r"^[A-Z]{2,5}$")

# Expanded forms of file formats, units, colors, etc. that are
# NOT programming languages despite being labeled as Language.
# Checked case-insensitively against entity name.
_NOT_LANGUAGE_NAMES = {
    # File formats (expanded and abbreviated)
    "bitmap", "comma-separated value", "comma separated value",
    "tab-separated value", "tab separated value",
    "portable document format", "tagged image file format",
    "portable network graphics", "joint photographic experts group",
    "extensible markup language", "csv", "bmp", "tiff", "tif",
    "png", "jpeg", "jpg", "gif", "pdf", "doc", "docx", "xls", "xlsx",
    "rtf", "svg", "avi", "mp4", "wav",
    # Units of measurement
    "gigabyte", "megabyte", "kilobyte", "terabyte", "petabyte",
    "gigabytes", "megabytes", "kilobytes", "terabytes",
    "pixels per inch", "dots per inch",
    "hertz", "megahertz", "gigahertz",
    "end of line", "end of file", "end-of-line", "end-of-file",
    # Colors
    "red", "blue", "green", "yellow", "orange", "purple",
    "white", "black", "gray", "grey", "cyan", "magenta",
    # Concepts that aren't languages
    "object oriented development", "object-oriented development",
    "agile", "waterfall", "scrum",
    # Operating systems (not programming languages)
    "windows", "linux", "unix", "macos", "mac os", "mac os x",
    "solaris", "android", "ios", "ms-dos", "dos",
    # IDEs and development tools (not languages)
    "visual studio", "eclipse", "intellij", "xcode", "netbeans",
    "visual studio code", "vs code",
    # CPU architectures (hardware, not programming languages)
    "x86", "x64", "x86_64", "x86-64", "amd64",
    "arm", "arm64", "aarch64",
    "mips", "risc-v", "sparc", "powerpc", "ppc",
    "ia-32", "ia-64", "itanium",
}

# DataStore entities that are really EBTS/standard field identifiers
# like "2.010a", "9.300a-e", "13.006"
_FIELD_ID_PATTERN = re.compile(r"^\d+\.\d+[a-z]?(-[a-z])?$", re.IGNORECASE)

# Citation references like "[5]", "[12]"
_CITATION_PATTERN = re.compile(r"^\[\d+\]$")

# Section/heading references (document structure, not entities)
_SECTION_REF_PATTERN = re.compile(r"^Section\s+\d", re.IGNORECASE)

# Figure/table references (document structure, not entities)
_FIGURE_REF_PATTERN = re.compile(r"^(Figure|Table|Appendix)\s+\d", re.IGNORECASE)

# Language entities whose descriptions reveal they are NOT programming languages.
# Checked case-insensitively against the entity description.
_NOT_LANGUAGE_DESC_PATTERNS = [
    re.compile(r"unit of measurement", re.IGNORECASE),
    re.compile(r"data (format|structure)", re.IGNORECASE),
    re.compile(r"date.*(format|string)", re.IGNORECASE),
    re.compile(r"file format", re.IGNORECASE),
    re.compile(r"(text|table|log).*(format|structure)", re.IGNORECASE),
    re.compile(r"(line ending|delimiter|separator)", re.IGNORECASE),
]


# ── Person → Organization reclassification ─────────────────────────
# The LLM sometimes labels organizations as Person. These patterns
# in the description indicate the entity is an organization.
_ORG_DESC_PATTERNS = [
    re.compile(r"\b(company|corporation|corp|inc|ltd|llc|gmbh)\b", re.IGNORECASE),
    re.compile(r"\b(contractor|vendor|supplier|manufacturer)\b", re.IGNORECASE),
    re.compile(r"\b(agency|bureau|directorate|division|department)\b", re.IGNORECASE),
    re.compile(r"\b(institute|laboratory|university|college)\b", re.IGNORECASE),
    re.compile(r"\b(consortium|coalition|alliance|association|foundation)\b", re.IGNORECASE),
    re.compile(r"\b(program office|office of)\b", re.IGNORECASE),
    re.compile(r"\b(defense|defence) (contractor|company|firm)\b", re.IGNORECASE),
    re.compile(r"\bdeveloped (by|the)\b.*\b(system|software|platform)\b", re.IGNORECASE),
]

# ── Protocol description-based filtering ───────────────────────────
# The LLM sometimes labels date formats, table formats, etc. as Protocol.
_NOT_PROTOCOL_DESC_PATTERNS = [
    re.compile(r"\b(date|time) format\b", re.IGNORECASE),
    re.compile(r"\b(table|file|data|text|log) format\b", re.IGNORECASE),
    re.compile(r"\bformat (used|for|of|to)\b", re.IGNORECASE),
    re.compile(r"\bunit of measurement\b", re.IGNORECASE),
    re.compile(r"\bnaming convention\b", re.IGNORECASE),
]


# ── Relationship direction rules ───────────────────────────────────
# Defines logical constraints on which entity labels can appear as
# source/target for each relationship type. None means "any allowed".
DIRECTION_RULES = {
    "DEVELOPED_BY": {
        "target_labels": {"Person", "Organization"},
        "source_exclude": {"Person", "Organization"},
    },
    "SPONSORED_BY": {
        "target_labels": {"Organization", "Person"},
        "source_exclude": {"Person", "Organization"},
    },
    "CONTAINS": {
        "source_exclude": {"Person", "Organization", "Standard", "Language", "ExternalSystem"},
    },
    "READS_FROM": {
        "target_labels": {"DataStore"},
        "source_exclude": {"Person", "Organization", "Standard", "Language", "DataStore"},
    },
    "WRITES_TO": {
        "target_labels": {"DataStore"},
        "source_exclude": {"Person", "Organization", "Standard", "Language", "DataStore"},
    },
    "REFERENCES": {
        "target_labels": {"Standard", "Requirement"},
    },
    "IMPLEMENTS": {
        # Source should be a software component, not Language/Person/Org
        "source_exclude": {"Language", "Person", "Organization", "Standard"},
    },
}


# ============================================
# Entity Validators
# ============================================

def remove_generic_entities(
    entities: List[Dict],
) -> Tuple[List[Dict], int]:
    """
    Remove entities with overly generic names that are not specific
    named components. Generic single-word nouns and known boilerplate
    like "Author(s)" add noise to the graph.

    Multi-word names like "File Manager" or "Data Processor" are preserved
    because they are likely specific named components.
    """
    valid = []
    removed = 0
    for entity in entities:
        name_lower = entity["name"].lower().strip()
        if name_lower in GENERIC_NAMES:
            removed += 1
            logger.debug(
                "Removed generic entity: %s [%s]", entity["name"], entity["label"]
            )
            continue
        valid.append(entity)

    if removed:
        logger.info("Removed %d generic entities", removed)
    return valid, removed


def remove_low_value_entities(
    entities: List[Dict],
) -> Tuple[List[Dict], int]:
    """
    Remove entities that are misclassified or too low-level to be
    useful in the knowledge graph:

    - DataStore entities that look like individual data fields/columns
    - DataStore entities that are hardware specs or field identifiers
    - CSU entities that are data field identifiers, not software units
    - System entities that are actually hardware components
    - ExternalSystem entities that are hardware or OS/IDE names
    - Function entities that are really file formats or line endings
    - Language entities that are file formats, units, hardware, or acronyms
    - Section headings referenced as entities
    - Any entity that is a citation reference like "[5]"
    """
    valid = []
    removed = 0

    for entity in entities:
        should_remove = False
        label = entity["label"]
        name = entity["name"]
        name_lower = name.lower().strip()
        desc = (entity.get("description") or "").lower()

        # Citation references are never valid entities
        if _CITATION_PATTERN.search(name.strip()):
            should_remove = True

        # Section headings and figure/table refs are document structure
        elif _SECTION_REF_PATTERN.search(name.strip()):
            should_remove = True
        elif _FIGURE_REF_PATTERN.search(name.strip()):
            should_remove = True

        elif label == "DataStore":
            # Data fields rather than actual data stores
            if any(p.search(name) for p in _DATA_FIELD_PATTERNS):
                should_remove = True
            # EBTS/standard field identifiers (e.g., "2.010a", "9.300a-e")
            elif _FIELD_ID_PATTERN.search(name.strip()):
                should_remove = True
            # Hardware specs mislabeled as DataStore
            elif any(p.search(name) for p in _HARDWARE_PATTERNS):
                should_remove = True
            # Short generic names with non-specific descriptions
            elif len(name.split()) <= 2 and any(
                kw in desc
                for kw in ("unit of", "quantity of", "set of", "value of", "number of")
            ):
                should_remove = True
            # Very short entity names — data field identifiers (e.g., "X", "ΔX A")
            elif len(re.sub(r"[\s_\-.]", "", name)) <= 3:
                should_remove = True
            # Description says "data field" — not an actual data store
            elif "data field" in desc:
                should_remove = True

        elif label == "CSU":
            # Very short names — data field identifiers, not software units
            if len(re.sub(r"[\s_\-.]", "", name)) <= 3:
                should_remove = True
            # Description says "data field" — not a software unit
            elif "data field" in desc:
                should_remove = True

        elif label == "System":
            # Hardware components
            if any(p.search(name) for p in _HARDWARE_PATTERNS):
                should_remove = True

        elif label == "ExternalSystem":
            # Hardware components mislabeled as ExternalSystem
            if any(p.search(name) for p in _HARDWARE_PATTERNS):
                should_remove = True
            # Description reveals it's hardware, not an external system
            elif any(
                kw in desc
                for kw in ("hardware requirement", "memory configuration",
                           "hard drive configuration", "type of processor")
            ):
                should_remove = True
            else:
                # OS/IDE names that aren't external systems
                ext_norm = re.sub(r"\s*\([^)]*\)\s*", " ", name_lower).strip()
                ext_norm = re.sub(r"\s+\d[\d.]*\s*$", "", ext_norm).strip()
                if ext_norm in _NOT_LANGUAGE_NAMES or name_lower in _NOT_LANGUAGE_NAMES:
                    should_remove = True

        elif label == "Function":
            # File formats described as functions
            if any(
                kw in desc
                for kw in ("file format", "end of line", "end-of-line",
                           "end of file", "end-of-file")
            ):
                should_remove = True

        elif label == "Language":
            # Hardware specs mislabeled as Language (e.g., "2 GB of RAM")
            if any(p.search(name) for p in _HARDWARE_PATTERNS):
                should_remove = True
            elif name_lower not in KNOWN_LANGUAGES:
                # Bare acronyms (2-5 uppercase chars)
                if _NOT_LANGUAGE_PATTERN.search(name):
                    should_remove = True
                # Description reveals it's a format/unit, not a language
                elif any(p.search(desc) for p in _NOT_LANGUAGE_DESC_PATTERNS):
                    should_remove = True
                else:
                    # Normalize: strip parentheticals and trailing versions
                    lang_norm = re.sub(r"\s*\([^)]*\)\s*", " ", name_lower).strip()
                    lang_norm = re.sub(r"\s+\d[\d.]*\s*$", "", lang_norm).strip()
                    if lang_norm in _NOT_LANGUAGE_NAMES or name_lower in _NOT_LANGUAGE_NAMES:
                        should_remove = True

        if should_remove:
            removed += 1
            logger.debug(
                "Removed low-value entity: %s [%s] - %s",
                name, label, desc[:60] if desc else "(no description)",
            )
        else:
            valid.append(entity)

    if removed:
        logger.info("Removed %d low-value entities", removed)
    return valid, removed


def check_description_quality(
    entities: List[Dict],
    min_length: int = 10,
) -> Tuple[List[Dict], int]:
    """
    Remove entities with very short or missing descriptions.

    These are typically extraction artifacts where the model couldn't
    determine what the entity actually is. The default min_length of 10
    catches descriptions like "A type" or single words, while preserving
    genuine one-sentence descriptions.
    """
    valid = []
    removed = 0
    for entity in entities:
        desc = (entity.get("description") or "").strip()
        if len(desc) < min_length:
            removed += 1
            logger.debug(
                "Removed entity with poor description: %s [%s] desc='%s'",
                entity["name"], entity["label"], desc,
            )
        else:
            valid.append(entity)

    if removed:
        logger.info("Removed %d entities with poor descriptions", removed)
    return valid, removed


# ============================================
# Relationship Validators
# ============================================

def remove_orphan_relationships(
    relationships: List[Dict],
    entity_ids: Set[str],
) -> Tuple[List[Dict], int]:
    """
    Remove relationships where source_id or target_id does not match
    any entity in the entity list.

    This catches phantom references caused by _find_entity_label()
    falling back to the wrong label, creating mismatched entity IDs
    (e.g., system_author_s instead of person_author_s).
    """
    valid = []
    removed = 0
    for rel in relationships:
        if rel["source_id"] in entity_ids and rel["target_id"] in entity_ids:
            valid.append(rel)
        else:
            removed += 1
            logger.debug(
                "Removed orphan relationship: %s -[%s]-> %s",
                rel["source_id"], rel["type"], rel["target_id"],
            )

    if removed:
        logger.info("Removed %d orphan relationships", removed)
    return valid, removed


def validate_relationship_directions(
    relationships: List[Dict],
    entity_map: Dict[str, Dict],
) -> Tuple[List[Dict], int]:
    """
    Validate that relationship types have logically correct source/target
    entity labels.

    For example:
    - DEVELOPED_BY target must be Person or Organization
    - READS_FROM/WRITES_TO target must be DataStore
    - REFERENCES target must be Standard or Requirement
    - CONTAINS source must not be Person/Organization/Standard/Language
    """
    valid = []
    removed = 0

    for rel in relationships:
        rel_type = rel["type"]
        source = entity_map.get(rel["source_id"])
        target = entity_map.get(rel["target_id"])

        if source is None or target is None:
            # Orphan — should have been caught already, pass through
            valid.append(rel)
            continue

        rules = DIRECTION_RULES.get(rel_type)
        if rules is None:
            # No direction rules for this relationship type
            valid.append(rel)
            continue

        source_label = source["label"]
        target_label = target["label"]

        ok = True
        if "target_labels" in rules and target_label not in rules["target_labels"]:
            ok = False
        if "source_exclude" in rules and source_label in rules["source_exclude"]:
            ok = False

        if ok:
            valid.append(rel)
        else:
            removed += 1
            logger.debug(
                "Removed invalid relationship: %s [%s] -[%s]-> %s [%s]",
                source.get("name", "?"), source_label,
                rel_type,
                target.get("name", "?"), target_label,
            )

    if removed:
        logger.info("Removed %d invalid-direction relationships", removed)
    return valid, removed


def reclassify_person_to_org(
    entities: List[Dict],
) -> Tuple[List[Dict], int, Dict[str, str]]:
    """
    Reclassify Person entities to Organization when their description
    contains organizational keywords (company, agency, contractor, etc.).

    The LLM sometimes labels organizations as Person, especially for
    single-name entities like "Raytheon" or "Northrop Grumman".
    Reclassifying is better than removing because the entity is real,
    just mislabeled.

    Returns:
        (entities, count_reclassified, id_remap) where id_remap maps
        old person_* IDs to new organization_* IDs.
    """
    reclassified = 0
    id_remap: Dict[str, str] = {}

    for entity in entities:
        if entity["label"] != "Person":
            continue
        desc = (entity.get("description") or "").lower()
        if any(p.search(desc) for p in _ORG_DESC_PATTERNS):
            old_id = entity["id"]
            entity["label"] = "Organization"
            # Regenerate ID with new label
            from .resolution import generate_entity_id
            new_id = generate_entity_id("Organization", entity["name"])
            entity["id"] = new_id
            id_remap[old_id] = new_id
            reclassified += 1
            logger.debug(
                "Reclassified Person → Organization: %s (was %s, now %s)",
                entity["name"], old_id, new_id,
            )

    if reclassified:
        logger.info("Reclassified %d Person entities to Organization", reclassified)
    return entities, reclassified, id_remap


def remove_misclassified_protocols(
    entities: List[Dict],
) -> Tuple[List[Dict], int]:
    """
    Remove Protocol entities whose descriptions reveal they are
    actually formats, naming conventions, or units — not communication
    protocols.

    Real protocols (TCP/IP, RS-232, HTTP) have descriptions about
    communication and data exchange, not formatting.
    """
    valid = []
    removed = 0
    for entity in entities:
        if entity["label"] == "Protocol":
            desc = (entity.get("description") or "").lower()
            if any(p.search(desc) for p in _NOT_PROTOCOL_DESC_PATTERNS):
                removed += 1
                logger.debug(
                    "Removed misclassified Protocol: %s - %s",
                    entity["name"], desc[:60],
                )
                continue
        valid.append(entity)

    if removed:
        logger.info("Removed %d misclassified Protocol entities", removed)
    return valid, removed


# ============================================
# Orchestration
# ============================================

def validate_extraction(
    entities: List[Dict],
    relationships: List[Dict],
) -> Tuple[List[Dict], List[Dict], Dict]:
    """
    Run all validation passes on extracted entities and relationships.

    Validators run in dependency order: entity filters first (so orphan
    relationship removal sees the final entity set), then relationship
    filters, then final dedup.

    Returns:
        (cleaned_entities, cleaned_relationships, validation_report)
    """
    report = {}

    # --- Entity validators ---

    entities, n = remove_generic_entities(entities)
    report["generic_entities_removed"] = n

    entities, n = remove_low_value_entities(entities)
    report["low_value_entities_removed"] = n

    entities, n, id_remap = reclassify_person_to_org(entities)
    report["person_to_org_reclassified"] = n

    # Apply reclassification ID remaps to relationships so they don't
    # become orphans (person_raytheon -> organization_raytheon)
    if id_remap:
        for rel in relationships:
            if rel["source_id"] in id_remap:
                rel["source_id"] = id_remap[rel["source_id"]]
            if rel["target_id"] in id_remap:
                rel["target_id"] = id_remap[rel["target_id"]]

    entities, n = remove_misclassified_protocols(entities)
    report["misclassified_protocols_removed"] = n

    entities, n = check_description_quality(
        entities,
        min_length=settings.VALIDATION_MIN_DESCRIPTION_LENGTH,
    )
    report["poor_description_entities_removed"] = n

    # --- Relationship validators ---

    entity_ids = {e["id"] for e in entities}
    entity_map = {e["id"]: e for e in entities}

    relationships, n = remove_orphan_relationships(relationships, entity_ids)
    report["orphan_relationships_removed"] = n

    relationships, n = validate_relationship_directions(relationships, entity_map)
    report["invalid_direction_relationships_removed"] = n

    # Final dedup (some relationships may now be identical after entity removal)
    seen: Set[tuple] = set()
    unique_rels: List[Dict] = []
    for rel in relationships:
        key = (rel["source_id"], rel["target_id"], rel["type"])
        if key not in seen:
            seen.add(key)
            unique_rels.append(rel)
    report["duplicate_relationships_removed"] = len(relationships) - len(unique_rels)
    relationships = unique_rels

    # --- Summary ---

    total_entities_removed = (
        report["generic_entities_removed"]
        + report["low_value_entities_removed"]
        + report["misclassified_protocols_removed"]
        + report["poor_description_entities_removed"]
    )
    total_relationships_removed = (
        report["orphan_relationships_removed"]
        + report["invalid_direction_relationships_removed"]
        + report["duplicate_relationships_removed"]
    )

    report["entities_remaining"] = len(entities)
    report["relationships_remaining"] = len(relationships)

    logger.info(
        "Validation complete: %d entities, %d relationships remaining "
        "(removed %d entities, %d relationships total)",
        len(entities),
        len(relationships),
        total_entities_removed,
        total_relationships_removed,
    )

    return entities, relationships, report
