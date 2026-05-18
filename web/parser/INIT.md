# AI/ML Learning Plan — Phase 1: Foundations

## Purpose

Build a strong technical foundation in modern ML, especially around large language models, transformer mechanics, and interpretability. The aim is to deepen the skill base that supports current AI work (QA chatbot, GraphRAG retrieval, evaluation methodology) and to grow into more advanced AI engineering work as defense applications increasingly require robustness and explainability.

## Timing

Phase 1: **May 18 – September 13, 2026** (17 weeks). Roughly 6 hours per workday week of focused learning, plus optional light review on Thursday evenings.

## Schedule

In-office Mon–Wed, WFH Thu:

| Day | Hours | Type | Best for |
|---|---|---|---|
| Mon | 1.5 | Fresh-week deep read | New conceptual material, dense papers |
| Tue | 1.5 | Continuation | Continue Mon's thread, work exercises |
| Wed | 1.5 | Implementation-flavored | Code-alongs, PyTorch drills, hands-on exercises |
| Thu (charged) | 1.5 | Paper reading | Retrieval-practice protocol on the week's paper |
| Thu evening (optional, light) | up to 1.5 | Review only | Anki review of week's vocab; re-watch unclear segments at 0.75× |

Fri/Sat/Sun are off; this plan does not cover those days.

## Reading Protocol (apply to every paper)

Based on retrieval-practice research (Roediger & Karpicke):

1. Read the paper in one sitting if possible
2. Close the paper
3. Write a one-paragraph summary in Obsidian *from memory*: question, method, result, limitation
4. Compare to the actual paper — note what you missed
5. Make 3–5 atomic Anki cards for key definitions or equations

Don't skip step 3. Re-reading without active retrieval feels productive but produces weak retention.

---

## Block 1 — Math Foundations (Weeks 1–4 | May 18 – June 14)

Goal: linear algebra fluency, calculus chain-rule comfort, probability basics, PyTorch tensor competence. Foundation for everything that follows.

### Week 1 (May 18–22)
- **Mon**: 3Blue1Brown "Essence of Linear Algebra" videos 1–4 + Obsidian notes
- **Tue**: *Mathematics for Machine Learning* (Deisenroth, Faisal, Ong) ch. 2 sections 2.1–2.4
- **Wed**: PyTorch `einsum` tutorial + practice exercises in scratch notebook
- **Thu (charged)**: 3B1B videos 5–8 + notes
- **Thu (optional)**: Anki review of linear algebra vocabulary

### Week 2 (May 25–29)
- **Mon**: 3B1B videos 9–12; MML ch. 2 sections 2.5–2.7
- **Tue**: MML ch. 2 exercises (paper first, then check)
- **Wed**: PyTorch tensor operations — `unsqueeze`, broadcasting, `reshape`, `view`
- **Thu (charged)**: 3B1B videos 13–16 + summary writeup
- **Thu (optional)**: Anki

### Week 3 (June 1–5)
- **Mon**: MML ch. 3 (Analytic Geometry) sections 3.1–3.4
- **Tue**: MML ch. 3 sections 3.5–3.7 + exercises
- **Wed**: NumPy/PyTorch hands-on — dot products, projections, norms
- **Thu (charged)**: MML ch. 4 (Matrix Decompositions) sections 4.1–4.3
- **Thu (optional)**: Anki

### Week 4 (June 8–12)
- **Mon**: MML ch. 4 sections 4.4–4.6 (eigendecomposition, SVD)
- **Tue**: MML ch. 4 exercises
- **Wed**: Hands-on SVD on small matrices in PyTorch
- **Thu (charged)**: MML ch. 5 (Vector Calculus) sections 5.1–5.3
- **Thu (optional)**: Anki

---

## Block 2 — ML Math + Transformer Foundations (Weeks 5–8 | June 15 – July 12)

Goal: backprop fluency, basic probability for ML, and a working mental model of the transformer architecture from the original paper plus Karpathy's walkthroughs.

### Week 5 (June 15–19)
- **Mon**: MML ch. 5 sections 5.4–5.6 (chain rule, automatic differentiation)
- **Tue**: Apply chain rule to a 2-layer neural network by hand
- **Wed**: Karpathy "micrograd" video — watch + notes (no public reproduction during workday)
- **Thu (charged)**: MML ch. 6 (Probability) sections 6.1–6.3
- **Thu (optional)**: Anki — derivatives, chain rule

### Week 6 (June 22–26)
- **Mon**: MML ch. 6 sections 6.4–6.7 (distributions, MLE, conjugacy)
- **Tue**: Derive cross-entropy from KL divergence on paper; state Bayes from memory
- **Wed**: PyTorch loss functions — what does `CrossEntropyLoss` actually compute? Verify on small examples.
- **Thu (charged)**: "The Bitter Lesson" (Sutton, 2019) — short read + retrieval summary
- **Thu (optional)**: Anki — probability vocabulary

### Week 7 (June 29 – July 3)
- **Mon**: Karpathy "Let's build GPT", part 1 — watch + notes
- **Tue**: Karpathy "Let's build GPT", part 2
- **Wed**: Karpathy "Let's build GPT", part 3 + finish notes
- **Thu (charged)**: "Attention Is All You Need" (Vaswani et al., 2017) — full retrieval-practice read
- **Thu (optional)**: Anki — attention vocabulary

### Week 8 (July 6–10)
- **Mon**: Karpathy "Let's reproduce GPT-2 (124M)", part 1 — watch + notes
- **Tue**: Karpathy GPT-2 video, part 2
- **Wed**: Karpathy GPT-2 video, part 3 + finish notes
- **Thu (charged)**: Re-read "Attention Is All You Need" with retrieval — what did you miss the first time?
- **Thu (optional)**: Anki — transformer architecture

---

## Block 3 — Mechanistic Understanding + Alternative Paradigms (Weeks 9–12 | July 13 – August 9)

Goal: understand how transformer internals are studied (relevant for debugging and explainability work), and read across paradigms to develop research taste. Defensive AI applications increasingly require explainability, which is what mechanistic interpretability research addresses.

### Week 9 (July 13–17)
- **Mon**: "A Mathematical Framework for Transformer Circuits" (Elhage et al., 2021), part 1
- **Tue**: Mathematical Framework, part 2
- **Wed**: Anthropic Transformer Circuits walkthrough video/post — visualize concepts from the paper
- **Thu (charged)**: Mathematical Framework, finish + retrieval summary
- **Thu (optional)**: Anki — residual stream, virtual weights, attention head decomposition

### Week 10 (July 20–24)
- **Mon**: "In-context Learning and Induction Heads" (Olsson et al., 2022), part 1
- **Tue**: Induction Heads, part 2 + retrieval summary
- **Wed**: "Toy Models of Superposition" (Elhage et al., 2022), part 1
- **Thu (charged)**: Toy Models of Superposition, part 2 + retrieval summary
- **Thu (optional)**: Anki — superposition concepts

### Week 11 (July 27–31)
- **Mon**: "A Path Towards Autonomous Machine Intelligence" (LeCun, 2022)
- **Tue**: "I-JEPA" (Assran et al., 2023)
- **Wed**: One LeCun talk or interview — note critiques of LLM paradigm
- **Thu (charged)**: "V-JEPA 2" (Ballas, Rabbat et al., 2025)
- **Thu (optional)**: Anki — world-model vocabulary

### Week 12 (August 3–7)
- **Mon**: "Coconut" (Hao et al., 2024) — Chain of Continuous Thought
- **Tue**: Write one-paragraph summary per paradigm: what it believes about intelligence, strongest argument, weakest point
- **Wed**: Review week — return to weakest-confidence Anki cards, re-derive any blurry math
- **Thu (charged)**: Catch-up on any incomplete papers
- **Thu (optional)**: Anki review

---

## Block 4 — ARENA Chapter 0 + Start of Chapter 1 (Weeks 13–17 | August 10 – September 13)

ARENA is a structured ML curriculum widely used for self-directed ML learning. Chapter 0 covers PyTorch fundamentals, einops, and optimization at depth; chapter 1 covers transformer mechanics and TransformerLens. Workday slots cover the conceptual content and locally-run exercises.

### Week 13 (August 10–14)
- **Mon**: ARENA ch. 0.0 prerequisites review
- **Tue**: ARENA ch. 0.1 (ray tracing) — conceptual content + small exercises
- **Wed**: Continue ch. 0.1
- **Thu (charged)**: ARENA ch. 0.2 (CNNs and ResNets) — conceptual
- **Thu (optional)**: Anki

### Week 14 (August 17–21)
- **Mon**: ARENA ch. 0.2 continued
- **Tue**: ARENA ch. 0.3 (optimization) — conceptual
- **Wed**: ARENA ch. 0.3 exercises (local)
- **Thu (charged)**: ARENA ch. 0.4 (backprop)
- **Thu (optional)**: Anki

### Week 15 (August 24–28)
- **Mon**: ARENA ch. 0.4 continued
- **Tue**: ARENA ch. 0.5 (GANs and VAEs) — conceptual
- **Wed**: ARENA ch. 0.5 small exercises
- **Thu (charged)**: Finish remaining ch. 0 sections
- **Thu (optional)**: Anki

### Week 16 (August 31 – September 4)
- **Mon**: ARENA ch. 1.1 (transformer from scratch) — conceptual
- **Tue**: ARENA ch. 1.1 continued
- **Wed**: ARENA ch. 1.2 (intro to mech interp) — conceptual
- **Thu (charged)**: ARENA ch. 1.2 continued
- **Thu (optional)**: Anki

### Week 17 (September 7–11)
- **Mon**: ARENA ch. 1.3 (TransformerLens basics) — conceptual
- **Tue**: ARENA ch. 1.3 continued
- **Wed**: Phase 1 review — re-read retrieval summaries, identify weak areas
- **Thu (charged)**: Catch-up + planning for Phase 2 (next 4-month block)
- **Thu (optional)**: Final phase-end Anki review

---

## Application to Current Work

This material connects to current AI work in several ways:

- **QA chatbot**: attention mechanics inform how retrieval+generation interact; eval methodology informs how to measure chatbot quality consistently
- **GraphRAG retrieval**: transformer mechanics inform reranking and embedding choices; understanding what models attend to helps tune retrieval
- **AI team competition**: foundational fluency is the baseline for model selection, prompt engineering, or fine-tuning work
- **Future AI projects**: defensive AI applications increasingly require explainability and robustness, which is exactly what interpretability research addresses

## Tools

- **Obsidian**: paper notes, retrieval-practice summaries, weekly review
- **Anki**: spaced repetition for vocabulary and key equations
- **PyTorch + uv**: hands-on exercises in scratch notebooks
- **GitHub (private)**: local exercise scratch directory

## Adjustment Notes

- Weeks may run 10–20% over or under estimate. Adjust forward without guilt; cut scope rather than extending phase.
- If a paper is taking 2× expected time, that's signal of a foundation gap. Pause, go back, fix the gap.
- Anki time on Thursday evening is **optional** — only do it on weeks where Thursday's earlier load was light. Don't push past tiredness; sleep beats cramming for retention.
