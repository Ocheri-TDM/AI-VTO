# Semantic search and evaluation

Production retrieval combines PostgreSQL Russian lexical search, open taxonomy evidence, structured constraints and deterministic semantic preferences. `PREFERENCE_TERMS` produces RULE_BASED tags from supplier facts. It does not turn inferred style into a supplier fact. Bounded `semantic_document` contains at most 2400 characters of normalized meaningful metadata, not raw HTML.

Hard examples: `только металлические`, `до 5000`, `от 5000`, category exclusions and quantity. Soft examples: `желательно металлические`, `желательно недорогие`, premium, minimal, technology, eco, business and creative preferences. Ranking retains the entire eligible pool. Similarity currently combines category, color, lexical facts and price proximity; references must exist in the session.

Qwen3 4B remains available for unrecognized refinements via the existing local model abstraction. Common commands bypass generation. Typed decisions restrict side effects, product IDs are validated, explicit refresh/continuation are guarded. Ordinals refer to the server's ordered current view. The `по N` selection command selects independently per category.

## Local embedding experiment

`scripts/eval_final_semantics.py` downloads no supplier data and evaluates real local Ollama `embeddinggemma` vectors against a labelled synthetic merchandise set. Model: 768 dimensions; 12 documents; six query families; query/document prefixes from the model conventions. The artifact is `.local/final/semantic-eval.json`.

On this small set, macro precision and recall at the number of labelled positives were **0.917 for rules versus 0.667 for embeddings**. The artifact lists obvious false positives for every query. Embeddings improved premium results but regressed eco, technology and unusual/creative gifts. Hard-constraint regressions reported zero violations because semantic ordering receives only the eligible pool. Initial batch including loading took about 41.4 seconds. This is not a general multilingual benchmark and does not establish broad semantic quality.

Given the conditional requirement to adopt embeddings only when evaluation demonstrates practical benefit, this candidate was **not enabled in production**. No pgvector migration or background embedding pipeline is claimed. The existing `EmbeddingProvider` boundary remains. There is no paid API or per-product Qwen pairwise scoring.

References: [EmbeddingGemma model overview](https://ai.google.dev/gemma/docs/embeddinggemma), [Ollama model](https://ollama.com/library/embeddinggemma), [multilingual E5 alternative](https://huggingface.co/intfloat/multilingual-e5-small). Only EmbeddingGemma was run locally in this evaluation; E5 is not presented as tested.

The semantic fixtures are deliberately small. Style scoring is explainable but cannot reliably interpret every subjective aesthetic request. This limitation must remain visible in the final assessment.
