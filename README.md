# utilities

# RAG Chatbot Architecture

How we ground LLM responses in our own documents.

---

## The Core Idea

LLMs only know what they were trained on, and they'll confidently make things up when they don't know something. RAG fixes that—before the model generates a response, we retrieve relevant documents and inject them into the prompt. Now the model reads our content instead of guessing.

```
User Question  →  Search Documents  →  Inject Context  →  Generate Response
```

The model isn't guessing. It's reading what we gave it and synthesizing an answer from that.

---

## Hybrid Search

No single search method catches everything. We run two in parallel and merge the results.

| | Vector Search (FAISS) | BM25 |
|---|---|---|
| **What it does** | Semantic similarity | Lexical similarity |
| **How it works** | Embeds documents into vectors where meaning is spatial. Similar concepts cluster together. Finds nearest neighbors to the query. | Ranks by exact term matches, weighted by frequency and document length. |
| **Catches** | Conceptual matches when words differ | Precise keyword matches |
| **Example query** | "How do I fix my automobile?" | "Error code 0x8007" |
| **Matches** | Documents about "car" and "vehicle" | Only documents containing "0x8007" |
| **Misses** | Exact codes, IDs, specific strings | Paraphrases, synonyms |

We run both, normalize the scores, and merge.

---

## Vector Search — How It Works

Documents get embedded into a high-dimensional space where meaning is encoded geometrically. Similar concepts cluster together.

```
                    ┌─────────────────────────────────┐
                    │                                 │
                    │    ·pasta    ·spaghetti        │
                    │        (food cluster)           │
                    │                                 │
     ·car                                             │
     ·automobile         ·code   ·script             │
     ·vehicle            (programming cluster)        │
     (vehicle cluster)                                │
                                                      │
     ⊙ query:"truck"                                  │
       └──→ nearest: "automobile"                     │
                    │                                 │
                    └─────────────────────────────────┘
```

When a query comes in, we embed it and find the nearest neighbors. FAISS handles this lookup efficiently at scale.

---

## MMR — Eliminating Redundancy

Pure relevance ranking might return five documents saying the same thing. MMR re-ranks by penalizing similarity to already-selected documents.

| Pure Relevance (bad) | With MMR (good) |
|---|---|
| Setup guide v1 | Setup guide |
| Setup guide v2 | Troubleshooting |
| Installation steps | Config options |
| Getting started | API reference |

Same topic, different angles. Coverage instead of repetition.

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

## Component Summary

| Component | Role |
|---|---|
| **Flask App** | Frontend. Takes input, orchestrates retrieval, builds prompt, returns response. |
| **FAISS** | Vector search. Semantic similarity via embeddings. |
| **BM25** | Lexical search. Exact keyword matching with smart weighting. |
| **MMR** | Re-ranks merged results to maximize diversity. |
| **vLLM** | Serves the model. Optimized for throughput via PagedAttention. |
| **System Prompt** | Tells the model how to behave: use context, cite sources, admit uncertainty. |

---
---

# Current Architecture — Limitations

Why we're rebuilding the RAG pipeline.

---

## The Problems

Our current architecture works for small corpora and simple queries. It breaks down at scale and can't handle complex information needs. Here's what's wrong.

---

## 1. Always Retrieves — Even When It Shouldn't

The pipeline is hardcoded so every query triggers retrieval. User says "hi" or "what's 2+2" and we still hit the vector store.

```
User: "Hello"
                    ↓
        ┌───────────────────┐
        │  Search Documents │  ← Why?
        └───────────────────┘
                    ↓
        Context: "Chapter 3: System Configuration..."
                    ↓
        Model: "Hello! I see you're interested in system configuration..."
```

Irrelevant context gets injected and responses become confused or overly formal when they should be simple. There's no decision layer asking "does this query even need retrieval?" — we just always do it.

---

## 2. Single-Pass — No Self-Correction

One retrieval. One generation. Done.

```
Current:    Query → Retrieve → Generate → Done

Should be:  Query → Plan → Retrieve → Check → Retrieve again? → Generate
```

The model can't say "this context doesn't answer the question, let me search for something else." It can't break a complex question into parts and retrieve for each. It gets one shot and has to work with whatever context it got, even if it's insufficient. Complex queries get incomplete answers because there's no iteration.

---

## 3. Junk Chunks From Bad Parsing

Our current parsing doesn't handle document structure well. Tables become garbled text. Headers merge with body content. Footers appear mid-sentence. Multi-column layouts concatenate wrong.

A clean table with 3 columns becomes `"Name Revenue Q1 Acme 50M Q2 Beta 30M Q3..."`. Section headers smash into paragraphs. Two-column layouts get interleaved line by line.

Retrieval surfaces this junk, and generation hallucinates around it. Garbage in, garbage out.

---

## 4. No Relational Understanding

Vector search treats every chunk as isolated. It doesn't know that Document A references Document B, or that Entity X appears in five documents with different contexts. It can't tell when one chunk contradicts another, or when three chunks describe the same event from different angles.

```
         Chunk 1          Chunk 2          Chunk 3
            ·                ·                ·
            
            (no connections — just floating points in space)
```

We retrieve by similarity, not by meaning. Related information stays disconnected and the model can't reason across documents.

---

## 5. More Documents = More Confusion

As the corpus grows, retrieval precision drops. The noise floor rises.

With 100 docs we get good matches and low noise. At 1,000 some false positives start creeping in. By 10,000 good matches are competing with similar-but-wrong chunks. At 100,000+ it's a needle in a haystack — relevant chunks get crowded out by almost-right content that confuses the model.

The problem isn't that we can't find the answer. It's that we retrieve too much almost-right content. Scaling the corpus makes the system worse, not better. That's backwards.

---

## Next

See **[Next Architecture — Plan and Execute Graph RAG]** for how we're solving these.

---
---

# Next Architecture — Plan and Execute Graph RAG

How we're solving the limitations of the current pipeline.

---

## Overview

The new architecture has three major changes. First, we're adding agentic control through a Plan and Execute pattern that decides if and what to retrieve. Second, we're replacing flat vector search with Neo4j so we get structured relationships instead of just similarity scores. Third, we're building new parsers with fitz (PyMuPDF) to actually understand document structure.

```
┌─────────────────────────────────────────────────────────────────┐
│                                                                 │
│    User Query                                                   │
│         │                                                       │
│         ▼                                                       │
│    ┌─────────┐                                                  │
│    │  PLAN   │  ← Decides: retrieve? how many steps? what to    │
│    └────┬────┘    search for?                                   │
│         │                                                       │
│         ▼                                                       │
│    ┌─────────┐        ┌─────────┐                               │
│    │ EXECUTE │ ──────→│  Neo4j  │  ← Graph traversal, not just  │
│    └────┬────┘        └─────────┘    vector similarity          │
│         │                                                       │
│         ▼                                                       │
│    ┌─────────┐                                                  │
│    │ REPLAN? │  ← Check: do we have enough? need more?          │
│    └────┬────┘                                                  │
│         │                                                       │
│         ▼                                                       │
│      Response                                                   │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

Here's how each piece solves the problems from the current architecture.

---

## 1. Plan and Execute — Agentic Control

Instead of blindly retrieving, the model first makes a plan. The plan step decides whether this query needs retrieval at all, what specific information we need, how many retrieval steps to take, and what search queries to run. Then the execute step runs each planned retrieval, checks if results are sufficient, and can trigger re-planning if not.

So when a user asks "What's 2+2?" the plan is simple: no retrieval needed, answer directly, done. But when someone asks "How does our auth system handle token refresh?" the plan becomes: search for "auth token refresh", then search for "session management", then synthesize. If the results mention OAuth but not our specific implementation, we replan and add another search for "OAuth integration" before generating the final answer.

Simple queries stay simple. Complex queries get the iteration they need. This solves both the "always retrieves" problem and the "no self-correction" problem in one pattern.

---

## 2. Neo4j Graph RAG — Structured Relationships

Instead of flat vector similarity, we traverse a knowledge graph.

Right now when someone asks "What issues has Acme Corp reported?" vector search returns chunks that mention "Acme" with high similarity, chunks that mention "Corp" that might be a different company, and chunks about "issues" that are completely unrelated. We're guessing based on word proximity.

With graph traversal, Acme Corp is an entity node. That node has REPORTED edges connecting it to Auth Bug #1234, Rate Limit Issue #1567, and API Timeout #1892. We traverse those edges and get exactly what we need.

```
Query: "What issues has Acme Corp reported?"

Graph traversal:
  Acme Corp (entity)
      │
      ├── REPORTED → Auth Bug #1234
      ├── REPORTED → Rate Limit Issue #1567
      └── REPORTED → API Timeout #1892
      
Returns: All three issues with full context
```

This scales because entities are deduplicated — "Acme Corp" is one node, not scattered across chunks. Relationships are explicit so we traverse edges instead of guessing at similarity. And queries are constrained by the graph structure which limits the search space. More documents means a richer graph, not more noise.

---

## 3. Graph Construction — Still Deciding

The documents are standalone, so relationships aren't explicit in the structure. I'm evaluating two approaches.

Entity extraction runs each chunk through the model with a prompt to extract entities and relationships. It's flexible and can find implicit connections, but it's expensive and slower at indexing time.

Metadata-based parsing looks at document structure like headings, tags, and explicit references, then builds the graph from that. It's cheaper and faster but limited to structure that's already explicit in the documents.

Decision pending based on what our corpus actually looks like and what latency we can tolerate at indexing time.

---

## 4. fitz Parser — Clean Chunks

We're replacing current parsing with fitz (PyMuPDF) for precise control over extraction.

fitz gives us layout detection so we can distinguish headers from body text from tables from columns. It extracts tables as structured data instead of garbled strings. It gives us block-level control so we decide how to chunk, not the library. And it's aware of fonts and styles which we can use as semantic signals.

Where our current parser produces `"3.1 Installation Run the following command to install..."`, fitz separates that into a header block "3.1 Installation" and a body block "Run the following command to install..."

Clean chunks in, clean retrieval out. No more junk polluting the index.

---

## What Changes

Currently we have a hardcoded pipeline that always retrieves in a single pass, searches by vector similarity through FAISS and BM25, stores everything as flat chunks, and uses basic extraction for parsing. It degrades as we scale.

The new architecture is agentic with Plan and Execute controlling whether and how we retrieve. Retrieval becomes conditional and iterative. Search becomes graph traversal through Neo4j combined with vector search. Storage becomes an entity graph with explicit relationships. Parsing becomes layout-aware through fitz. And critically, it maintains precision as we scale instead of degrading.

---

## Next Steps

1. Finalize entity extraction vs. metadata approach
2. Build fitz parsing pipeline
3. Design Neo4j schema
4. Implement Plan and Execute agent
5. Benchmark against current architecture
