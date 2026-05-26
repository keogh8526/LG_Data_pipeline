"""v2.0 Step 6 — bge-m3 임베딩 (Ollama 우선, sentence-transformers fallback).

preprocessing_v2.md §7. 자유텍스트만 임베딩 — 전체 테이블 임베딩 금지.
``ENABLE_EMBEDDING=1`` 게이트 — 본 모듈 import만으로는 모델 다운로드/네트워크
호출 발생하지 않음 (D-004).
"""

from __future__ import annotations

import os
import time
from functools import lru_cache

from src.utils.logging import get_logger

log = get_logger(__name__)

DEFAULT_MODEL = "bge-m3"
EMBEDDING_DIM = 1024
DEFAULT_BATCH = 64
FALLBACK_BATCH = 16


def embedding_enabled() -> bool:
    return os.environ.get("ENABLE_EMBEDDING", "0") == "1"


def _embed_ollama(texts: list[str], model: str, batch: int = DEFAULT_BATCH) -> list[list[float]]:
    """Ollama 로컬 호출. batch 단위, 실패 시 exponential backoff."""
    import ollama

    host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    client = ollama.Client(host=host)

    out: list[list[float]] = []
    for start in range(0, len(texts), batch):
        chunk = texts[start : start + batch]
        for attempt in range(3):
            try:
                for t in chunk:
                    if not t:
                        out.append([0.0] * EMBEDDING_DIM)
                        continue
                    resp = client.embeddings(model=model, prompt=t)
                    out.append(resp["embedding"])
                break
            except Exception as exc:  # noqa: BLE001
                log.warning("embed.ollama_retry", attempt=attempt, error=str(exc))
                time.sleep(2**attempt)
        else:
            raise RuntimeError("Ollama embedding failed after retries")
    return out


@lru_cache(maxsize=1)
def _load_st_model(model_name: str) -> object:
    """sentence-transformers fallback (~2GB download). lazy."""
    from sentence_transformers import SentenceTransformer

    log.info("embed.load_st_model", model=model_name)
    return SentenceTransformer(model_name)


def _embed_st(texts: list[str], model_name: str) -> list[list[float]]:
    model = _load_st_model(model_name)
    return model.encode(texts, normalize_embeddings=True).tolist()  # type: ignore[attr-defined]


def embed_texts(texts: list[str], model: str | None = None) -> list[list[float]]:
    """텍스트 리스트 → 임베딩 리스트. Ollama 우선, 실패 시 ST fallback.

    Raises:
        RuntimeError: ENABLE_EMBEDDING != 1.
    """
    if not embedding_enabled():
        raise RuntimeError(
            "Embedding disabled. Set ENABLE_EMBEDDING=1 and run Ollama "
            "(or install 'embed' extra) to embed text."
        )
    if not texts:
        return []
    name = model or os.environ.get("EMBED_MODEL", DEFAULT_MODEL)
    try:
        return _embed_ollama(texts, name)
    except Exception as exc:  # noqa: BLE001
        log.warning("embed.ollama_failed", error=str(exc))
        st_model = os.environ.get("EMBED_ST_MODEL", "BAAI/bge-m3")
        return _embed_st(texts, st_model)
