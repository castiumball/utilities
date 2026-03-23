"""
Entity Resolution
=================

Resolves and deduplicates entities extracted from document chunks.
Four resolution stages ensure that the same real-world entity is
represented by a single canonical node in the knowledge graph.

Name Matching Helpers (private):
    _is_acronym_of              Check if one name is an acronym of another
    _normalize_for_matching     Strip formatting/articles for fuzzy comparison
    _abbreviation_or_subset_match  Detect abbreviation or token-subset overlap

ID Generation:
    generate_entity_id          Deterministic ID from label + normalized name

Cross-Chunk Entity Registry:
    EntityRegistry              Tracks known entities across extraction chunks;
                                provides LLM prompt context and post-extraction
                                entity listing

Deduplication:
    fuzzy_deduplicate           Merge near-duplicate entities (fuzzy, acronym,
                                abbreviation, and cross-label strategies)
    apply_merge_map             Rewrite relationship IDs after deduplication
"""

import logging
import re
from typing import Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


# ============================================
# Name Matching Helpers
# ============================================


def _is_acronym_of(short: str, long: str) -> bool:
    """
    Check if `short` is a plausible acronym of `long`.

    Matches cases like:
        "MDT" -> "Minutia Deviation Tool"
        "MDT" -> "Minutia Deviation Tool (MDT)"
        "EBTS" -> "Electronic Biometric Transmission Specification"
        "NIJ SSBT CoE" -> "National Institute of Justice (NIJ) SSBT CoE"

    Strategy: extract capital letters / leading word characters from `long`
    and check if they form the `short` string.
    """
    short_clean = re.sub(r"[^A-Za-z0-9]", "", short).upper()
    if len(short_clean) < 2 or len(short_clean) >= len(long):
        return False

    # Check if the short name appears in parentheses in the long name
    # e.g., "Minutia Deviation Tool (MDT)" contains "(MDT)"
    paren_match = re.search(r"\(([^)]+)\)", long)
    if paren_match:
        paren_content = re.sub(r"[^A-Za-z0-9]", "", paren_match.group(1)).upper()
        if paren_content == short_clean:
            return True

    # Build acronym from first letter of each significant word
    words = re.findall(r"[A-Z][a-z]*|[A-Z]+(?=[A-Z][a-z]|\b)", long)
    if not words:
        words = long.split()
    # Skip short filler words when building acronym
    skip = {"of", "the", "and", "for", "in", "on", "a", "an", "to", "or", "by"}
    initials = "".join(
        w[0].upper() for w in long.split() if w.lower() not in skip and len(w) > 0
    )

    return initials == short_clean


def _normalize_for_matching(name: str) -> str:
    """
    Normalize an entity name for fuzzy matching comparison.

    Transformations applied:
    1. Strip markdown formatting: "_Baseline Fingerprint_" -> "Baseline Fingerprint"
    2. Strip parenthetical content: "Minutia Deviation Tool (MDT)" -> "Minutia Deviation Tool"
    3. Strip leading articles: "The MDT System" -> "MDT System"
    4. Collapse whitespace and strip

    This improves fuzzy match scores for names that differ only by
    markdown formatting, an appended acronym, or a leading article.
    """
    # Strip markdown italic/bold markers (leading/trailing underscores and asterisks)
    normalized = re.sub(r"^[_*]+|[_*]+$", "", name)

    # Remove parenthetical content (e.g., "(MDT)", "(GUI)")
    normalized = re.sub(r"\s*\([^)]*\)\s*", " ", normalized)

    # Remove leading articles
    normalized = re.sub(r"^(the|a|an)\s+", "", normalized, flags=re.IGNORECASE)

    # Collapse whitespace and strip
    normalized = re.sub(r"\s+", " ", normalized).strip()

    return normalized


def _abbreviation_or_subset_match(name_a: str, name_b: str) -> bool:
    """
    Check if two names refer to the same entity via abbreviation or
    token overlap. Catches cases that fuzzy matching and acronym
    detection miss.

    Two strategies:

    1. **Parenthetical abbreviation**: every significant token of the
       shorter name matches either a direct token or a parenthetical
       abbreviation in the longer name.
       "MS Excel" ↔ "Microsoft (MS) Excel"
       "NIJ SSBT CoE" ↔ "National Institute of Justice (NIJ) ... (SSBT) ... (CoE)"

    2. **Token subset**: all significant tokens of the shorter name
       appear directly in the longer name, with ≥50% coverage ratio
       to avoid matching unrelated entities that share a few common words.
       "SSBT CoE" ↔ "NIJ SSBT CoE"

    Requires ≥2 significant tokens in the shorter name to prevent
    single-word false positives like "FBI" matching "FBI Universal
    Latent Workstation".
    """
    # Ensure name_a is the shorter one
    if len(name_a) > len(name_b):
        name_a, name_b = name_b, name_a

    _filler = {"of", "the", "and", "for", "in", "on", "a", "an", "to", "or", "by"}

    # Extract parenthetical abbreviations from the longer name
    parens = re.findall(r"\(([^)]+)\)", name_b)
    paren_tokens = {p.strip().lower() for p in parens}

    # Significant tokens from shorter name
    short_tokens = {
        t.lower() for t in re.findall(r"[A-Za-z0-9]+", name_a)
        if t.lower() not in _filler
    }

    # Significant tokens from longer name (excluding parenthetical content)
    long_cleaned = re.sub(r"\([^)]*\)", "", name_b)
    long_tokens = {
        t.lower() for t in re.findall(r"[A-Za-z0-9]+", long_cleaned)
        if t.lower() not in _filler
    }

    if len(short_tokens) < 2:
        return False

    # Strategy 1: Parenthetical abbreviation matching
    # Every short token must match either a long token or a parenthetical
    if paren_tokens:
        all_matched = all(
            token in long_tokens or token in paren_tokens
            for token in short_tokens
        )
        if all_matched:
            return True

    # Strategy 2: Token subset with coverage check
    # All short tokens must appear in the long name's tokens, and must
    # account for at least half the long name to avoid false positives
    if short_tokens.issubset(long_tokens):
        coverage = len(short_tokens) / max(len(long_tokens), 1)
        if coverage >= 0.5:
            return True

    return False


# ============================================
# ID Generation
# ============================================


def generate_entity_id(label: str, name: str) -> str:
    """
    Generate a deterministic entity ID from label and name.

    Normalization: lowercase, strip, replace non-alphanumeric with
    underscores, collapse consecutive underscores.

    Examples:
        ("CSCI", "CSCI-1") -> "csci_csci_1"
        ("System", "STARS") -> "system_stars"
    """
    normalized = re.sub(r"[^a-z0-9]+", "_", name.lower().strip())
    normalized = normalized.strip("_")
    prefix = label.lower()
    return f"{prefix}_{normalized}"


# ============================================
# Cross-Chunk Entity Registry
# ============================================


class EntityRegistry:
    """
    Tracks entities across chunks during extraction.

    Two purposes:
    1. Build a 'known entities' list for LLM prompts so the model
       references existing entities instead of creating duplicates.
    2. Post-extraction deduplication via fuzzy matching.
    """

    def __init__(self):
        self._entities: Dict[str, Dict] = {}  # id -> {name, label, description, chunk_ids}

    def register(
        self,
        name: str,
        label: str,
        description: Optional[str] = None,
        chunk_id: Optional[str] = None,
    ) -> str:
        """Register an entity or update if already known. Returns entity ID."""
        entity_id = generate_entity_id(label, name)

        if entity_id in self._entities:
            existing = self._entities[entity_id]
            if description and not existing.get("description"):
                existing["description"] = description
            if chunk_id:
                existing["chunk_ids"].add(chunk_id)
        else:
            self._entities[entity_id] = {
                "name": name,
                "label": label,
                "description": description,
                "chunk_ids": {chunk_id} if chunk_id else set(),
            }

        return entity_id

    def get_known_entities_prompt(self, max_entities: int = 50) -> str:
        """
        Build a compact string of known entities for LLM prompt context.

        Limits to max_entities to keep prompt small for 8B model.
        Prioritizes entities seen in more chunks.
        """
        if not self._entities:
            return "No entities found yet."

        sorted_entities = sorted(
            self._entities.values(),
            key=lambda e: len(e["chunk_ids"]),
            reverse=True,
        )[:max_entities]

        lines = []
        for e in sorted_entities:
            lines.append(f"- {e['name']} [{e['label']}]")
        return "\n".join(lines)

    def get_all_entities(self) -> List[Dict]:
        """Return all registered entities as serializable dicts."""
        result = []
        for entity_id, data in self._entities.items():
            result.append({
                "id": entity_id,
                "name": data["name"],
                "label": data["label"],
                "description": data["description"],
                "chunk_ids": list(data["chunk_ids"]),
            })
        return result

    def __len__(self) -> int:
        return len(self._entities)


# ============================================
# Deduplication
# ============================================


def fuzzy_deduplicate(
    entities: List[Dict],
    threshold: int = 85,
) -> Tuple[List[Dict], Dict[str, str]]:
    """
    Merge near-duplicate entities using four strategies:

    1. **Fuzzy string match** (within same label): merges names that are
       similar strings (e.g., "MS Windows 7" / "Windows 7").
    2. **Acronym match** (within same label): merges when one name is an
       acronym of the other (e.g., "MDT" / "Minutia Deviation Tool").
    3. **Abbreviation / token subset match**: merges when the shorter
       name's tokens match parenthetical abbreviations or are a subset of
       the longer name's tokens (e.g., "MS Excel" / "Microsoft (MS) Excel",
       "NIJ SSBT CoE" / "National Institute of Justice (NIJ) ... (SSBT) ... (CoE)").
    4. **Cross-label match**: merges entities with the same name but
       different labels — keeps the more specific/architectural label.

    Returns:
        (deduplicated_entities, merge_map) where merge_map maps
        old_id -> canonical_id for merged entities.
    """
    try:
        from rapidfuzz import fuzz
    except ImportError:
        logger.warning("rapidfuzz not installed; skipping fuzzy deduplication")
        return entities, {}

    merge_map: Dict[str, str] = {}

    def _merge_into(winner: Dict, loser: Dict) -> None:
        """Merge loser entity into winner, combining chunk_ids."""
        merge_map[loser["id"]] = winner["id"]
        winner["chunk_ids"] = list(
            set(winner.get("chunk_ids", []))
            | set(loser.get("chunk_ids", []))
        )
        # Keep longer description
        winner_desc = winner.get("description") or ""
        loser_desc = loser.get("description") or ""
        if len(loser_desc) > len(winner_desc):
            winner["description"] = loser_desc

    def _should_merge(name_a: str, name_b: str) -> bool:
        """Check if two names should merge (fuzzy, acronym, or abbreviation match)."""
        # Normalize names (strip parentheticals, articles) for comparison
        norm_a = _normalize_for_matching(name_a)
        norm_b = _normalize_for_matching(name_b)

        # Compare normalized versions
        score = fuzz.token_sort_ratio(norm_a, norm_b)
        if score >= threshold:
            return True

        # Also check original names in case normalization lost useful info
        if norm_a != name_a or norm_b != name_b:
            orig_score = fuzz.token_sort_ratio(name_a, name_b)
            if orig_score >= threshold:
                return True

        # Acronym check: is the shorter one an acronym of the longer?
        if len(name_a) < len(name_b):
            if _is_acronym_of(name_a, name_b):
                return True
        elif len(name_b) < len(name_a):
            if _is_acronym_of(name_b, name_a):
                return True

        # Abbreviation / token subset check (uses original names
        # to preserve parenthetical info for matching)
        return _abbreviation_or_subset_match(name_a, name_b)

    # ----- Stage 1: Within same label (fuzzy + acronym) -----
    by_label: Dict[str, List[Dict]] = {}
    for entity in entities:
        by_label.setdefault(entity["label"], []).append(entity)

    merged_ids: Set[str] = set()
    within_label_result: List[Dict] = []

    for label, group in by_label.items():
        for i, entity_a in enumerate(group):
            if entity_a["id"] in merged_ids:
                continue
            for j in range(i + 1, len(group)):
                entity_b = group[j]
                if entity_b["id"] in merged_ids:
                    continue

                if _should_merge(entity_a["name"], entity_b["name"]):
                    # Keep the one with the longer name (more canonical)
                    if len(entity_a["name"]) >= len(entity_b["name"]):
                        _merge_into(entity_a, entity_b)
                        merged_ids.add(entity_b["id"])
                    else:
                        _merge_into(entity_b, entity_a)
                        merged_ids.add(entity_a["id"])
                        break

            if entity_a["id"] not in merged_ids:
                within_label_result.append(entity_a)

    # ----- Stage 2: Cross-label dedup -----
    # Handles cases like "GUI" appearing as both System and DataStore.
    # Priority: CSCI > CSC > CSU > System > Function > others
    _LABEL_PRIORITY = {
        "CSCI": 0, "CSC": 1, "CSU": 2, "System": 3, "Function": 4,
        "ExternalSystem": 5, "Interface": 6, "DataStore": 7,
        "Organization": 8, "Person": 9, "Standard": 10, "Language": 11,
        "Requirement": 12, "Message": 13, "Protocol": 14,
    }

    cross_merged: Set[str] = set()
    final_result: List[Dict] = []

    for i, entity_a in enumerate(within_label_result):
        if entity_a["id"] in cross_merged:
            continue
        for j in range(i + 1, len(within_label_result)):
            entity_b = within_label_result[j]
            if entity_b["id"] in cross_merged:
                continue
            if entity_a["label"] == entity_b["label"]:
                continue  # already handled in stage 1

            if _should_merge(entity_a["name"], entity_b["name"]):
                # Keep the entity with higher architectural priority
                pri_a = _LABEL_PRIORITY.get(entity_a["label"], 99)
                pri_b = _LABEL_PRIORITY.get(entity_b["label"], 99)
                if pri_a <= pri_b:
                    _merge_into(entity_a, entity_b)
                    cross_merged.add(entity_b["id"])
                else:
                    _merge_into(entity_b, entity_a)
                    cross_merged.add(entity_a["id"])
                    break

        if entity_a["id"] not in cross_merged:
            final_result.append(entity_a)

    if merge_map:
        logger.info(
            "Deduplication merged %d entities (fuzzy + acronym + cross-label)",
            len(merge_map),
        )

    return final_result, merge_map


def apply_merge_map(
    relationships: List[Dict],
    merge_map: Dict[str, str],
) -> List[Dict]:
    """
    Update relationship source/target IDs using the merge map.
    Deduplicates relationships that become identical after merging.
    """
    if not merge_map:
        return relationships

    seen: Set[tuple] = set()
    updated = []
    for rel in relationships:
        source_id = merge_map.get(rel["source_id"], rel["source_id"])
        target_id = merge_map.get(rel["target_id"], rel["target_id"])

        # Skip self-loops created by merging
        if source_id == target_id:
            continue

        key = (source_id, target_id, rel["type"])
        if key not in seen:
            seen.add(key)
            updated.append({
                **rel,
                "source_id": source_id,
                "target_id": target_id,
            })

    return updated
