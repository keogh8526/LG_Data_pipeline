"""Step 10 — FastAPI backend.

Exposes the BOM agent over HTTP. ``/api/validate`` is fully implemented
(deterministic). ``/api/draft`` and ``/api/upload`` depend on the local LLM and
loaded data; they return ``501`` until those are wired in — see DECISIONS D-003.
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from src.agent.tools import validate_bom

app = FastAPI(title="LG BOM Agent", version="0.1.0")


class DraftRequest(BaseModel):
    """Request body for ``/api/draft``."""

    change_point: str


class ValidateRequest(BaseModel):
    """Request body for ``/api/validate``."""

    bom: list[dict[str, object]]


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness probe."""
    return {"status": "ok"}


@app.post("/api/draft")
def draft(request: DraftRequest) -> dict[str, object]:
    """Generate a BOM / master draft from a change-point description.

    Args:
        request: The change-point text.

    Raises:
        HTTPException: 501 — deferred until the local LLM is wired in (D-003).
    """
    raise HTTPException(
        status_code=501, detail="draft endpoint deferred — local LLM required (D-003)"
    )


@app.post("/api/upload")
def upload() -> dict[str, object]:
    """Index a newly uploaded master file into Neo4j.

    Raises:
        HTTPException: 501 — deferred until the ingest pipeline is wired in.
    """
    raise HTTPException(
        status_code=501, detail="upload endpoint deferred — ingest pipeline (D-003)"
    )


@app.post("/api/validate")
def validate(request: ValidateRequest) -> dict[str, object]:
    """Validate a BOM against deterministic axioms.

    Args:
        request: The BOM rows to validate.

    Returns:
        The validation report.
    """
    return validate_bom(request.bom)
