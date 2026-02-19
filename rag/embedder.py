"""
Embedding pipeline for the RAG knowledge base.

Wraps SentenceTransformer (all-MiniLM-L6-v2) to encode text chunks into
384-dimensional dense vectors, then upserts them to a Pinecone index with
metadata tags for filtered retrieval.

Usage
-----
    python -m rag.embedder          # one-shot index build
    from rag.embedder import index_documents
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from config.settings import settings

logger = logging.getLogger(__name__)

# Default scraped docs path
_DOCS_PATH = Path(__file__).resolve().parent / "data" / "scraped_docs.json"

# Embedding model config
_MODEL_NAME = "all-MiniLM-L6-v2"
_EMBEDDING_DIM = 384
_BATCH_SIZE = 64


# ──────────────────────────────────────────────────────────────────────────────
# Lazy-loaded model (avoid cold-start cost on import)
# ──────────────────────────────────────────────────────────────────────────────

_model = None


def _get_model():
    """Return a cached SentenceTransformer model instance."""
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer

        logger.info("Loading embedding model: %s", _MODEL_NAME)
        _model = SentenceTransformer(_MODEL_NAME)
    return _model


# ──────────────────────────────────────────────────────────────────────────────
# Pinecone client
# ──────────────────────────────────────────────────────────────────────────────


def _get_pinecone_index():
    """
    Return a Pinecone index, creating it if it doesn't exist.
    """
    from pinecone import Pinecone, ServerlessSpec

    pc = Pinecone(api_key=settings.PINECONE_API_KEY)
    index_name = settings.PINECONE_INDEX_NAME

    # Create index if it doesn't exist
    existing = [idx.name for idx in pc.list_indexes()]
    if index_name not in existing:
        logger.info("Creating Pinecone index: %s", index_name)
        pc.create_index(
            name=index_name,
            dimension=_EMBEDDING_DIM,
            metric="cosine",
            spec=ServerlessSpec(cloud="aws", region="us-east-1"),
        )

    return pc.Index(index_name)


# ──────────────────────────────────────────────────────────────────────────────
# Embedding + Upserting
# ──────────────────────────────────────────────────────────────────────────────


def _chunk_id(text: str) -> str:
    """Generate a deterministic ID for a text chunk."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def encode_texts(texts: list[str]) -> list[list[float]]:
    """Encode a list of texts into 384-dim dense vectors."""
    model = _get_model()
    embeddings = model.encode(texts, batch_size=_BATCH_SIZE, show_progress_bar=True)
    return embeddings.tolist()


def index_documents(docs: list[dict[str, Any]]) -> int:
    """
    Encode and upsert documents into the Pinecone index.

    Each document should have at minimum:
        ``{text: str, source: str, service: str}``

    Returns the number of vectors upserted.
    """
    if not docs:
        logger.warning("No documents to index")
        return 0

    index = _get_pinecone_index()
    total_upserted = 0

    # Process in batches
    for i in range(0, len(docs), _BATCH_SIZE):
        batch = docs[i : i + _BATCH_SIZE]
        texts = [doc["text"] for doc in batch]
        embeddings = encode_texts(texts)

        vectors = []
        for doc, embedding in zip(batch, embeddings):
            vec_id = _chunk_id(doc["text"])
            metadata = {
                "text": doc["text"][:1000],  # Pinecone metadata limit
                "source": doc.get("source", "unknown"),
                "service": doc.get("service", "General"),
                "category": doc.get("category", "general"),
            }
            if "url" in doc:
                metadata["url"] = doc["url"]

            vectors.append({"id": vec_id, "values": embedding, "metadata": metadata})

        index.upsert(vectors=vectors)
        total_upserted += len(vectors)
        logger.info("Upserted batch %d-%d (%d vectors)", i, i + len(batch), len(vectors))

    logger.info("Total vectors upserted: %d", total_upserted)
    return total_upserted


def load_and_index(docs_path: Path | None = None) -> int:
    """
    Load documents from JSON file and index them in Pinecone.

    Parameters
    ----------
    docs_path : Path, optional
        Path to the scraped documents JSON file.
        Defaults to ``rag/data/scraped_docs.json``.

    Returns
    -------
    int
        Number of vectors upserted.
    """
    path = docs_path or _DOCS_PATH

    if not path.exists():
        logger.error("Documents file not found: %s — run rag.scraper first", path)
        return 0

    with open(path, encoding="utf-8") as f:
        docs = json.load(f)

    logger.info("Loaded %d documents from %s", len(docs), path)
    return index_documents(docs)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    count = load_and_index()
    print(f"Indexing complete: {count} vectors upserted")
