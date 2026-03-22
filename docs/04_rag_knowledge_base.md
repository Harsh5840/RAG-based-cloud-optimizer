# 04 - RAG Knowledge Retrieval System

To generate good infrastructure code, Claude needs exact structural context. That's why the `rag/` directory implements a Vector Database search capability utilizing **Pinecone**.

## How the Retrieval Happens

In `rag/optimization_rag.py`, we execute `retrieve_context()`, which takes the identified `Anomaly` dataclass as an input.

1. **Text Serialization:**
   `build_query(anomaly)` translates the raw dataclass into a semantic NLP text block.
   *Example: "EC2 idle resource optimization cpu utilization 1.2% instance m5.xlarge cost reduction"*

2. **Vectorization:**
   We encode the query using `SentenceTransformers`. Specifically, the system utilizes the `all-MiniLM-L6-v2` dense embedding model (`embedder.py`), turning our search query into a 384-dimensional vector float array.

3. **Pinecone Search:**
   Using the active `Pinecone` client, we fire our vector off, searching for the nearest `top_k=5` cosine neighbor documents.
   To optimize accuracy and cut context noise, we use `metadata_filter` to only pull data tagged to exactly that Cloud `service`. 

4. **Formatting:**
   The chunks are assembled via `.join()` strings and sent back as raw context blocks (Source + Relevance + Text).

## Fallback Design

If Pinecone is offline or lacks the API key, the script gracefully resolves `Exception` errors by throwing `_fallback_context(anomaly)`, which manually injects basic hard-coded best practices (e.g. AWS Well-Architected Framework tips for S3/EC2).
