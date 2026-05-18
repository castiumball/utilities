# Notes and Flashcards — Guidelines

## Purpose

A reference for how to take effective notes and make effective flashcards while learning ML papers, math, and ARENA content. Designed for the workday workflow: plaintext notes on the work machine, converted to Obsidian + Anki at home (the conversion is a deliberate second retrieval pass).

Also defines the format for pasting notes and flashcards into this project for review and feedback.

---

## Part 1 — Note-taking

### Core principles

1. **Synthesize, don't transcribe.** If you find yourself copying sentences from the paper, stop and close it. Notes are useful in proportion to how much your brain processed the content, not how completely you captured it. A bad note in your own words beats a perfect copy of the paper's words.

2. **Active reading over passive reading.** Before each section of a paper, predict what's coming. After each section, summarize what you just read without looking back. This converts reading from a recognition task to a production task, which is what builds durable memory.

3. **Retrieval before review.** The single biggest leverage move is writing your summary *before* looking back at the paper. The act of producing it from memory is what consolidates the learning. Re-reading feels productive but produces weak retention.

4. **Connection is content.** Notes that tie a new concept to something you already know (your QA chatbot, a previous paper, your Raytheon work) outlast notes that just describe the concept in isolation. Always ask: how does this connect to what I already know?

### Failure modes to watch for

- **Highlighting in place of thinking**: yellow ink on a paper does nothing for retention. The literature on this is unambiguous.
- **Note bloat**: 3000-word notes on a 4000-word paper. The compression ratio matters; you should be capturing the structure and key ideas, not the full text.
- **Skipping the from-memory step**: writing notes *while* reading the paper, instead of summarizing from memory after. This produces high-fidelity notes that don't stick.
- **Note-taking as procrastination**: spending 30 minutes formatting notes instead of 30 minutes engaging with the next concept. The notes are a tool, not the goal.

### Note format (plaintext, work-machine friendly)

Use this template for every paper. Adapt it for non-paper content (videos, book chapters, ARENA sections) by relaxing the structure.

```
# [Paper Title] — [Authors, Year]

Source: [URL or arXiv id]
Date read: [YYYY-MM-DD]
Phase block: [e.g., Block 2 / Week 7]

## Pre-read prediction (1-2 min, before reading)
[What do I think this paper does? What problem does it solve?
What would I expect the method to look like?]

## From-memory summary (write IMMEDIATELY after closing the paper)
Question: [what problem does this paper address?]
Method: [how do they approach it? what's the key technical move?]
Result: [what did they find? what's the main claim?]
Limitation: [what's the weakest part? what doesn't this paper address?]

## What I missed on second pass
[After re-opening the paper to check: what did I get wrong or leave out?
This section is the most valuable. It tells you what to study harder.]

## Key concepts → flashcard candidates
- [concept 1, with a brief reason it's worth remembering]
- [concept 2, ...]
- [concept 3, ...]

## Connections
[How does this connect to other papers I've read?
How does it connect to my current work (QA chatbot, GraphRAG, etc.)?
What would I do differently in my work after reading this?]

## Open questions
[Things I still don't understand or want to read more on later.]
```

### Note format for non-paper content

For videos (Karpathy, 3B1B), book chapters (MML), or ARENA sections, simplify:

```
# [Content Title] — [Source]

Date: [YYYY-MM-DD]

## Pre-watch / pre-read prediction
[Brief]

## Main ideas (from memory after finishing)
1. [idea 1 in own words]
2. [idea 2]
3. ...

## Confusions / gaps
[What didn't click. Where to dig deeper.]

## Flashcard candidates
- [concept 1]
- [concept 2]

## Connections
[How this links to other content this week / your work.]
```

---

## Part 2 — Flashcards

### Core principles

1. **Atomicity.** One fact per card. If a card has two facts on the back, split it into two cards. This isn't pedantic — non-atomic cards fail at retrieval because you remember half and feel uncertain about the rest, which weakens the memory trace.

2. **Recall, not recognition.** A good card requires you to *produce* the answer, not pick it from options. If the prompt is so detailed that the answer is obvious, the prompt is too detailed.

3. **Encode the way you want to retrieve.** Cards should test you in the form you'd actually use the knowledge. If you want to recognize an attention mechanism in code, make a card that shows code and asks what it computes. If you want to derive cross-entropy, make a card that asks you to derive it.

4. **Pruning beats hoarding.** A deck of 200 cards you review consistently is dramatically more valuable than a deck of 2000 cards you bail on. If a card hasn't stuck after 4–5 failed reviews, the card is bad — rewrite it or delete it.

5. **Card-worthiness test.** Before making a card, ask: would I want to know this cold six months from now? If the answer is no (because it's trivia, or because understanding the concept once is enough), don't make a card. Notes are sufficient for one-time knowledge; flashcards are for things you'll re-use.

### What makes a good card vs. a bad card

**Good cards:**
- Test a definition you'll keep referring to ("In a transformer, what is the residual stream?")
- Test a mechanism ("Why does scaled dot-product attention divide by √d_k?")
- Test a key equation ("Cross-entropy between distributions p and q is: ___")
- Test a design choice and its reason ("Why use LayerNorm before attention rather than after, in modern transformer variants?")
- Use cloze deletion for definitions and formulas ("The {{c1::softmax}} function maps a vector of real numbers to a probability distribution.")

**Bad cards:**
- Trivia ("What year was 'Attention Is All You Need' published?")
- Vague prompts ("What is attention?" — too broad, you'll never grade yourself fairly)
- Compound cards ("List the three matrices in self-attention" — should be three cards: what is Q, what is K, what is V)
- Cards that test your ability to recognize the answer rather than produce it
- Cards that depend on context not on the card itself ("What did the paper say about X?" — which paper? You won't remember in 6 months.)

### Flashcard format (plaintext, work-machine friendly)

For Q&A cards:
```
Q: [front]
A: [back]
Tags: [topic, source-paper, week]
```

For cloze cards:
```
Cloze: The {{c1::residual stream}} is the central object of mechanistic interpretability analysis in transformers.
Tags: [topic, source-paper, week]
```

Group related cards together by paper or concept, separated by blank lines. Example batch:

```
Q: In scaled dot-product attention, why divide by √d_k?
A: To prevent the dot product magnitude from growing with dimension, which would push softmax into saturated regions with vanishing gradients.
Tags: attention, vaswani-2017, week-7

Q: What does the residual stream refer to in mechanistic interpretability?
A: The running sum of all attention and MLP outputs at each token position; the central object that every component reads from and writes to.
Tags: residual-stream, math-framework-circuits, week-9

Cloze: An {{c1::induction head}} is a circuit of two attention heads that completes patterns of the form "...A B...A → B" by attending to the previous occurrence of A.
Tags: induction-heads, olsson-2022, week-10
```

### Cards per paper — rough guidance

For a heavy paper (Mathematical Framework, Attention Is All You Need): 6–12 cards.
For a lighter paper (The Bitter Lesson, a one-idea blog post): 1–3 cards.
For a video (Karpathy "Let's build GPT"): 5–10 cards spread over the parts.
For an MML chapter: 4–8 cards on key definitions and theorems.

If you're making 20+ cards per paper, you're over-carding. Prune.

---

## Part 3 — Pasting into this project for feedback

When you want feedback on notes, paste the full note for one paper or content piece. The pre-read prediction, from-memory summary, and "what I missed" sections are the most useful for feedback — they let me see your mental model and where it's drifting from the paper.

When you want feedback on flashcards, paste a batch of 5–15 cards (any mix of Q&A and cloze) together. Smaller batches let me give more granular feedback per card; larger batches are better for spotting patterns across cards.

### What feedback I can give on notes

- Whether your from-memory summary captured the actual core argument of the paper
- Whether the "limitation" you identified matches what the paper itself or follow-up work identifies
- Connection gaps — things this paper links to that you didn't surface
- Confusions you flagged that might have a clear answer
- Where your mental model might be subtly wrong in ways that will compound

### What feedback I can give on flashcards

- Atomicity (is this really one fact?)
- Vagueness (could you grade yourself fairly on this prompt?)
- Trivia detection (is this worth long-term recall?)
- Missing context (will this card make sense in 6 months without the paper?)
- Better phrasings of weak prompts
- Card-vs-note miscategorization (some concepts belong in notes, not cards)

### What feedback I can't reliably give

- Whether your overall pacing is right for the phase (you'll see this in your weekly review)
- Whether a card is the *most important* one to make from a given paper (this is judgment that improves with practice)
- Specific page references in textbooks you have but I don't

---

## Quick reference

Before reading a paper:
- Predict in 1–2 sentences what it does.

After reading:
- Close the paper.
- Write Question / Method / Result / Limitation from memory.
- Re-open. Note what you missed.
- List flashcard candidates.
- Write connections to other work.

Before making a card:
- Ask: do I want to know this cold in 6 months?
- Is the prompt specific enough to grade yourself fairly?
- Does the answer fit in one breath?
- If no to any: rewrite or skip.

At end of week:
- Convert plaintext notes to Obsidian (one note per source).
- Convert plaintext flashcards to Anki (one deck for ML Foundations to start).
- Note the act of conversion is itself a retrieval pass — engage with it actively, don't just paste.
