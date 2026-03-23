"""
Entity Extraction via vLLM
==========================

Async extraction of entities and relationships from document chunks
using a local LLM served by vLLM.

Design decisions:
- aiohttp + asyncio for async HTTP (vLLM handles continuous batching)
- Semaphore-controlled concurrency (default 32) to avoid overwhelming vLLM
- response_format with json_schema for grammar-constrained output
- Simple, explicit prompts with examples
- Acronym expansion as pre-processing before sending to LLM

Prompt Construction:
    SYSTEM_PROMPT          System prompt defining entity/relationship types and rules
    _build_user_prompt     Build the per-chunk user prompt with context

Single-Chunk Extraction:
    extract_single_chunk   Send one chunk to vLLM and parse the structured response

Batch Orchestration:
    extract_all_chunks     Process all chunks with batching and entity registry updates
    run_extraction_sync    Synchronous wrapper for use from Flask
"""

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

import aiohttp

from config import settings
from .acronyms import expand_acronyms
from .schemas import EXTRACTION_SCHEMA, ExtractionResult
from .resolution import EntityRegistry

logger = logging.getLogger(__name__)


# ============================================
# Prompt Construction
# ============================================

SYSTEM_PROMPT = """\
You are a technical document analyst specializing in DoD software design documents. \
Extract entities and relationships from the given text.

Entity types (use the most specific label that fits):
- System: A top-level software system or application. Must be \
a named software system, NOT hardware.
- CSCI: Computer Software Configuration Item — a major software component
- CSC: Computer Software Component — a sub-component within a CSCI
- CSU: Computer Software Unit — a low-level unit (function, class, module)
- Interface: A defined interface between components or systems
- DataStore: A database, file, log, message queue, or data repository. NOT individual \
data fields, columns, or variables within a store.
- Function: A named software function, capability, or processing step
- Requirement: A requirement identifier (e.g., SRS-1234, REQ-001)
- ExternalSystem: An external system that exchanges data with the software
- Message: A message or data packet exchanged between components
- Protocol: A communication protocol (e.g., TCP/IP, RS-232)
- Person: A specific named individual (e.g., "Dr. Jane Smith"). NOT generic roles \
like "user", "author", or "operator".
- Organization: A company, agency, laboratory, or program office
- Standard: A referenced standard, specification, or regulatory document
- Language: A programming language or development framework (e.g., Java, C#, Ada 95). \
NOT file formats (CSV, BMP), units (GB, MHz), or arbitrary acronyms.

Relationship types:
- CONTAINS: Parent contains child (system contains CSCI, CSCI contains CSC)
- INTERFACES_WITH: Two components exchange data through an interface
- READS_FROM: Component reads from a data store (target MUST be a DataStore)
- WRITES_TO: Component writes to a data store (target MUST be a DataStore)
- IMPLEMENTS: Software component implements a requirement or function
- DEPENDS_ON: Component depends on another component to operate
- SENDS: Component sends a message
- RECEIVES: Component receives a message
- CALLS: Component calls a function or another component
- DEVELOPED_BY: Software is developed by a person or organization. The SOURCE is \
the thing that was built, the TARGET is who built it (Person or Organization).
- SPONSORED_BY: Project is sponsored or funded by an organization
- REFERENCES: Document or component references a standard or requirement

Rules:
1. Only extract entities explicitly mentioned in the text
2. Use full canonical names — prefer "Minutia Deviation Tool (MDT)" over just "MDT"
3. Every entity MUST have a description — one sentence explaining what it is or does
4. Each relationship must reference entities you listed by exact name
5. Do NOT extract generic nouns (e.g., "users", "data", "system", "author(s)", \
"operator") as entities. Only extract named, specific things.
6. Do NOT extract section headings, document structure, or boilerplate as entities
7. If no entities are found, return empty lists
8. Do NOT extract individual data fields, column names, coordinate values, pixel \
values, or measurement parameters as DataStore entities. A "Session Database" is \
a DataStore; "X coordinate" or "threshold value" is NOT.
9. Do NOT extract hardware components (processors, memory modules, hard drives, \
monitors) as System entities. System is for software systems only.
10. Do NOT extract file format names (CSV, BMP, TIFF), units of measurement (GB, \
MB, Ppi, dpi), or arbitrary acronyms as Language entities.
11. Relationship direction matters. For DEVELOPED_BY: the SOURCE is the software that \
was built, the TARGET is the Person or Organization. Example: "APP DEVELOPED_BY \
Apex Defense Corp" is correct. "Apex Defense Corp DEVELOPED_BY APP" is wrong.
12. Prefer extracting at the CSCI/CSC/Function level, not at the level of individual \
variables, parameters, or data fields.

Example:

Input text: "The Alpha Processing Platform (APP) is a CSCI developed by Apex Defense \
Corp under contract to the Federal Systems Directorate (FSD). APP contains two CSCs: \
the Data Ingest Handler and the Report Generator. The Data Ingest Handler reads \
records from the Observation Database and writes summaries to the Audit Trail. The \
software is implemented in C++."

Correct output:
{"entities": [
  {"name": "Alpha Processing Platform (APP)", "label": "CSCI", "description": \
"A Computer Software Configuration Item that ingests and processes observation data."},
  {"name": "Data Ingest Handler", "label": "CSC", "description": \
"A CSC within APP responsible for reading and processing incoming data records."},
  {"name": "Report Generator", "label": "CSC", "description": \
"A CSC within APP responsible for producing summary reports from processed data."},
  {"name": "Observation Database", "label": "DataStore", "description": \
"A database that stores observation records read by APP."},
  {"name": "Audit Trail", "label": "DataStore", "description": \
"A log where APP writes processing summaries for auditing purposes."},
  {"name": "Apex Defense Corp", "label": "Organization", "description": \
"Defense contractor that developed APP."},
  {"name": "Federal Systems Directorate (FSD)", "label": "Organization", \
"description": "Government directorate that contracted the APP development."},
  {"name": "C++", "label": "Language", "description": \
"Programming language used to implement APP."}
], "relationships": [
  {"source": "Alpha Processing Platform (APP)", "target": "Data Ingest Handler", \
"type": "CONTAINS"},
  {"source": "Alpha Processing Platform (APP)", "target": "Report Generator", \
"type": "CONTAINS"},
  {"source": "Data Ingest Handler", "target": "Observation Database", \
"type": "READS_FROM"},
  {"source": "Data Ingest Handler", "target": "Audit Trail", "type": "WRITES_TO"},
  {"source": "Alpha Processing Platform (APP)", "target": "Apex Defense Corp", \
"type": "DEVELOPED_BY"},
  {"source": "Alpha Processing Platform (APP)", "target": \
"Federal Systems Directorate (FSD)", "type": "SPONSORED_BY"}
]}"""


def _build_user_prompt(
    chunk_content: str,
    chunk_title: str,
    chunk_number: str,
    known_entities: str,
) -> str:
    """Build the user prompt for a single chunk extraction."""
    return f"""\
Section {chunk_number}: {chunk_title}

Known entities from other sections (reuse these names if the same entity appears):
{known_entities}

Text to analyze:
\"\"\"{chunk_content}\"\"\"\

Extract all entities and relationships from the text above."""


# ============================================
# Single-Chunk Extraction
# ============================================


async def extract_single_chunk(
    session: aiohttp.ClientSession,
    semaphore: asyncio.Semaphore,
    chunk: Dict[str, Any],
    chunk_index: int,
    known_entities_prompt: str,
    acronym_map: Dict[str, str],
) -> Dict[str, Any]:
    """
    Extract entities from a single chunk via vLLM.

    Returns a dict with the chunk's metadata plus extracted entities
    and relationships. On failure, returns empty lists with an error field.
    """
    content = chunk.get("content", "")
    if not content or len(content.strip()) < 20:
        return {
            "chunk_index": chunk_index,
            "chunk_number": chunk.get("number", ""),
            "chunk_type": chunk.get("type", ""),
            "entities": [],
            "relationships": [],
            "skipped": True,
        }

    # Pre-process: expand acronyms
    expanded_content = expand_acronyms(content, acronym_map)

    user_prompt = _build_user_prompt(
        chunk_content=expanded_content,
        chunk_title=chunk.get("title", ""),
        chunk_number=chunk.get("number", ""),
        known_entities=known_entities_prompt,
    )

    payload = {
        "model": settings.VLLM_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": settings.VLLM_MAX_TOKENS,
        "temperature": settings.VLLM_TEMPERATURE,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "extraction_result",
                "schema": EXTRACTION_SCHEMA,
            },
        },
    }

    async with semaphore:
        try:
            async with session.post(
                f"{settings.VLLM_BASE_URL}/chat/completions",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=settings.VLLM_TIMEOUT),
            ) as response:
                if response.status != 200:
                    error_text = await response.text()
                    logger.error(
                        "vLLM error for chunk %s: %s %s",
                        chunk_index, response.status, error_text,
                    )
                    return {
                        "chunk_index": chunk_index,
                        "chunk_number": chunk.get("number", ""),
                        "chunk_type": chunk.get("type", ""),
                        "entities": [],
                        "relationships": [],
                        "error": f"vLLM returned {response.status}",
                    }

                result = await response.json()

        except asyncio.TimeoutError:
            logger.warning("Timeout extracting chunk %s", chunk_index)
            return {
                "chunk_index": chunk_index,
                "chunk_number": chunk.get("number", ""),
                "chunk_type": chunk.get("type", ""),
                "entities": [],
                "relationships": [],
                "error": "timeout",
            }
        except Exception as exc:
            logger.warning("Error extracting chunk %s: %s", chunk_index, exc)
            return {
                "chunk_index": chunk_index,
                "chunk_number": chunk.get("number", ""),
                "chunk_type": chunk.get("type", ""),
                "entities": [],
                "relationships": [],
                "error": str(exc),
            }

    # Parse the LLM response
    try:
        content_str = result["choices"][0]["message"]["content"]
        extraction = ExtractionResult.model_validate_json(content_str)
        return {
            "chunk_index": chunk_index,
            "chunk_number": chunk.get("number", ""),
            "chunk_type": chunk.get("type", ""),
            "entities": [e.model_dump() for e in extraction.entities],
            "relationships": [r.model_dump() for r in extraction.relationships],
        }
    except Exception as exc:
        logger.warning(
            "Failed to parse extraction for chunk %s: %s", chunk_index, exc
        )
        return {
            "chunk_index": chunk_index,
            "chunk_number": chunk.get("number", ""),
            "chunk_type": chunk.get("type", ""),
            "entities": [],
            "relationships": [],
            "error": f"parse_error: {exc}",
        }


# ============================================
# Batch Orchestration
# ============================================


async def extract_all_chunks(
    chunks: List[Dict[str, Any]],
    acronym_map: Dict[str, str],
    concurrency: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """
    Extract entities from all chunks with semaphore-controlled concurrency.

    Processes in batches of EXTRACTION_BATCH_SIZE. Between batches, updates
    the entity registry so later chunks see entities found in earlier ones.
    """
    if concurrency is None:
        concurrency = settings.EXTRACTION_CONCURRENCY

    semaphore = asyncio.Semaphore(concurrency)
    registry = EntityRegistry()
    all_results: List[Dict[str, Any]] = []
    batch_size = settings.EXTRACTION_BATCH_SIZE

    async with aiohttp.ClientSession() as session:
        for batch_start in range(0, len(chunks), batch_size):
            batch_end = min(batch_start + batch_size, len(chunks))
            batch = chunks[batch_start:batch_end]

            known_prompt = registry.get_known_entities_prompt()

            tasks = [
                extract_single_chunk(
                    session=session,
                    semaphore=semaphore,
                    chunk=chunk,
                    chunk_index=batch_start + i,
                    known_entities_prompt=known_prompt,
                    acronym_map=acronym_map,
                )
                for i, chunk in enumerate(batch)
            ]

            batch_results = await asyncio.gather(*tasks)

            # Register discovered entities for the next batch
            for result in batch_results:
                chunk_id = f"{result['chunk_number']}_{result['chunk_index']}"
                for entity in result.get("entities", []):
                    registry.register(
                        name=entity["name"],
                        label=entity["label"],
                        description=entity.get("description"),
                        chunk_id=chunk_id,
                    )

            all_results.extend(batch_results)
            logger.info(
                "Extracted batch %d-%d of %d chunks (%d entities so far)",
                batch_start, batch_end, len(chunks), len(registry),
            )

    return all_results


def run_extraction_sync(
    chunks: List[Dict[str, Any]],
    acronym_map: Dict[str, str],
) -> List[Dict[str, Any]]:
    """Synchronous wrapper for extract_all_chunks (for use from Flask)."""
    return asyncio.run(extract_all_chunks(chunks, acronym_map))
