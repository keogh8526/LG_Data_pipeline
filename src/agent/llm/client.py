"""로컬 LLM 클라이언트 (Ollama JSON 모드).

embedder.py(D-004)와 동일한 게이트 패턴: ``ENABLE_LLM=1`` 미설정 시 ``default_llm()``은
RuntimeError. import만으로 네트워크 호출 없음. 외부 API/키 금지 — Ollama 로컬만.

L1 structurizer의 의미 슬롯, L4의 자연어 설명에만 사용. L3 결정론 룰 엔진에서는 import 금지.
"""

from __future__ import annotations

import json
import os
from typing import Any, Protocol

import requests

from src.utils.logging import get_logger

log = get_logger(__name__)

DEFAULT_MODEL = "qwen2.5:32b"
HTTP_TIMEOUT = 120


def llm_enabled() -> bool:
    return os.environ.get("ENABLE_LLM", "0") == "1"


def _ollama_host() -> str:
    return os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")


def _safe_json(raw: str) -> dict[str, Any]:
    """LLM 응답 문자열 → dict. 잘못된 JSON / 비객체는 ValueError (스키마 강제 경계)."""
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError(f"LLM did not return valid JSON: {raw[:120]!r}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"LLM JSON is not an object: {type(data).__name__}")
    return data


class LlmClient(Protocol):
    """JSON 모드 로컬 LLM 인터페이스 (테스트는 fake 주입)."""

    def complete_json(
        self, prompt: str, *, system: str | None = None, temperature: float = 0.0
    ) -> dict[str, Any]: ...


class OllamaClient:
    """Ollama ``/api/generate`` (``format=json``) 백엔드."""

    def __init__(self, model: str | None = None) -> None:
        self._model = model or os.environ.get("LLM_MODEL", DEFAULT_MODEL)

    def complete_json(
        self, prompt: str, *, system: str | None = None, temperature: float = 0.0
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self._model,
            "prompt": prompt,
            "format": "json",
            "stream": False,
            "options": {"temperature": temperature},
        }
        if system:
            payload["system"] = system
        resp = requests.post(
            f"{_ollama_host()}/api/generate", json=payload, timeout=HTTP_TIMEOUT
        )
        resp.raise_for_status()
        return _safe_json(resp.json().get("response", ""))


def default_llm() -> LlmClient:
    """게이트된 기본 클라이언트. ``ENABLE_LLM=1`` 필요."""
    if not llm_enabled():
        raise RuntimeError(
            "LLM disabled. Set ENABLE_LLM=1 and run Ollama (qwen2.5:32b) to use the "
            "L1 semantic slots / L4 explanation."
        )
    return OllamaClient()
