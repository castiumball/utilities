# utilities

# Polaris Demo Overview

The demo version of Polaris is live as of 1/12/2025. This document gives a high-level overview of how it works and where it falls short.

---

## Prerequisites

**LLM** stands for Large Language Model, a type of AI that processes text. It takes text as input (a question or prompt) and generates text as output (an answer or response). These models are commonly used to answer questions and assist with text-based tasks. Examples include ChatGPT, Gemini, Claude, and Deepseek.

**Tokens** are to LLMs what words are to humans: the building blocks of understanding. For our purposes, think of tokens and words as interchangeable, but know they're technically different.

---

## The Core Idea

LLMs only know what they were trained on, and they'll confidently make things up when they don't know something. RAG (Retrieval Augmented Generation) solves this by using the user's question to retrieve relevant information from documents the LLM wasn't trained on, then injecting that context into the prompt. This way, the model answers based on actual documents rather than guessing.

```
User Question  →  Search Documents  →  Inject Context  →  Generate Response
```

---

## Ingestion

Before we can search our documents, we need to make them searchable. We do this by splitting them into chunks, typically about the size of a paragraph.

Chunk size is a balancing act. Too small and the LLM lacks sufficient context. Too large and the LLM gets overwhelmed with irrelevant information. Mis-sized chunks also cause retrieval problems: they get retrieved too frequently or too rarely, both of which confuse the response.

---

## Hybrid Search

No single search method catches everything. Different methods have different strengths, so we run multiple methods in parallel and merge their results.

### Vector Search

Through a process called embedding, we turn words and sentences into high-dimensional vectors that encapsulate their meaning. This lets us find semantic similarities between the user's question and document chunks by calculating the distance between their embeddings.

### BM25

On STARS, people communicate using acronyms and names that lack semantic meaning. Vector search struggles with these. BM25 (Best Match 25) is a lexical similarity method that directly matches words in the user's question to words in the document chunks. No interpretation, just matching.

### MMR

Ranking purely on relevance often returns a bunch of chunks that all say the same thing. MMR (Maximum Marginal Relevance) re-ranks the results by penalizing similarity to already-selected chunks. This gives the LLM the same topic from different angles rather than redundant repetition.

---

## System Prompt

The system prompt is the markdown file sent to the LLM. It includes:

1. **Purpose:** A description of what it does (e.g., "You are an AI that answers questions based on STARS documents")
2. **Behavior:** How it should act (e.g., "Be friendly, admit when you don't know something, don't make things up")
3. **Conversation history:** The back-and-forth so far, always ending with the most recent user question

As conversations get longer, the prompt grows. There are alternatives like ConversationSummaryBufferMemory that compress older exchanges.

---

## vLLM Endpoint

vLLM is a Python package that serves LLMs at an endpoint, automatically and efficiently handling simultaneous user queries. Two settings matter for troubleshooting:

| Setting | What It Does |
|---------|--------------|
| `max-model-len` | The model's context length: total tokens it can process in a single input (input + output combined). Keep your system prompt short to leave room for actual conversation. Context length is allocated per user, so keep it small enough to allow reasonable concurrency. |
| `gpu-memory-utilization` | Percentage of GPU VRAM the model can use. The two GPUs on gpw132 are dedicated to Polaris, so this is set as high as possible without crashing (~0.85). If other projects share these GPUs, this number needs to decrease. |

---

## Full Architecture

```
                    ┌─────────────────┐
                    │    Flask App    │  ← User interface
                    └────────┬────────┘
                             │ query
                             ▼
              ┌──────────────┴──────────────┐
              │                             │
              ▼                             ▼
     ┌────────────────┐           ┌────────────────┐
     │  FAISS         │           │  BM25          │
     │  (vector)      │           │  (lexical)     │
     └────────┬───────┘           └────────┬───────┘
              │                             │
              └──────────────┬──────────────┘
                             │ merged results
                             ▼
                    ┌─────────────────┐
                    │  MMR Re-ranking │
                    └────────┬────────┘
                             │ context + query
                             ▼
                    ┌─────────────────┐
                    │  vLLM Endpoint  │  ← Model serving
                    └────────┬────────┘
                             │
                             ▼
                    ┌─────────────────┐
                    │  System Prompt  │  ← Behavioral rules
                    └─────────────────┘
```

---

## Limitations

### Retrieval Without Judgment

Polaris retrieves context for every query, regardless of whether retrieval makes sense. When someone says "From now on, write short concise answers," they're setting a preference, not asking a question. But because Polaris always retrieves, it searched the STARS manual for chunks related to "short concise answers" and tried to answer from documentation that had nothing to do with the request.

The problem runs deeper. Polaris uses the user's exact phrasing as the search query, which breaks in predictable ways:

- **Synonym mismatch:** If someone asks about `acronym1` but the documentation uses `acronym2`, the correct chunk won't surface, even if they refer to the same thing.
- **Compound questions get mangled:** A question like "What is ARV? What is STARS?" should trigger two separate searches. Instead, Polaris searches for the entire phrase at once, retrieving chunks that mention both terms together rather than chunks that define each term clearly.

The root cause: the model has no agency in the retrieval process. It doesn't decide *whether* to search, *what* to search for, or *how many* searches to run. It just receives whatever the verbatim query happens to pull back.

### Garbage In, Garbage Out

Polaris occasionally generates complete nonsense. Gibberish, foreign characters, text that trails off into nothing. Two things cause this:

1. **Contaminated chunks:** The parsers that extract text from PDFs don't understand document structure. They grab text in reading order, but PDFs don't store text in reading order. Headers, footers, captions, and multi-column layouts get interleaved with body text. Worse, some extracted "text" isn't text at all. It's font glyphs rendered as Unicode, producing strings like `◆❖✦` that look like language to a language model. When these corrupted chunks enter the context window, the model's next-token prediction latches onto the noise and amplifies it.

2. **Context overflow:** Language models have a fixed context window. When the conversation history plus retrieved chunks approach that limit, the model's behavior becomes unpredictable. Responses may truncate, repeat, or degrade into incoherence. The current architecture has no mechanism to manage this. It just keeps stuffing chunks into the prompt until something breaks.

### Isolated Chunks, Isolated Understanding

The demo treats every chunk as an island. Vector search finds chunks with similar embeddings; BM25 finds chunks with matching keywords. Neither approach understands that Chunk A defines a term that Chunk B references, or that Chunks C, D, and E together describe a workflow that none of them fully explains alone.

This isolation compounds as the knowledge base grows. More documents mean more chunks, which means more competition for retrieval slots. The probability of surfacing the *right* chunk drops with every document added. At scale, Polaris doesn't get smarter. It gets confused.

---
---

# Polaris Version 1 Overview

The demo version of Polaris has three fundamental problems: it retrieves context when it shouldn't, it can't understand relationships between concepts, and poor document parsing poisons everything downstream. This document explains what's broken and how Version 1 fixes it.

---

## Problems with the Demo

### Retrieval Without Judgment

Polaris retrieves context for every query, regardless of whether retrieval makes sense. When someone says "From now on, write short concise answers," they're setting a preference, not asking a question. But because Polaris always retrieves, it searched the STARS manual for chunks related to "short concise answers" and tried to answer from documentation that had nothing to do with the request.

The problem runs deeper. Polaris uses the user's exact phrasing as the search query, which breaks in predictable ways:

- **Synonym mismatch:** If someone asks about `acronym1` but the documentation uses `acronym2`, the correct chunk won't surface, even if they refer to the same thing.
- **Compound questions get mangled:** A question like "What is ARV? What is STARS?" should trigger two separate searches. Instead, Polaris searches for the entire phrase at once, retrieving chunks that mention both terms together rather than chunks that define each term clearly.

The root cause: the model has no agency in the retrieval process. It doesn't decide *whether* to search, *what* to search for, or *how many* searches to run. It just receives whatever the verbatim query happens to pull back.

### Garbage In, Garbage Out

Polaris occasionally generates complete nonsense. Gibberish, foreign characters, text that trails off into nothing. Two things cause this:

1. **Contaminated chunks:** The parsers that extract text from PDFs don't understand document structure. They grab text in reading order, but PDFs don't store text in reading order. Headers, footers, captions, and multi-column layouts get interleaved with body text. Worse, some extracted "text" isn't text at all. It's font glyphs rendered as Unicode, producing strings like `◆❖✦` that look like language to a language model. When these corrupted chunks enter the context window, the model's next-token prediction latches onto the noise and amplifies it.

2. **Context overflow:** Language models have a fixed context window. When the conversation history plus retrieved chunks approach that limit, the model's behavior becomes unpredictable. Responses may truncate, repeat, or degrade into incoherence. The current architecture has no mechanism to manage this. It just keeps stuffing chunks into the prompt until something breaks.

### Isolated Chunks, Isolated Understanding

The demo treats every chunk as an island. Vector search finds chunks with similar embeddings; BM25 finds chunks with matching keywords. Neither approach understands that Chunk A defines a term that Chunk B references, or that Chunks C, D, and E together describe a workflow that none of them fully explains alone.

This isolation compounds as the knowledge base grows. More documents mean more chunks, which means more competition for retrieval slots. The probability of surfacing the *right* chunk drops with every document added. At scale, Polaris doesn't get smarter. It gets confused.

---

## The Version 1 Architecture

Three changes address these problems: agentic control through Plan and Execute, relational structure through a graph database, and clean extraction through custom parsers. These aren't independent fixes. They form an integrated system where each component depends on the others.

```
┌─────────────────────────────────────────────────────────────────┐
│                         USER QUERY                              │
└─────────────────────────────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│                      PLAN AND EXECUTE                           │
│  ┌───────────┐    ┌───────────┐    ┌───────────┐               │
│  │   PLAN    │───▶│  EXECUTE  │───▶│  REPLAN   │──┐            │
│  │           │    │           │    │           │  │            │
│  │ - Decide  │    │ - Run     │    │ - Evaluate│  │            │
│  │   if/what │    │   graph   │    │   results │  │            │
│  │   to      │    │   queries │    │ - Retry   │  │            │
│  │   retrieve│    │ - Collect │    │   if      │◀─┘            │
│  │ - Generate│    │   results │    │   needed  │               │
│  │   search  │    │           │    │           │               │
│  │   queries │    │           │    │           │               │
│  └───────────┘    └───────────┘    └───────────┘               │
└─────────────────────────────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│                      GRAPH DATABASE (Neo4j)                     │
│                                                                 │
│    ┌─────────┐  DEFINES   ┌─────────┐  PART_OF   ┌─────────┐   │
│    │  Chunk  │───────────▶│ Concept │◀───────────│  Chunk  │   │
│    │   A     │            │  "ARV"  │            │    B    │   │
│    └─────────┘            └─────────┘            └─────────┘   │
│                                │                               │
│                           RELATES_TO                           │
│                                │                               │
│                                ▼                               │
│                          ┌─────────┐                           │
│                          │ Concept │                           │
│                          │ "STARS" │                           │
│                          └─────────┘                           │
│                                ▲                               │
│                            DEFINES                             │
│                                │                               │
│                          ┌─────────┐                           │
│                          │  Chunk  │                           │
│                          │    C    │                           │
│                          └─────────┘                           │
└─────────────────────────────────────────────────────────────────┘
                               ▲
                               │
                          populated by
                               │
┌─────────────────────────────────────────────────────────────────┐
│                      FITZ PARSER                                │
│                                                                 │
│  PDF ──▶ Blocks ──▶ Structure Detection ──▶ Clean Chunks       │
│              │                                                  │
│              ├── Headings (by font size/weight)                 │
│              ├── Body text (proper reading order)               │
│              ├── Lists (indentation + markers)                  │
│              ├── Tables (cell boundaries)                       │
│              └── Metadata (page, section, hierarchy)            │
└─────────────────────────────────────────────────────────────────┘
```

The flow: Fitz parses documents into clean, structured chunks. Those chunks feed into Neo4j, where entity extraction identifies concepts and their relationships. When a user asks a question, Plan and Execute decides whether retrieval is needed, generates targeted queries, and traverses the graph to pull back not just matching chunks but related context. The system can self-correct. If the first retrieval isn't sufficient, it replans and tries again.

---

### Plan and Execute

Plan and Execute gives Polaris the judgment it currently lacks. Before any retrieval happens, the model generates a plan: a sequence of steps that may include zero, one, or multiple retrievals depending on what the query actually requires.

**How It Works**

1. **Plan:** The model analyzes the query and produces a structured plan. Each step specifies whether to retrieve, what to search for, and which query to use. Steps are independent. A compound question generates multiple retrieval steps, each with its own targeted query.

2. **Execute:** The system runs each retrieval step against the graph database, collecting results.

3. **Replan:** The model evaluates whether the retrieved information is sufficient. If gaps remain, it generates additional retrieval steps. If the information is complete, it proceeds to response generation.

**Examples**

| Query | Plan | Outcome |
|-------|------|---------|
| "Please respond concisely from here on out." | No retrieval steps | Direct acknowledgment, no search needed |
| "What is ARV?" | One step: search for ARV definition | Single targeted retrieval |
| "What is ARV? What is STARS?" | Two steps: search ARV, search STARS | Parallel retrievals, combined results |
| "How does ARV relate to the STARS workflow?" | One step: traverse ARV→STARS relationship | Graph traversal, not keyword search |

**Safeguards**

- **Step limits:** Plans cannot exceed a fixed number of steps, preventing resource exhaustion from adversarial or malformed queries.
- **Replan limits:** The system can only replan a fixed number of times, preventing infinite loops when no satisfactory answer exists.
- **Context budgeting:** The planner tracks how much context each retrieval adds, stopping before the context window overflows. This directly addresses the "garbage at context limit" problem.

**Resources**

- [Plan-and-Solve Prompting (Wang et al., 2023)](https://arxiv.org/abs/2305.04091)
- [LangGraph Documentation](https://langchain-ai.github.io/langgraph/)

---

### Graph Database

A graph database stores information as nodes and edges rather than rows and columns. This structure lets us capture relationships explicitly: Chunk A *defines* Concept X, which *relates to* Concept Y, which *appears in* Chunk B. Queries can traverse these relationships, surfacing context that keyword or vector search would miss entirely.

**How It Works**

We use Neo4j. During indexing, each chunk passes through an entity extraction prompt that identifies:

- **Entities:** Named concepts, terms, acronyms, procedures, and other significant nouns
- **Relationships:** How entities connect (definitions, dependencies, workflows, hierarchies)

The extraction prompt uses a predefined schema of entity types (Term, Procedure, System, Role, Document) and relationship types (DEFINES, REFERENCES, PART_OF, PRECEDES, DEPENDS_ON). This constrained vocabulary prevents the model from hallucinating arbitrary relationships while still capturing the connections that matter for STARS documentation.

**Example**

Given this chunk:

> "The Automated Reconciliation Validator (ARV) is a module within STARS that compares submitted data against source records. ARV runs nightly after the batch import completes."

Entity extraction produces:

```
Entities:
  - ARV (Term): "Automated Reconciliation Validator"
  - STARS (System): parent system
  - batch import (Procedure): prerequisite process

Relationships:
  - ARV -[PART_OF]-> STARS
  - ARV -[DEPENDS_ON]-> batch import
  - this chunk -[DEFINES]-> ARV
```

**Retrieval Strategies**

| Query Type | Strategy | Example |
|------------|----------|---------|
| Definition lookup | Find chunk that DEFINES the entity | "What is ARV?" → traverse to defining chunk |
| Relationship query | Traverse edges between entities | "How does ARV relate to batch import?" → follow DEPENDS_ON edge |
| Context expansion | Pull chunks connected to retrieved chunk | Found one chunk about ARV → also retrieve chunks about its dependencies |
| Multi-hop reasoning | Chain traversals | "What must complete before ARV can validate my data?" → batch import → its prerequisites |

**Resources**

- [Neo4j Documentation](https://neo4j.com/docs/)
- [Microsoft GraphRAG](https://microsoft.github.io/graphrag/)
- [From Local to Global: A Graph RAG Approach (Edge et al., 2024)](https://arxiv.org/abs/2404.16130)

---

### Fitz Parser

Fitz (the Python binding for MuPDF) extracts document content with positional and styling information intact. Unlike black-box parsers that return flat text, Fitz gives us the raw geometry: where each text block sits on the page, what font it uses, how large the characters are. We use this to reconstruct document structure.

**How It Works**

Fitz represents a PDF page as a hierarchy:

```
Page
  └── Block (text or image region)
        └── Line (horizontal text span)
              └── Span (contiguous text with same formatting)
                    └── Characters (individual glyphs with positions)
```

Each span carries metadata: font name, font size, font flags (bold, italic), color, and bounding box coordinates. We use these signals to classify content:

| Signal | Interpretation |
|--------|----------------|
| Font size > body threshold | Heading |
| Bold + larger size | Section title |
| Fixed-width font | Code or preformatted text |
| Consistent left indent | List or nested content |
| Uniform grid of bounding boxes | Table |
| Unusual Unicode ranges | Potentially corrupted, flag for review |

**What This Fixes**

The demo's parsing produced chunks like:

```
Section 3.2 continued from page 12 STARS User Guide
The validator checks three Required fields include: Name,
conditions before Date, Amount The batch process runs
accepting a submission: nightly See Appendix B
```

Fitz-based parsing produces:

```
## Section 3.2: Validation Rules

The validator checks three conditions before accepting a submission:

1. Required fields include: Name, Date, Amount
2. The batch process runs nightly
3. See Appendix B

[Source: STARS User Guide, Page 12]
```

The first chunk might cause the model to hallucinate connections between sentence fragments. The second chunk preserves meaning, hierarchy, and source attribution.

**Metadata Preservation**

Every chunk carries structured metadata: document title and version, page numbers, section hierarchy (Chapter → Section → Subsection), content type (prose, list, table, code), and extraction confidence score.

This metadata feeds into the graph. A chunk isn't just text. It's a node with typed relationships to its document, section, and neighboring chunks. The planner can use this structure: "Find all chunks from Section 3 of the STARS User Guide" becomes a graph query, not a keyword search.

**Resources**

- [PyMuPDF (Fitz) Documentation](https://pymupdf.readthedocs.io/)
- [PDF Text Extraction Explained](https://pymupdf.readthedocs.io/en/latest/recipes-text.html)

---

## How the Components Connect

**Fitz feeds the graph.** Clean, structured chunks with metadata become graph nodes. Sloppy parsing would pollute the graph with garbage nodes, broken relationships, and unreliable metadata.

**The graph enables intelligent planning.** When the planner generates retrieval steps, it can specify *how* to query: keyword search, vector similarity, relationship traversal, or metadata filter. These options only exist because the graph stores structure, not just text.

**Planning prevents context overflow.** Each retrieval step has a cost (tokens added to context). The planner tracks cumulative cost and stops retrieving before hitting the limit. It can also *choose* retrieval strategies based on cost: a graph traversal that returns three focused chunks beats a vector search that returns ten tangentially related ones.

**Self-correction closes the loop.** If the planner retrieves poorly (wrong chunks, missing context), the replan stage catches it. This only works because the model evaluates the retrieved content against the original query. In the demo, there was no evaluation. Whatever came back went into the prompt.

---

## What's Not Changing

- **Model:** Still using the same base LLM via vLLM
- **Frontend:** Flask interface remains the same
- **Deployment:** Same infrastructure, same access patterns

The changes are architectural, not infrastructural. Users won't see a different interface. They'll see better answers.

---

## Open Questions

1. **Entity extraction cost:** Running every chunk through the model for entity extraction is slow. We may need to batch aggressively or cache extraction results.

2. **Schema evolution:** The predefined entity/relationship types work for STARS documentation. If we add documents from other domains, do we extend the schema or keep it generic?

3. **Retrieval ranking:** When multiple retrieval strategies return results, how do we rank and merge them?

These are implementation decisions, not architectural uncertainties. The approach is sound; the tuning is ongoing.
