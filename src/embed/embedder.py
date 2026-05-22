"""Step 7 — text embedding (lazy-loaded).

Only free-text fields are embedded — never whole tables. The embedding model
(~2GB) loads lazily and only when ``ENABLE_EMBEDDING=1``, so importing this
module never triggers a multi-GB download.
"""

from __future__ import annotations

import os
from functools import lru_cache

from src.utils.logging import get_logger

log = get_logger(__name__)

DEFAULT_MODEL = "BAAI/bge-m3"

# Free-text fields eligible for embedding (DB column references).
EMBEDDABLE_FIELDS: tuple[str, ...] = (
    "change_events.change_point",
    "change_events.change_reason",
    "parts.description",
    "parts.technical_spec",
)


def embedding_enabled() -> bool:
    """Return True if runtime embedding is enabled via ``ENABLE_EMBEDDING``."""
    return os.environ.get("ENABLE_EMBEDDING", "0") == "1"


@lru_cache(maxsize=1)
def _load_model(model_name: str) -> object:
    """Load and cache the sentence-transformer model (lazy import).

    Args:
        model_name: HuggingFace model id.

    Returns:
        A ``SentenceTransformer`` instance.
    """
    from sentence_transformers import SentenceTransformer

    log.info("embed.load_model", model=model_name)
    return SentenceTransformer(model_name)


def embed_texts(texts: list[str], model_name: str | None = None) -> list[list[float]]:
    """Embed a list of free-text strings.

    Args:
        texts: Text strings to embed.
        model_name: Optional model override; defaults to ``EMBED_MODEL`` env
            or :data:`DEFAULT_MODEL`.

    Returns:
        One dense vector per input text.

    Raises:
        RuntimeError: If embedding is not enabled (``ENABLE_EMBEDDING != 1``).
    """
    if not embedding_enabled():
        raise RuntimeError(
            "Embedding is disabled. Set ENABLE_EMBEDDING=1 and install the "
            "'embed' extra to load the model."
        )
    name = model_name or os.environ.get("EMBED_MODEL", DEFAULT_MODEL)
    model = _load_model(name)
    return model.encode(texts, normalize_embeddings=True).tolist()  # type: ignore[attr-defined]
