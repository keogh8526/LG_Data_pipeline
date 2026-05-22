"""Step 7 — text embedding (Ollama primary, sentence-transformers fallback).

Only free-text fields are embedded — never whole tables. Embedding is gated on
``ENABLE_EMBEDDING`` so importing this module never triggers a model download
or a network call.
"""

from __future__ import annotations

import os
from functools import lru_cache

from src.utils.logging import get_logger

log = get_logger(__name__)

DEFAULT_MODEL = "bge-m3"
EMBEDDING_DIM = 1024

# Free-text fields eligible for embedding (graph property references).
EMBEDDABLE_FIELDS: tuple[str, ...] = (
    "ChangeEvent.change_point",
    "ChangeEvent.change_reason",
    "Part.description",
)


def embedding_enabled() -> bool:
    """Return True if runtime embedding is enabled via ``ENABLE_EMBEDDING``."""
    return os.environ.get("ENABLE_EMBEDDING", "0") == "1"


def _embed_ollama(texts: list[str], model: str) -> list[list[float]]:
    """Embed texts via a local Ollama server.

    Args:
        texts: Text strings to embed.
        model: Ollama model name.

    Returns:
        One dense vector per input text.
    """
    import ollama

    host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    client = ollama.Client(host=host)
    return [client.embeddings(model=model, prompt=t)["embedding"] for t in texts]


@lru_cache(maxsize=1)
def _load_st_model(model_name: str) -> object:
    """Load and cache a sentence-transformers fallback model.

    Args:
        model_name: HuggingFace model id.

    Returns:
        A ``SentenceTransformer`` instance.
    """
    from sentence_transformers import SentenceTransformer

    log.info("embed.load_st_model", model=model_name)
    return SentenceTransformer(model_name)


def _embed_sentence_transformers(
    texts: list[str], model_name: str
) -> list[list[float]]:
    """Embed texts via the sentence-transformers fallback.

    Args:
        texts: Text strings to embed.
        model_name: HuggingFace model id (e.g. ``BAAI/bge-m3``).

    Returns:
        One dense vector per input text.
    """
    model = _load_st_model(model_name)
    return model.encode(texts, normalize_embeddings=True).tolist()  # type: ignore[attr-defined]


def embed_texts(texts: list[str], model: str | None = None) -> list[list[float]]:
    """Embed free-text strings, preferring Ollama and falling back locally.

    Args:
        texts: Text strings to embed.
        model: Optional model override; defaults to ``EMBED_MODEL`` env or
            :data:`DEFAULT_MODEL`.

    Returns:
        One dense vector per input text.

    Raises:
        RuntimeError: If embedding is disabled (``ENABLE_EMBEDDING != 1``).
    """
    if not embedding_enabled():
        raise RuntimeError(
            "Embedding is disabled. Set ENABLE_EMBEDDING=1 and run Ollama "
            "(or install the 'embed' extra) to embed text."
        )
    name = model or os.environ.get("EMBED_MODEL", DEFAULT_MODEL)
    try:
        return _embed_ollama(texts, name)
    except Exception as exc:  # noqa: BLE001 — fall back to local model
        log.warning("embed.ollama_failed", error=str(exc))
        st_model = os.environ.get("EMBED_ST_MODEL", "BAAI/bge-m3")
        return _embed_sentence_transformers(texts, st_model)
