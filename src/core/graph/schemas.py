"""
Entity and Relationship Schemas
================================

Pydantic models for LLM-extracted entities and relationships.
Exports JSON Schema for vLLM's response_format parameter, which uses
grammar-constrained decoding to guarantee valid output.

The schema is intentionally restrictive (closed enums for labels
and relationship types) because smaller models perform better with
explicit constraints than open-ended extraction.

Enums:
    EntityLabel             Allowed entity type labels (DoD STD-2167A hierarchy)
    RelationshipType        Allowed relationship type labels

Models:
    ExtractedEntity         A single entity extracted from chunk text
    ExtractedRelationship   A relationship between two extracted entities
    ExtractionResult        Complete extraction output for a single chunk

Constants:
    EXTRACTION_SCHEMA       JSON Schema dict for vLLM response_format
"""

from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field


class EntityLabel(str, Enum):
    # Software architecture (DoD STD-2167A hierarchy)
    SYSTEM = "System"
    CSCI = "CSCI"
    CSC = "CSC"
    CSU = "CSU"
    INTERFACE = "Interface"
    DATA_STORE = "DataStore"
    FUNCTION = "Function"
    REQUIREMENT = "Requirement"
    EXTERNAL_SYSTEM = "ExternalSystem"
    MESSAGE = "Message"
    PROTOCOL = "Protocol"
    # Organizational / contextual
    PERSON = "Person"
    ORGANIZATION = "Organization"
    STANDARD = "Standard"
    LANGUAGE = "Language"


class RelationshipType(str, Enum):
    CONTAINS = "CONTAINS"
    INTERFACES_WITH = "INTERFACES_WITH"
    READS_FROM = "READS_FROM"
    WRITES_TO = "WRITES_TO"
    IMPLEMENTS = "IMPLEMENTS"
    DEPENDS_ON = "DEPENDS_ON"
    SENDS = "SENDS"
    RECEIVES = "RECEIVES"
    CALLS = "CALLS"
    DEVELOPED_BY = "DEVELOPED_BY"
    SPONSORED_BY = "SPONSORED_BY"
    REFERENCES = "REFERENCES"


class ExtractedEntity(BaseModel):
    """A single entity extracted from chunk text."""
    name: str = Field(description="Canonical name of the entity")
    label: EntityLabel = Field(description="Entity type")
    description: str = Field(
        description="One-sentence description of the entity's role or purpose",
    )


class ExtractedRelationship(BaseModel):
    """A relationship between two extracted entities."""
    source: str = Field(description="Name of the source entity (must match an entity name)")
    target: str = Field(description="Name of the target entity (must match an entity name)")
    type: RelationshipType = Field(description="Relationship type")


class ExtractionResult(BaseModel):
    """Complete extraction output for a single chunk."""
    entities: List[ExtractedEntity] = Field(
        default_factory=list,
        description="Entities found in this chunk",
    )
    relationships: List[ExtractedRelationship] = Field(
        default_factory=list,
        description="Relationships between entities in this chunk",
    )


# JSON Schema dict for vLLM's response_format parameter.
# vLLM uses this for grammar-constrained decoding at the token level,
# guaranteeing the output matches this schema exactly.
EXTRACTION_SCHEMA = ExtractionResult.model_json_schema()
