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
